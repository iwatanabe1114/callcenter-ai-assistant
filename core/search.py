"""検索の仕組み（設計書 4.2 / 4.3 / 3.1）。

キーワード検索（文字の一致を見る方式）を BM25 で実装しています。

トークン化には形態素解析（fugashi / MeCab）を使い、
「名詞」「動詞」「形容詞」など検索に有効な品詞のみを抽出します。
fugashi が利用できない環境では bi-gram にフォールバックします。

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
_NON_WORD = re.compile(r"[\s　\-–—_/／\\|｜･・、。，．,.\(\)（）\[\]「」『』【】<>＜＞:：;；!！?？\"''""*＊+＋=＝~〜^％%#＃&＆@＠$￥¥]+")
# 英数字のかたまり（そのまま1トークンとして扱う）
_ASCII_WORD = re.compile(r"[a-z0-9]{2,}")

# ── 形態素解析の初期化（グローバルで1回だけ） ──────────────
# fugashi (MeCab) を使って単語分割する。
# インストールされていない場合は bi-gram にフォールバックする。
_tagger = None
_USE_MORPHO = False

try:
    import fugashi
    _tagger = fugashi.Tagger()
    _USE_MORPHO = True
except ImportError:
    pass

# 商品名などの固有名詞（形態素解析で分割されるのを防ぐ）
# テキストに出現したらトークン化の前にまとめて1語に置き換える
_BRAND_NAMES = [
    "メグレアpremium", "メグレアlight", "メグレア",
    "爽軽青汁", "糖貫プロネス", "肝匠プロネス",
    "アユミルpremium", "アユミル",
    "はつらつコラーゲン", "アイゼン",
    "すっぽん黒酢", "lipo clear vitamin c",
    "マイページ", "リピートプラス",
    "定期便", "回数便", "通常便",
]
# 長いものから順にマッチさせる（「メグレアpremium」を先に処理）
_BRAND_NAMES.sort(key=len, reverse=True)

# 検索に有効な品詞（これ以外は捨てる）
_CONTENT_POS = {"名詞", "動詞", "形容詞", "副詞"}

# 名詞のうち検索に不要なサブカテゴリ
_SKIP_POS2 = {"助数詞", "非自立可能", "接尾"}

# ストップワード（どの資料にも出てくる一般的すぎる語）
_STOP_WORDS = frozenset({
    "する", "いる", "ある", "なる", "できる", "れる", "られる",
    "こと", "もの", "ため", "ところ", "よう", "ほう",
    "これ", "それ", "あれ", "ここ", "そこ",
    "この", "その", "あの",
    "何", "誰", "どこ", "いつ", "なぜ",
    "場合", "方", "方法", "件", "点", "時",
    "等", "的", "上", "下", "中", "内", "外",
    "お客様", "お客さん", "オペレーター",
})

# 質問の「言い回し」。何を聞きたいかとは無関係なので、検索する前に取り除く。
_FRAMING_PHRASES = [
    "と言われました", "と言われている", "と言われてる", "と言われた", "と言われて",
    "と聞かれました", "と聞かれた", "と聞かれて",
    "と言っています", "と言っている", "と言ってる", "と言った",
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


# ── トークン化（形態素解析 or bi-gram フォールバック） ──────

def _extract_brands(text: str) -> tuple[str, list[str]]:
    """テキストから商品名などの固有名詞を先に抽出し、残りのテキストを返す。"""
    found: list[str] = []
    for brand in _BRAND_NAMES:
        brand_norm = normalize(brand)
        if brand_norm in text:
            found.append(brand_norm)
            text = text.replace(brand_norm, " ")
    return text, found


def _tokenize_morpho(text: str) -> list[str]:
    """形態素解析でトークン化。名詞・動詞・形容詞を原形（表層形優先）で抽出する。"""
    normalized = normalize(text)

    # 固有名詞を先に抽出（形態素解析で分割されるのを防ぐ）
    remaining, brand_tokens = _extract_brands(normalized)
    tokens: list[str] = list(brand_tokens)
    for word in _tagger(remaining):  # type: ignore[misc]
        pos1 = word.feature.pos1 or ""
        pos2 = word.feature.pos2 or ""
        surface = word.surface

        # 検索に有効な品詞だけを残す
        if pos1 not in _CONTENT_POS:
            continue

        # 名詞のうち不要なサブカテゴリをスキップ
        if pos1 == "名詞" and pos2 in _SKIP_POS2:
            continue

        # 名詞は表層形をそのまま使う（固有名詞の分割を防ぐ）
        # 動詞・形容詞は原形を使う（「解約します」→「解約」+「する」）
        if pos1 == "名詞":
            token = surface
        else:
            lemma = word.feature.lemma
            token = str(lemma) if lemma and str(lemma) != "None" else surface
            # 原形に「-」が含まれる場合は表層形を使う（「メグ-Meg」対策）
            if "-" in token:
                token = surface

        token = token.strip()
        if not token:
            continue

        # ストップワードを除外
        if token in _STOP_WORDS:
            continue

        # 1文字のひらがな・カタカナは除外（「し」「て」など取りこぼし対策）
        if len(token) == 1 and re.match(r"[ぁ-んァ-ヶー]", token):
            continue

        tokens.append(token)

    # 英数字の単語も拾う（形態素解析が英数字を細切れにする場合の補完）
    for word in _ASCII_WORD.findall(remaining):
        if word not in tokens:
            tokens.append(word)

    return tokens


def _tokenize_bigram(text: str) -> list[str]:
    """bi-gram によるトークン化（フォールバック用）。"""
    normalized = normalize(text)
    tokens: list[str] = []

    for chunk in _NON_WORD.split(normalized):
        if not chunk:
            continue
        for word in _ASCII_WORD.findall(chunk):
            tokens.append(word)
        japanese = _ASCII_WORD.sub(" ", chunk)
        for part in japanese.split():
            if len(part) == 1:
                tokens.append(part)
                continue
            for i in range(len(part) - 1):
                tokens.append(part[i : i + 2])
    return tokens


def tokenize(text: str) -> list[str]:
    """テキストをトークン化する。

    形態素解析（fugashi）が利用可能ならそちらを使い、
    インストールされていなければ bi-gram にフォールバックする。
    """
    if _USE_MORPHO:
        return _tokenize_morpho(text)
    return _tokenize_bigram(text)


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

        # 検索語をトークン化し、重みを引き継ぐ
        token_weight: dict[str, float] = {}
        token_origin: dict[str, str] = {}
        for term, weight in weighted_terms.items():
            for token in tokenize(term):
                if weight > token_weight.get(token, 0.0):
                    token_weight[token] = weight
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
    """質問文を、検索語として使えるかたまりに分ける（元の質問側）。

    形態素解析が利用可能な場合は単語単位で分割し、
    そうでなければ記号区切りで分割する。
    """
    stripped = strip_framing(question)
    if _USE_MORPHO:
        tokens = tokenize(stripped)
        return tokens if tokens else [normalize(stripped)]
    normalized = normalize(stripped)
    parts = [p for p in _NON_WORD.split(normalized) if p]
    return parts or [normalized]
