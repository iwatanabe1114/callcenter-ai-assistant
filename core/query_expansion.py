"""セマンティッククエリ生成（設計書 4.3 改良版）。

オペレーターの質問を意味的に解釈して、社内マニュアル側で使われている
表現に近い検索キーワードを生成する。

従来の「同義語5〜8語」から進化し、以下の3種類のキーワードを生成する：
  1. 同義語・言い換え（返品→返送、解約→退会）
  2. 関連する業務用語（返品→返送先住所、着払い、未開封）
  3. マニュアルの見出しに使われそうな表現（返品→返品の受付手順）

追加した言葉は search.weighted_terms() で低い重みを付けて扱う。
元の質問の言葉と同じ重要度で扱うと、一般的すぎる語が
ほとんど全部の資料にヒットして、かえって精度が落ちるため。
"""

from __future__ import annotations

from typing import Any

from .config import AppConfig
from .llm import structured_call

EXPANSION_SYSTEM = """あなたはコールセンター向け社内マニュアル検索のアシスタントです。
オペレーターの質問文を受け取り、社内マニュアルを検索するための最適なキーワードを生成してください。

あなたの役割は、オペレーターが使う「話し言葉」を、マニュアルに書かれている「文書の表現」に変換することです。

【生成するキーワードの種類】

1. synonyms（同義語・言い換え）2〜4語
   - 同じ意味で別の表現（例：返品→返送、解約→退会・停止）
   - 業界・社内でよく使われる表記ゆれ

2. related（関連する業務用語）2〜4語
   - その業務で一緒に出てくる具体的な用語
   - 例：返品 → 未開封、着払い、返送先住所、返品期限
   - 例：解約 → 次回発送日、10日前、回数縛り、差額

3. headings（マニュアルの見出しに使われそうな表現）1〜3語
   - 社内マニュアルのタイトルやセクション名として書かれそうな短いフレーズ
   - 例：「返品したい」→ 返品の受付手順、返送先住所の案内
   - 例：「解約したい」→ 定期便の解約受付、解約の流れ

【重要なルール】
- 各カテゴリの語数を守ること（合計5〜11語）
- 名詞または短い名詞句のみ。文章にしないこと。
- 「対応」「手続き」「方法」「確認」「案内」のような、どんな資料にも出てくる一般的すぎる語は絶対に含めないこと。
- 質問文にすでに出ている語は含めないこと。
- 健康食品・サプリメントの通販コールセンターという文脈を意識すること。
"""

EXPANSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "synonyms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "同義語・言い換え（2〜4語）",
        },
        "related": {
            "type": "array",
            "items": {"type": "string"},
            "description": "関連する業務用語（2〜4語）",
        },
        "headings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "マニュアルの見出しに使われそうな表現（1〜3語）",
        },
    },
    "required": ["synonyms", "related", "headings"],
    "additionalProperties": False,
}

# 一般的すぎて検索の役に立たない語（AIが出してきても捨てる）
_STOP_TERMS = {
    "対応", "手続き", "方法", "確認", "case", "ケース", "内容", "情報", "資料",
    "お客様", "顧客", "質問", "回答", "説明", "案内", "件", "こと", "もの",
    "手順", "流れ", "について", "する", "できる", "ある", "いる",
    "コールセンター", "オペレーター", "電話",
}


def expand_query(config: AppConfig, question: str, *, max_terms: int = 11) -> list[str]:
    """質問の意図を解釈して、マニュアル検索に最適なキーワードを生成する。

    失敗したら空リスト（拡張なしで検索を続ける）。
    """
    if not question.strip() or not config.has_llm:
        return []

    result = structured_call(
        config,
        model=config.expansion_model,
        system=EXPANSION_SYSTEM,
        user_content=f"オペレーターの質問: {question}",
        schema=EXPANSION_SCHEMA,
        max_tokens=512,
    )
    if not result.ok:
        return []

    terms: list[str] = []
    normalized_question = question.lower()

    # 3カテゴリを順に処理（synonyms, related, headings）
    for key in ("synonyms", "related", "headings"):
        for raw in result.data.get(key, []) or []:
            term = str(raw).strip()
            if not term or len(term) > 30:
                continue
            if term in _STOP_TERMS:
                continue
            if term.lower() in normalized_question:
                continue
            if term not in terms:
                terms.append(term)

    return terms[:max_terms]
