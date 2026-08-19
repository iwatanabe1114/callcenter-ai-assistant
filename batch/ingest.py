"""資料を取ってくるバッチ処理（設計書 4.1 / 2.3 設計のポイント）。

アプリ本体とは別の、独立したプログラムです。
決まった時間に自動実行してください（例：毎日 早朝5時）。

アプリ本体の中に「資料を取ってくる重い処理」を混ぜると、オペレーターさんが
質問している最中にその処理が動いてしまい、画面の反応が遅くなったり
不安定になったりします。

取得元は2通りあります。

  ① Google スプレッドシートから直接（本番）
     サービスアカウントの鍵が必要です。
       python -m batch.ingest

  ② Excelファイルから（サービスアカウントを用意する前の動作確認用）
     スプレッドシートを「ファイル」→「ダウンロード」→
     「Microsoft Excel (.xlsx)」で書き出し、data/source/ に置いてください。
       python -m batch.ingest --file
       python -m batch.ingest --file ~/Downloads/テスト.xlsx

ジャンルを絞る場合は続けて書きます:
    python -m batch.ingest manual
    python -m batch.ingest --file manual product

cron の例（毎日5時）:
    0 5 * * * cd /path/to/callcenter-ai-assistant && .venv/bin/python -m batch.ingest >> data/logs/ingest.log 2>&1
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache_store  # noqa: E402
from core.config import DATA_DIR, GENRES, load_config  # noqa: E402
from core.sources import sheets, workbook  # noqa: E402

SOURCE_DIR = DATA_DIR / "source"


def _find_workbook(given: str | None) -> Path | None:
    """読み込む Excel ファイルを決める。"""
    if given:
        return Path(given).expanduser()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (p for p in SOURCE_DIR.glob("*.xlsx") if not p.name.startswith("~$")),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return files[0] if files else None


def _write(targets: list[str], results: dict) -> int:
    failed = 0
    for genre in targets:
        item = results.get(genre)
        if item is None:
            print(f"  - {GENRES.get(genre, genre)}: 設定がありません（スキップ）")
            continue
        if item["error"]:
            print(f"  - {GENRES.get(genre, genre)}: [NG] {item['error']}")
            failed += 1
            continue
        path = cache_store.write_genre(genre, item["docs"], note=item.get("note", ""))
        print(f"  - {GENRES.get(genre, genre)}: {len(item['docs'])}件 → {path.name}")
        if item.get("note"):
            for line in str(item["note"]).split(" / "):
                print(f"      {line}")
    return failed


def run(genres: list[str] | None = None, *, from_file: str | None = None,
        use_file: bool = False) -> int:
    config = load_config()

    if not config.sources:
        print("[NG] 取得元シートの設定（[[sources]]）がありません。")
        return 1

    targets = genres or config.configured_genres
    unknown = [g for g in targets if g not in GENRES]
    if unknown:
        print(f"[NG] 知らないジャンルです: {', '.join(unknown)}")
        print(f"     使えるジャンル: {', '.join(GENRES)}")
        return 1

    print(f"取得対象: {', '.join(GENRES.get(g, g) for g in targets)}")
    print(f"ブランド: {config.tenant_id}（許可: {', '.join(config.allowed_tenant_ids)}）")

    # 4.1 実行する順番に注意。
    #     ここでは「ジャンル単位の全入れ替え」だけを行い、1ジャンル1ファイルに
    #     書き出します。あとから部分的な追記処理を足す場合は、必ずこの
    #     全入れ替えより「後」に実行してください。先に追記して後から作り直すと、
    #     追加しておいた分が消えます。
    if use_file or from_file:
        path = _find_workbook(from_file)
        if path is None:
            print("[NG] 読み込む Excel ファイルが見つかりません。")
            print(f"     スプレッドシートを「ファイル」→「ダウンロード」→")
            print(f"     「Microsoft Excel (.xlsx)」で書き出し、次の場所に置いてください:")
            print(f"       {SOURCE_DIR}")
            return 1
        print(f"取得元: {path}（Excelファイル）\n")
        try:
            results = workbook.fetch_all(config, path, targets)
        except workbook.WorkbookError as exc:
            print(f"[NG] {exc}")
            return 1
    else:
        if not config.has_sheets:
            print("[NG] サービスアカウントの鍵がありません。")
            print("     鍵を用意する場合:   python -m batch.setup_sheets <鍵.json>")
            print("     Excelで試す場合:    python -m batch.ingest --file")
            return 1
        print("取得元: Google スプレッドシート\n")
        results = sheets.fetch_all(config, targets)

    failed = _write(targets, results)

    if failed:
        print(f"\n[終了] {failed}ジャンルで失敗しました。")
        return 1
    print("\n[完了] キャッシュを更新しました。アプリを開くと実データで動きます。")
    return 0


def main(argv: list[str]) -> int:
    use_file = False
    from_file: str | None = None
    genres: list[str] = []

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--file", "-f"):
            use_file = True
            # 次の引数がジャンル名でなければ、ファイルパスとして扱う
            if i + 1 < len(argv) and argv[i + 1] not in GENRES and not argv[i + 1].startswith("-"):
                from_file = argv[i + 1]
                i += 1
        elif arg in ("--help", "-h"):
            print(__doc__)
            return 0
        else:
            genres.append(arg)
        i += 1

    return run(genres or None, from_file=from_file, use_file=use_file)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
