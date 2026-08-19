"""Claude APIキーの設定と動作確認。

APIキーはコマンドの引数に書きません（シェルの履歴に残ってしまうため）。
実行すると入力を求められるので、そこに貼り付けてください（画面には表示されません）。

使い方:

  # ① APIキーを設定する
  python -m batch.setup_llm

  # ② 実際に呼び出して確認する（料金は1円未満です）
  python -m batch.setup_llm --check
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import llm  # noqa: E402
from core.config import SECRETS_PATH, load_config  # noqa: E402

# 1Mトークンあたりの料金（米ドル）。表示用の概算です。
_PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_USD_JPY = 155  # 表示用の概算レート


def _write_key(key: str) -> bool:
    if not SECRETS_PATH.exists():
        print(f"[NG] {SECRETS_PATH} がありません。")
        return False

    lines = SECRETS_PATH.read_text(encoding="utf-8").split("\n")
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("anthropic_api_key"):
            # 最初のセクション見出しより前に書く必要があるので、位置は動かさない
            out.append(f'anthropic_api_key = "{key}"')
            replaced = True
        else:
            out.append(line)

    if not replaced:
        # 見つからない場合は、どのセクションよりも前（ファイル冒頭）に入れる
        insert_at = next(
            (i for i, ln in enumerate(out) if ln.strip().startswith("[")), len(out)
        )
        out.insert(insert_at, f'anthropic_api_key = "{key}"')
        out.insert(insert_at + 1, "")

    SECRETS_PATH.write_text("\n".join(out), encoding="utf-8")
    return True


def install() -> int:
    print("Claude APIキーを設定します。")
    print("まだ持っていない場合は https://console.anthropic.com で発行してください。")
    print("（入力しても画面には表示されません。貼り付けて Enter を押してください）")
    print()

    try:
        key = getpass.getpass("APIキー: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n中止しました。")
        return 1

    if not key:
        print("[NG] 何も入力されませんでした。")
        return 1
    if not key.startswith("sk-ant-"):
        print("[NG] Anthropic のAPIキーは sk-ant- で始まります。")
        print("     コピーする範囲が間違っていないか確認してください。")
        return 1

    if not _write_key(key):
        return 1

    print(f"\n[OK] 設定しました → {SECRETS_PATH}")
    print("     （このファイルはコード管理に含まれません）")
    print("\n続けて動作確認をします...\n")
    return check()


def _call(client, model: str, **kwargs):
    return client.messages.create(
        model=model,
        max_tokens=kwargs.pop("max_tokens", 1024),
        messages=[{"role": "user", "content": kwargs.pop("prompt")}],
        **kwargs,
    )


def _cost_yen(model: str, usage) -> float:
    price_in, price_out = _PRICES.get(model, (5.0, 25.0))
    tokens_in = getattr(usage, "input_tokens", 0) or 0
    tokens_out = getattr(usage, "output_tokens", 0) or 0
    usd = tokens_in / 1_000_000 * price_in + tokens_out / 1_000_000 * price_out
    return usd * _USD_JPY


def check() -> int:
    config = load_config()

    if not config.has_llm:
        print("[NG] APIキーが設定されていません。先に次を実行してください:")
        print("       python -m batch.setup_llm")
        return 1

    try:
        client = llm.get_client(config)
    except llm.LLMError as exc:
        print(f"[NG] {exc}")
        return 1

    total_yen = 0.0

    # ① 言い換え生成に使う軽量モデル
    print(f"① 言い換え生成 ({config.expansion_model}) …", end="", flush=True)
    try:
        response = _call(client, config.expansion_model, prompt="「返品」の言い換えを3語、単語だけで。", max_tokens=200)
    except Exception as exc:
        print(f" NG\n   {_explain(exc)}")
        return 1
    total_yen += _cost_yen(config.expansion_model, response.usage)
    print(" OK")

    # ② 回答生成に使うモデル。実際に使う構造化出力の形で呼ぶ
    print(f"② 回答生成   ({config.answer_model}) …", end="", flush=True)
    result = llm.structured_call(
        config,
        model=config.answer_model,
        system=llm.ANSWER_SYSTEM,
        user_content=(
            "【オペレーターからの質問】\n返品は何日以内ですか？\n\n"
            "【参考資料】\n[id: manual-2]\nジャンル: manual\nタイトル: 返品の受付手順\n"
            "内容:\n商品到着から8日以内・未開封であれば返品を受け付けます。"
        ),
        schema=llm.ANSWER_SCHEMA,
        max_tokens=config.answer_max_tokens,
    )
    if not result.ok:
        print(f" NG\n   {result.failure_message}")
        return 1
    print(" OK")

    print("\n── 回答の確認 ──")
    print(f"  答えが見つかったか: {result.data.get('has_answer')}")
    print(f"  回答: {result.data.get('answer')}")
    print(f"  根拠にした資料: {result.data.get('used_doc_ids')}")

    print(f"\n概算費用: 約{total_yen:.2f}円（この確認1回分）")
    print("\nすべて正常です。アプリを起動して試せます:")
    print("  .venv/bin/streamlit run app.py")
    print("\n※ 現在はサンプルデータです。スプレッドシートを繋ぐと実際の資料に切り替わります。")
    return 0


def _explain(exc: Exception) -> str:
    message = str(exc)
    if "authentication" in message.lower() or "401" in message:
        return "APIキーが正しくありません。console.anthropic.com で確認してください。"
    if "credit" in message.lower() or "billing" in message.lower():
        return "残高または支払い方法の設定を確認してください（console.anthropic.com の Billing）。"
    if "rate_limit" in message.lower() or "429" in message:
        return "呼び出し制限に達しています。少し待ってからもう一度お試しください。"
    if "not_found" in message.lower() or "404" in message:
        return "モデル名が正しくない可能性があります（secrets.toml の [llm]）。"
    return message[:200]


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("--check", "-c", "check"):
        return check()
    return install()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
