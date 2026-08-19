"""検索の仕組み（設計書 4.2 / 4.3 / 3.1）。

キーワード検索（文字の一致を見る方式）を BM25 で実装しています。
日本語は英語のように単語と単語の間にスペースがないため、
2文字ずつの組み合わせ（bi-gram）で一致を調べる、日本語向けの工夫を入れています。
追加のAI・追加費用が不要で、速いのが利点です。

「意味で探す検索」（ベクトル検索）は今は使っていません。
あとから足せるように、スコア計算は search() の中に閉じてあります。

重要（4.3）:
    クエリ拡張でAIが追加した言葉を、元の質問の言葉と同じ重要度で扱うと、
    追加された言葉が一般的すぎて（「対応」「手続き」など）ほとんど全部の
    資料にヒットしてしまい、かえって精度が落ちます。
    そのため term -> weight の形で重みを分けて渡す設計にしています。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .config import GENRE_PRIORITY

# BM25 のパラメータ
_K1 = 1.2
_B = 0.75

# 記号・空白を落とすための正規表現
_NON_WORD = re.compile(r"[\s　\-–—_/／\\|｜･・、。，．,.\(\)（）\[\]「」『』【】<>＜＞:：;；!！?？\"'’”“*＊+＋=＝~〜^％%#＃&＆@＠$￥¥]+")
# 英数字のかたまり（そのまま1トークンとして扱う）
_ASCII_WORD = re.compile(r"[a-z0-9]{2,}")
# ひらがなだけでできた2文字（「した」「たい」「です」など）
_HIRAGANA_ONLY = re.compile(r"^[ぁ-んー]+$")

# ひらがなだけのトークンの重み。
# 日本語の質問文には「〜したいと言われた」のような言い回しが多く含まれます。
# そこから作られる「した」「たい」「いと」といった2文字は、意味を持つ
# 「返品」「解約」と同じ重さで扱うと、関係のない資料にまで大量に一致して
# 検索の精度を下げます。そこで内容語より軽く扱います。
# （4.3 の「一般的すぎる語を同じ重要度で扱わない」と同じ考え方です）
_FUNCTION_TOKEN_WEIGHT = 0.2


# 質問の「言い回し」。何を聞きたいかとは無関係なので、検索する前に取り除く。
#
# なぜ必要か：
#   オペレーターは「返品したいと言われた」のように話し言葉で入力します。
#   このうち「と言われた」は、2文字ずつに分けると「と言」「言わ」になります。
#   これらは漢字を含むため内容語として扱われ、しかも資料の中では珍しいので
#   重要度が高く計算されます。その結果、お客様の声に「〜と言われた」と
#   書かれているだけの無関係な資料が、本命の「返品」の資料より上に来ます。
#   実データ705件で実際に起きた現象です。
#
# 長いものから順に消します（「と言われました」を先に消さないと
# 「と言われ」だけが消えて「ました」が残る）。
_FRAMING_PHRASES = [
    "と言われました", "と言われている", "と言われてる", "と言われた", "と言われて",
    "と聞かれました", "と聞かれた", "と聞かれて",
    "と言っています", "と言っている", "と言ってる", "と言っেた",
    "とのことです", "と相談されました", "と相談された",
    "どうすればいいですか", "どうすればいい", "どうしたらいいですか", "どうしたらいい",
    "教えてください", "教えて欲しい", "教えてほしい",
    "お客様から", "お客さんから", "お客様に", "でしょうか", "ですか",
]


def strip_framing(text: str) -> str:
    """質問文から言い回しを取り除く。全部消えてしまう場合は元の文を返す。"""
    stripped = text
    for phrase in _FRAMING_PHRASES:
        stripped = stripped.replace(phrase, " ")
    return stripped if stripped.strip() else text


def normalize(text: str) -> str:
    """全角/半角・大文字小文字のゆらぎを吸収する。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str) -> list[str]:
    """日本語向けのトークン化。

    ・英数字の単語はそのまま1トークン
    ・それ以外（日本語）は2文字ずつの組み合わせ（bi-gram）
      1文字だけの塊は、その1文字をトークンにする
    """
    normalized = normalize(text)
    tokens: list[str] = []

    for chunk in _NON_WORD.split(normalized):
        if not chunk:
            continue
        # 英数字の単語を先に抜き出す
        for word in _ASCII_WORD.findall(chunk):
            tokens.append(word)
        # 日本語部分は bi-gram
        japanese = _ASCII_WORD.sub(" ", chunk)
        for part in japanese.split():
            if len(part) == 1:
                tokens.append(part)
                continue
            for i in range(len(part) - 1):
                tokens.append(part[i : i + 2])
    return tokens


