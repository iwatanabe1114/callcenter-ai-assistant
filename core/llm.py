"""AI（LLM）に答えの文章を作ってもらう部分（設計書 4.4 / 8.3）。

守っていること:
  4.4  「渡した資料の内容だけをもとに答えること」「資料にないことは想像で
       書かないこと」をシステムプロンプトにはっきり書く。
  4.4  構造化出力（決まった項目に分けたデータ）を使う場合の注意点：
       答えの長さの上限に達すると、まだ書き終わっていないのに途中で打ち切られ、
       それでも「一見正常に見えるデータ」として返ってくることがある。
       返事に「途中で打ち切られたか」を示す情報（stop_reason）が含まれるので
       必ず確認し、打ち切られていた場合は結果を使わずに安全な表示に切り替える。
  8.3  AIに「正確な数字」や「参照元」を作文させない。参照元は検索結果側に
       持たせておき、画面にはプログラム側が機械的に表示する。
       「確定した情報」と「まだ確定していない提案」を区別させる。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import AppConfig
from .search import Hit

# Haiku は effort パラメータに対応していないので付けない
_EFFORT_UNSUPPORTED_PREFIXES = ("claude-haiku",)

# 1Mトークンあたりの料金（米ドル、入力／出力）。費用の目安表示に使います。
# 料金が変わったらここを直してください。
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
USD_JPY = 155  # 表示用の概算レート


def estimate_yen(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = MODEL_PRICES.get(model, (5.0, 25.0))
    usd = input_tokens / 1_000_000 * price_in + output_tokens / 1_000_000 * price_out
    return usd * USD_JPY


class LLMError(RuntimeError):
    pass


@dataclass
class StructuredResult:
    """構造化出力の呼び出し結果。

    ok が False のときは data を使ってはいけません（4.4）。
    """

    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    stop_reason: str = ""
    truncated: bool = False
    refused: bool = False
    error: str = ""
    raw_text: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def yen(self) -> float:
        """この呼び出しの概算費用（円）。"""
        return estimate_yen(self.model, self.input_tokens, self.output_tokens)

    @property
    def failure_message(self) -> str:
        if self.truncated:
            return (
                "AIの回答が長さの上限に達して途中で切れました。"
                "不完全な内容を表示しないよう、結果を破棄しています。"
                "質問を短くするか、もう一度お試しください。"
            )
        if self.refused:
            return "AIがこの内容への回答を控えました。上長に確認してください。"
        return self.error or "AIの呼び出しに失敗しました。"


def get_client(config: AppConfig):
    if not config.anthropic_api_key:
        raise LLMError(
            "Anthropic の APIキーが設定されていません。Secrets の anthropic_api_key を確認してください。"
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise LLMError("anthropic パッケージが入っていません。pip install -r requirements.txt を実行してください。") from exc
    return anthropic.Anthropic(api_key=config.anthropic_api_key)


def _output_config(schema: dict[str, Any], model: str, effort: str) -> dict[str, Any]:
    out: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if not model.startswith(_EFFORT_UNSUPPORTED_PREFIXES):
        out["effort"] = effort
    return out


def structured_call(
    config: AppConfig,
    *,
    model: str,
    system: str,
    user_content: str,
    schema: dict[str, Any],
    max_tokens: int,
    effort: str | None = None,
) -> StructuredResult:
    """決まった項目に分けたデータとして答えを受け取る。

    stop_reason を必ず確認し、途中で打ち切られていたら結果を捨てます（4.4）。
    """
    try:
        client = get_client(config)
    except LLMError as exc:
        return StructuredResult(ok=False, error=str(exc))

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_config=_output_config(schema, model, effort or config.effort),
        )
    except Exception as exc:
        return StructuredResult(ok=False, error=f"AIの呼び出しでエラーが発生しました: {exc}")

    stop_reason = str(getattr(response, "stop_reason", "") or "")

    # 費用の記録用にトークン数を控えておく（失敗時も課金されるため必ず載せる）
    usage = getattr(response, "usage", None)
    meta = {
        "model": model,
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }

    # 4.4 途中で打ち切られていないか必ず確認する
    if stop_reason == "max_tokens":
        return StructuredResult(ok=False, stop_reason=stop_reason, truncated=True, **meta)
    if stop_reason == "refusal":
        return StructuredResult(ok=False, stop_reason=stop_reason, refused=True, **meta)

    text = ""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "text":
            text += getattr(block, "text", "")

    if not text.strip():
        return StructuredResult(
            ok=False, stop_reason=stop_reason, error="AIから空の回答が返ってきました。",
            raw_text=text, **meta
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return StructuredResult(
            ok=False,
            stop_reason=stop_reason,
            error="AIの回答を決まった形式として読み取れませんでした。",
            raw_text=text,
            **meta,
        )

    if not isinstance(data, dict):
        return StructuredResult(
            ok=False, stop_reason=stop_reason, error="AIの回答の形式が想定と違います。",
            raw_text=text, **meta
        )

    return StructuredResult(ok=True, data=data, stop_reason=stop_reason, raw_text=text, **meta)


# ─────────────────────────────────────────────────────────────
# チャット画面用：質問に答える
# ─────────────────────────────────────────────────────────────

ANSWER_SYSTEM = """あなたはコールセンターのオペレーターを支援するアシスタントです。
オペレーターはお客様を保留にしたまま、この画面を読んでいます。
待たせている時間が長いほどクレームにつながるため、結論を最短で伝えることが最優先です。

