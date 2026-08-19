"""サービスアカウントの設定を行う道具。

つまずきやすいのは次の2つなので、そこを自動化します。
  1. 鍵のJSONを secrets.toml の形に書き写すとき、private_key の改行で失敗する
  2. スプレッドシートをサービスアカウントに共有し忘れて、原因が分からなくなる

使い方:

  # ① 鍵のJSONを設定ファイルに取り込む（取得元シートの設定も一緒に入ります）
  python -m batch.setup_sheets ~/Downloads/xxxxx-abc123.json

  # ② ちゃんと繋がるか確認する（共有漏れはここで分かります）
  python -m batch.setup_sheets --check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import SECRETS_PATH, load_config  # noqa: E402

SOURCES_EXAMPLE = SECRETS_PATH.parent / "sources.example.toml"

_REQUIRED_KEYS = ["type", "project_id", "private_key", "client_email", "token_uri"]


def _toml_escape(value: str) -> str:
    """TOMLの二重引用符文字列として書けるようにする。

    private_key の改行はファイル上では \\n の2文字として書きます。
    TOML側がこれを改行として解釈してくれるので、鍵が壊れません。
    """
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace("\t", "\\t")
    )


def _has_sources(text: str) -> bool:
    """取得元シートの設定が実際に入っているか（コメント内の記述は数えない）。"""
    import tomllib

    try:
        return bool(tomllib.loads(text).get("sources"))
    except tomllib.TOMLDecodeError:
        return False


def _strip_section(text: str, header: str) -> str:
    """既存の [header] セクションを取り除く（入れ直せるようにするため）。"""
    lines = text.split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            skipping = stripped == header
        if stripped.startswith("[[") or (
            stripped.startswith("[") and stripped.endswith("]") and stripped != header
        ):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out)


def install(json_path: Path) -> int:
    if not json_path.exists():
        print(f"[NG] ファイルが見つかりません: {json_path}")
        return 1

    try:
        info = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[NG] JSONとして読めませんでした: {exc}")
        return 1

    missing = [k for k in _REQUIRED_KEYS if not info.get(k)]
    if missing:
        print(f"[NG] 鍵のJSONに必要な項目がありません: {', '.join(missing)}")
        print("     Google Cloud で「サービスアカウント」の鍵をJSON形式で作り直してください。")
        return 1
    if info.get("type") != "service_account":
        print(f"[NG] これはサービスアカウントの鍵ではないようです（type={info.get('type')}）。")
        return 1

    if not SECRETS_PATH.exists():
        print(f"[NG] {SECRETS_PATH} がありません。secrets.toml.example をコピーしてください。")
        return 1

    text = SECRETS_PATH.read_text(encoding="utf-8")
    had_credentials = "[gcp_service_account]" in text
    text = _strip_section(text, "[gcp_service_account]").rstrip() + "\n"

    block = ["", "[gcp_service_account]"]
    for key in [
        "type", "project_id", "private_key_id", "private_key",
        "client_email", "client_id", "token_uri",
    ]:
        if info.get(key):
            block.append(f'{key} = "{_toml_escape(info[key])}"')
    text += "\n".join(block) + "\n"

    # 取得元シートの設定がまだ無ければ、用意してあるものを続けて入れる。
    # 文字列で探すと、説明用のコメントに書かれた [[sources]] を
    # 「もう設定済み」と誤認するので、TOMLとして解釈して確かめる。
    added_sources = False
    if not _has_sources(text) and SOURCES_EXAMPLE.exists():
        text += "\n" + SOURCES_EXAMPLE.read_text(encoding="utf-8").rstrip() + "\n"
        added_sources = True

    SECRETS_PATH.write_text(text, encoding="utf-8")

    print(f"[OK] 鍵を取り込みました → {SECRETS_PATH}")
    if had_credentials:
        print("     （以前の鍵は置き換えました）")
    if added_sources:
        print("[OK] 取得元シートの設定も入れました（13シート）")

    print()
    print("次にやること:")
    print("  1. スプレッドシートの「共有」を開き、下のメールアドレスを")
    print("     「閲覧者」で追加してください。")
    print()
    print(f"       {info['client_email']}")
    print()
    print("  2. 共有できたら、繋がるか確認します:")
    print("       python -m batch.setup_sheets --check")
    return 0


def check() -> int:
    config = load_config()

    if not config.service_account_info:
        print("[NG] 鍵が設定されていません。先に次を実行してください:")
        print("       python -m batch.setup_sheets <鍵のJSONのパス>")
        return 1
    if not config.sources:
        print("[NG] 取得元シートの設定（[[sources]]）がありません。")
        print(f"     {SOURCES_EXAMPLE} の中身を secrets.toml に貼り付けてください。")
        return 1

    email = config.service_account_info.get("client_email", "(不明)")
    print(f"サービスアカウント: {email}")
    print(f"確認するシート: {len(config.sources)}枚\n")

    from core.sources import sheets  # 依存を必要なときだけ読む

    try:
        client = sheets._client(config)
    except sheets.SheetsError as exc:
        print(f"[NG] Googleに接続できません: {exc}")
        return 1
    except Exception as exc:  # 鍵の中身が壊れている場合など
        print(f"[NG] 鍵を読み込めませんでした: {exc}")
        print("     鍵のJSONをもう一度ダウンロードして、取り込み直してください:")
        print("       python -m batch.setup_sheets <鍵のJSONのパス>")
        return 1

    ok = 0
    failed: list[str] = []
    opened: dict[str, object] = {}

    for source in config.sources:
        try:
            if source.spreadsheet_id not in opened:
                opened[source.spreadsheet_id] = client.open_by_key(source.spreadsheet_id)
            worksheet = opened[source.spreadsheet_id].worksheet(source.worksheet)  # type: ignore[attr-defined]
            rows = len(worksheet.get_all_values())
            print(f"  OK  {source.label:22} {source.worksheet:24} {rows:>5}行")
            ok += 1
        except Exception as exc:
            message = str(exc)
            if "PERMISSION_DENIED" in message or "403" in message:
                hint = "共有されていません"
            elif "WorksheetNotFound" in type(exc).__name__ or "not found" in message.lower():
                hint = "シート名が違います"
            else:
                hint = message[:60]
            print(f"  NG  {source.label:22} {source.worksheet:24} {hint}")
            failed.append(f"{source.label}（{hint}）")

    print(f"\n成功 {ok}件 / 失敗 {len(failed)}件")
    if failed:
        print("\n失敗したもの:")
        for item in failed:
            print(f"  - {item}")
        print(f"\n「共有されていません」と出た場合は、スプレッドシートの共有に")
        print(f"  {email} を「閲覧者」で追加してください。")
        return 1

    print("\nすべて読めました。取り込みを実行できます:")
    print("  python -m batch.ingest")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    if argv[0] in ("--check", "-c", "check"):
        return check()
    return install(Path(argv[0]).expanduser())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
