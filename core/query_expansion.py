"""質問の言葉を広げてあげる＝クエリ拡張（設計書 4.3）。

オペレーターさんが打ち込んだ質問の言葉そのままだと、資料側の表現と微妙に違って
いて見つからないことがあります（例：「返品」と「返送」）。
そこで、応答が速く・費用の安い軽量なAI（Haiku）で、質問に近い意味の言葉を
自動で追加してから検索します。

追加した言葉は search.weighted_terms() で低い重みを付けて扱います。
元の質問の言葉と同じ重要度で扱うと、「対応」「手続き」のような一般的すぎる語が
ほとんど全部の資料にヒットして、かえって精度が落ちるためです。
"""

from __future__ import annotations

from typing import Any

from .config import AppConfig
from .llm import structured_call

EXPANSION_SYSTEM = """あなたは日本語の社内文書検索を助けるアシスタントです。
オペレーターの質問文を受け取り、社内資料側で使われていそうな言い換え・関連語を挙げてください。

ルール：
- 5〜8語。名詞または短い名詞句のみ。文章にしないこと。
- 「対応」「手続き」「方法」「確認」のような、どんな資料にも出てくる一般的すぎる語は
  含めないでください。検索の精度が落ちます。
- 質問文にすでに出ている語は含めないでください。
- 業務でよく使われる言い換え（例：返品→返送、解約→退会・停止）を優先してください。
"""

EXPANSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "追加する検索語（5〜8語）",
        }
    },
    "required": ["terms"],
    "additionalProperties": False,
}

# 一般的すぎて検索の役に立たない語（AIが出してきても捨てる）
_STOP_TERMS = {
    "対応", "手続き", "方法", "確認", "case", "ケース", "内容", "情報", "資料",
    "お客様", "顧客", "質問", "回答", "説明", "案内", "件", "こと", "もの",
}


def expand_query(config: AppConfig, question: str, *, max_terms: int = 8) -> list[str]:
    """追加の検索語を返す。失敗したら空リスト（拡張なしで検索を続ける）。"""
    if not question.strip() or not config.has_llm:
        return []

    result = structured_call(
        config,
        model=config.expansion_model,
        system=EXPANSION_SYSTEM,
        user_content=f"質問文: {question}",
        schema=EXPANSION_SCHEMA,
        max_tokens=512,
    )
    if not result.ok:
        # 拡張は「あれば嬉しい」機能なので、失敗しても検索自体は続ける
        return []

    terms: list[str] = []
    normalized_question = question.lower()
    for raw in result.data.get("terms", []) or []:
        term = str(raw).strip()
        if not term or len(term) > 20:
            continue
        if term in _STOP_TERMS:
            continue
        if term.lower() in normalized_question:
            continue
        if term not in terms:
            terms.append(term)
    return terms[:max_terms]