答えは「結論」と「条件・詳細」の2つに分けて返してください。

【answer（結論）】
  ・オペレーターが最初に読む部分です。1文、長くても2文。**80文字以内**。
  ・これだけ読めば次の行動が決まる内容にしてください。
  ・条件が絡む場合は「〜の場合は可能です」のように、結論と主要な条件を1文にまとめます。
  ・箇条書きにしないでください。
  ・**強調** や見出し記号などの装飾は使わず、そのまま読める文章にしてください。

【details（条件・詳細）】
  ・例外・細かい条件・金額の内訳・手続きの手順など、結論を補う情報を書きます。
  ・オペレーターが必要になったときだけ開く場所なので、ここは箇条書きで構いません。
  ・書くことが無ければ空文字にしてください。結論の言い換えを書かないでください。

守るべき約束事：
1. 渡された【参考資料】の内容だけをもとに答えてください。資料に書かれていないことは、
   知識や推測で補わず、絶対に書かないでください。
2. 資料の中に答えが無い場合は、has_answer を false にし、answer には
   「この質問に該当する記載が資料内に見つかりませんでした」とだけ書いてください。
   それらしい答えを作らないでください。
3. 金額・日数・件数などの数字は、資料に書かれている値をそのまま引用してください。
   計算したり、丸めたり、それらしい数字を作ったりしないでください。
4. 「何行目に書いてあるか」「どの資料か」といった参照元はどこにも書かないでください。
   参照元は画面側が機械的に表示します。
5. 「すでに確定した情報」と「お客様やオペレーターがそう提案しただけで、まだ確定して
   いない内容」を区別してください。確定していない金額や条件を、確定した実績である
   かのように書いてはいけません。
6. 資料の中で本社への確認が必要と書かれている場合は、caution にその旨を書いてください。
"""

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_answer": {
            "type": "boolean",
            "description": "参考資料の中に、質問に答えられる記載があったか",
        },
        "answer": {
            "type": "string",
            "description": "結論。1〜2文、80文字以内。これだけ読めば次の行動が決まる内容",
        },
        "details": {
            "type": "string",
            "description": "条件・例外・内訳など、結論を補う情報。箇条書き可。無ければ空文字",
        },
        "used_doc_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "答えの根拠に使った参考資料のID（資料に付いている id をそのまま）",
        },
        "caution": {
            "type": "string",
            "description": "本社確認が必要など、注意すべきことがあれば書く。無ければ空文字",
        },
    },
    "required": ["has_answer", "answer", "details", "used_doc_ids", "caution"],
    "additionalProperties": False,
}


def format_documents(hits: list[Hit]) -> str:
    """検索で見つけた資料を、AIに渡す形に整える。"""
    blocks = []
    for hit in hits:
        blocks.append(
            "\n".join(
                [
                    f"[id: {hit.id}]",
                    f"ジャンル: {hit.genre}",
                    f"タイトル: {hit.title}",
                    f"内容:\n{hit.doc.get('body','')}",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks)


def answer_question(config: AppConfig, question: str, hits: list[Hit]) -> StructuredResult:
    if not hits:
        return StructuredResult(
            ok=True,
            data={
                "has_answer": False,
                "answer": "この質問に該当する記載が資料内に見つかりませんでした。",
                "details": "",
                "used_doc_ids": [],
                "caution": "",
            },
        )

    user_content = (
        f"【オペレーターからの質問】\n{question}\n\n"
        f"【参考資料】\n{format_documents(hits)}\n\n"
        "上の参考資料の中だけを根拠に答えてください。"
    )
    return structured_call(
        config,
        model=config.answer_model,
        system=ANSWER_SYSTEM,
        user_content=user_content,
        schema=ANSWER_SCHEMA,
        max_tokens=config.answer_max_tokens,
    )