@dataclass
class Hit:
    doc: dict[str, Any]
    score: float
    matched: list[str]

    @property
    def id(self) -> str:
        return str(self.doc.get("id", ""))

    @property
    def title(self) -> str:
        return str(self.doc.get("title", ""))

    @property
    def genre(self) -> str:
        return str(self.doc.get("genre", ""))

    @property
    def source_label(self) -> str:
        return str(self.doc.get("source_label", ""))

    @property
    def source_url(self) -> str:
        return str(self.doc.get("source_url", ""))

    @property
    def text(self) -> str:
        return f"{self.doc.get('title','')}\n{self.doc.get('body','')}".strip()


class SearchIndex:
    """BM25 の索引。作るのに少し時間がかかるので使い回します（6.2 遅延読み込み）。"""

    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs
        self._doc_tokens: list[Counter[str]] = []
        self._lengths: list[int] = []
        self._df: Counter[str] = Counter()

        for doc in docs:
            # タイトルは重要なので2回数える
            text = f"{doc.get('title','')} {doc.get('title','')} {doc.get('body','')}"
            counts = Counter(tokenize(text))
            self._doc_tokens.append(counts)
            self._lengths.append(sum(counts.values()) or 1)
            for token in counts:
                self._df[token] += 1

        self.n = len(docs)
        self.avg_len = (sum(self._lengths) / self.n) if self.n else 1.0

    def _idf(self, token: str) -> float:
        df = self._df.get(token, 0)
        if df == 0:
            return 0.0
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(
        self,
        weighted_terms: dict[str, float],
        *,
        top_k: int = 6,
        genres: Iterable[str] | None = None,
        use_genre_priority: bool = True,
        min_score: float = 0.0,
    ) -> list[Hit]:
        """重み付きの言葉で検索する。

        weighted_terms: {"返品": 1.0, "返送": 0.35} のように、
                        元の質問の言葉は高く、追加した言葉は低く渡す（4.3）。
        """
        if not self.n or not weighted_terms:
            return []

        allowed = set(genres) if genres is not None else None

        # 言葉 → bi-gram トークンへ展開し、重みを引き継ぐ。
        # そのうえで、ひらがなだけのトークン（言い回し由来）は軽くする。
        token_weight: dict[str, float] = {}
        token_origin: dict[str, str] = {}
        for term, weight in weighted_terms.items():
            for token in tokenize(term):
                effective = weight * (
                    _FUNCTION_TOKEN_WEIGHT if _HIRAGANA_ONLY.match(token) else 1.0
                )
                if effective > token_weight.get(token, 0.0):
                    token_weight[token] = effective
                    token_origin[token] = term

        hits: list[Hit] = []
        for idx, doc in enumerate(self.docs):
            if allowed is not None and doc.get("genre") not in allowed:
                continue

            counts = self._doc_tokens[idx]
            length = self._lengths[idx]
            score = 0.0
            matched_terms: set[str] = set()

            for token, weight in token_weight.items():
                tf = counts.get(token, 0)
                if not tf:
                    continue
                idf = self._idf(token)
                if idf <= 0:
                    continue
                denom = tf + _K1 * (1 - _B + _B * length / self.avg_len)
                score += weight * idf * (tf * (_K1 + 1) / denom)
                matched_terms.add(token_origin.get(token, token))

            if score <= 0:
                continue

            # 3.1 ジャンルごとの優先順位（キャンペーン > マニュアル > 料金 > 商品）
            if use_genre_priority:
                score *= GENRE_PRIORITY.get(str(doc.get("genre")), 1.0)

            hits.append(Hit(doc=doc, score=score, matched=sorted(matched_terms)))

        hits.sort(key=lambda h: h.score, reverse=True)
        hits = [h for h in hits if h.score >= min_score]
        return hits[:top_k]


def build_index(docs: list[dict[str, Any]]) -> SearchIndex:
    return SearchIndex(docs)


def weighted_terms(
    original: list[str],
    expanded: list[str],
    *,
    original_weight: float = 1.0,
    expanded_weight: float = 0.35,
) -> dict[str, float]:
    """元の質問の言葉と、AIが追加した言葉を、重みを分けてまとめる（4.3）。"""
    terms: dict[str, float] = {}
    for term in original:
        term = term.strip()
        if term:
            terms[term] = original_weight
    for term in expanded:
        term = term.strip()
        # 元の質問に既にある言葉は高い重みのまま
        if term and term not in terms:
            terms[term] = expanded_weight
    return terms


def split_query(question: str) -> list[str]:
    """質問文を、検索語として使えるかたまりに分ける（元の質問側）。"""
    normalized = normalize(strip_framing(question))
    parts = [p for p in _NON_WORD.split(normalized) if p]
    # 質問文全体も1つの語として入れておく（長い一致に強くするため）
    return parts or [normalized]
