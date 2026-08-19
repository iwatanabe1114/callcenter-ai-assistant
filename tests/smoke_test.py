"""動作確認用のかんたんなテスト。

外部サービス（Claude API・Google スプレッドシート）には接続しません。
AIの返事とシートの中身は偽物に差し替えて、設計書で特に注意が必要とされている
挙動と、実際のスプレッドシートの形に合わせた読み取りを確認します。

実行:
    .venv/bin/python -m tests.smoke_test
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cache_store, llm, search  # noqa: E402
from core.config import GENRES, SourceConfig, load_config  # noqa: E402
from core.masking import find_leaks, mask_text  # noqa: E402
from core.sources import sheets  # noqa: E402

_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  OK   {label}")
    else:
        print(f"  NG   {label}" + (f"  → {detail}" if detail else ""))
        _failures.append(label)


# ── 5.2 マスキング ─────────────────────────────────────────
def test_masking() -> None:
    print("\n[5.2] 個人情報のマスキング")
    for text in [
        "山田様よりお電話",
        "山田太郎さまより連絡",  # ひらがなの「さま」も見る
        "田中さんが対応",
        "田中様も同様のケース",
        "090-1234-5678 に連絡",
        "taro@example.com へ送信",
        "東京都渋谷区神南1-2-3 へ返送",
    ]:
        check(f"隠せる: {text}", "[MASKED" in mask_text(text), mask_text(text))

    # 隠しすぎると、マニュアルの文意が壊れてAIの回答精度が落ちる
    for text in [
        "返送料はお客様負担が原則です",
        "使用状況を所定様式で聞き取り",
        "様々なご要望",
        "担当者様あて",
        "商品の仕様",
        "同様の対応",
    ]:
        check(f"隠さない: {text}", mask_text(text) == text, mask_text(text))

    check(
        "電話番号を郵便番号と誤認しない",
        "[MASKED_PHONE]" in mask_text("090-1234-5678"),
        mask_text("090-1234-5678"),
    )


# ── 4.2 / 4.3 検索 ─────────────────────────────────────────
def _sample_docs() -> list[dict]:
    """テストは常にサンプルデータで行う。

    実データを取り込むとキャッシュの中身が変わるため、
    cache_store 経由ではなくサンプルを直接読みます。
    """
    import json

    from core.config import SAMPLE_DIR

    docs: list[dict] = []
    for path in sorted(SAMPLE_DIR.glob("*.json")):
        docs.extend(json.loads(path.read_text(encoding="utf-8")).get("docs", []))
    return docs


def test_search() -> None:
    print("\n[4.2/4.3] キーワード検索（bi-gram BM25・サンプルデータ）")
    docs = _sample_docs()
    check("資料が読み込める", len(docs) > 0, f"{len(docs)}件")
    index = search.build_index(docs)

    for question, expected in [
        ("返品したいと言われた", "返品の受付手順"),
        ("定期便を解約したい", "定期便の解約受付"),
        ("支払い方法をカードから代引きに変えたい", "支払い方法の変更"),
        ("サプリの粒数", "グリーンサプリ"),
    ]:
        terms = search.weighted_terms(search.split_query(question), [])
        titles = [h.title for h in index.search(terms, top_k=3)]
        check(f"「{question}」で正しい資料が上位3件に入る", any(expected in t for t in titles), str(titles))

    terms = search.weighted_terms(["返品"], ["返送"], original_weight=1.0, expanded_weight=0.35)
    check("追加語の重みが元の質問より低い", terms["返送"] < terms["返品"], str(terms))


# ── 4.4 構造化出力の打ち切り検知 ───────────────────────────
class _Block:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, stop_reason: str, text: str):
        self.stop_reason = stop_reason
        self.content = [_Block(text)]


def _fake_client(stop_reason: str, text: str):
    response = _Response(stop_reason, text)

    class _Messages:
        def create(self, **_kwargs):
            return response

    class _Client:
        messages = _Messages()

    return lambda _config: _Client()


def _call(config):
    return llm.structured_call(
        config, model="claude-opus-5", system="s", user_content="u",
        schema=llm.ANSWER_SCHEMA, max_tokens=100,
    )


def test_truncation() -> None:
    print("\n[4.4] 構造化出力が途中で打ち切られたときの扱い")
    config = dataclasses.replace(load_config(), anthropic_api_key="dummy")
    original = llm.get_client
    try:
        # 一見正常に見えるJSONでも、打ち切られていたら使ってはいけない
        llm.get_client = _fake_client(
            "max_tokens", '{"has_answer":true,"answer":"返品は8日以内","details":"","used_doc_ids":[],"caution":""}'
        )
        result = _call(config)
        check("打ち切りを検知して結果を捨てる", (not result.ok) and result.truncated)

        llm.get_client = _fake_client("refusal", "{}")
        check("回答拒否を検知する", (lambda r: (not r.ok) and r.refused)(_call(config)))

        llm.get_client = _fake_client("end_turn", '{"has_answer":true,"answer":"途中で')
        check("壊れたJSONを弾く", not _call(config).ok)

        llm.get_client = _fake_client(
            "end_turn", '{"has_answer":true,"answer":"OK","details":"","used_doc_ids":["manual-2"],"caution":""}'
        )
        result = _call(config)
        check("正常な回答は通す", result.ok and result.data.get("has_answer") is True)
    finally:
        llm.get_client = original


# ── 6.1 古い情報の除外（Q2） ───────────────────────────────
def test_date_filter() -> None:
    print("\n[6.1] 古い行の除外（共有日から半年）")
    for text, expected in [
        ("2026/08/06", date(2026, 8, 6)),
        ("2026-08-06", date(2026, 8, 6)),
        ("26/08/06", date(2026, 8, 6)),
        ("2026/8/6(火)", date(2026, 8, 6)),
        ("2026年8月6日", date(2026, 8, 6)),
    ]:
        check(f"日付を読める: {text}", sheets.parse_date(text) == expected, str(sheets.parse_date(text)))

    today = date(2026, 8, 17)
    check("半年より前は古い", sheets.is_too_old("2026/01/01", 180, today))
    check("半年以内は残す", not sheets.is_too_old("2026/06/01", 180, today))
    # 消しすぎないための安全側の作り
    check("空欄は除外しない", not sheets.is_too_old("", 180, today))
    check("読み取れない日付は除外しない", not sheets.is_too_old("随時", 180, today))


# ── シートの読み取り（Q2 / Q3） ────────────────────────────
def _config_for_test():
    return dataclasses.replace(load_config(), tenant_id="default", allowed_tenant_ids=["default"])


def test_table_layout() -> None:
    print("\n[表レイアウト] 回覧板・全商材価格表の形")
    config = _config_for_test()

    # 回覧板：共有日で古い行を落とす
    source = SourceConfig(
        genre="manual", spreadsheet_id="x", worksheet="回覧板", name="回覧板",
        columns=["No", "共有日", "カテゴリ", "タイトル", "内容"],
        title_columns=["タイトル"], body_columns=["カテゴリ", "内容", "共有日"],
        date_column="共有日", max_age_days=180,
    )
    rows = [
        ["No", "共有日", "カテゴリ", "タイトル", "内容"],
        ["228", "2026/08/06", "トラブル・緊急系", "購入画面のエラーについて", "現在調査中です"],
        ["10", "2024/01/15", "共有", "古い連絡", "半年以上前の内容"],
        ["11", "", "共有", "日付なしの連絡", "日付が空の行"],
    ]
    docs, _ = sheets._read_table(source, rows, config, None, {})
    titles = [d["title"] for d in docs]
    check("新しい行は取り込む", "購入画面のエラーについて" in titles, str(titles))
    check("半年より古い行は落とす", "古い連絡" not in titles, str(titles))
    check("日付が空の行は残す", "日付なしの連絡" in titles, str(titles))
    check("列名がラベルとして本文に入る", "カテゴリ: トラブル・緊急系" in docs[0]["body"], docs[0]["body"][:60])

    # 全商材価格表：列名が重複するので位置指定＋商材名の穴埋め
    price = SourceConfig(
        genre="price", spreadsheet_id="x", worksheet="全商材価格表", name="全商材価格表",
        columns=["A", "B", "E", "F"], title_columns=["A", "B"], body_columns=["E", "F"],
        fill_down_columns=["A"],
        column_labels={"A": "商材", "B": "コース名", "E": "初回 金額(クレカ)", "F": "初回 金額(後払い)"},
    )
    rows = [
        ["", "コース名", "", "", "金額(クレカ)", "金額(後払い)"],
        ["メグレア", "メグレア通常定期コース", "", "", "500", "650"],
        ["", "メグレア通常定期コースおまとめ配送", "", "", "500", "650"],
    ]
    docs, _ = sheets._read_table(price, rows, config, None, {})
    check("列を位置（A/B/E/F）で指定できる", len(docs) == 2, f"{len(docs)}件")
    check(
        "空欄の商材名を上の行から引き継ぐ",
        docs[1]["title"].startswith("メグレア"),
        docs[1]["title"],
    )
    check(
        "重複する列名に表示名を付けられる",
        "初回 金額(クレカ): 500" in docs[0]["body"],
        docs[0]["body"],
    )


def test_key_value_layout() -> None:
    print("\n[縦持ちレイアウト] 商材シートの形")
    config = _config_for_test()
    source = SourceConfig(
        genre="product", spreadsheet_id="x", worksheet="爽軽青汁", name="爽軽青汁",
        layout="key_value", key_column="A", value_columns=["B", "C", "D"],
        skip_keys=["商品画像"], start_row=2,
    )
    # 内容がC列の商材（爽軽青汁など）とB列の商材（メグレアlightなど）が混在する
    rows = [
        ["。・・ 商品研修資料 商品 機能性表示食品", "", "商品研修資料_青汁"],  # 1行目はバナー
        ["名称", "", "大麦若葉加工食品"],
        ["内容量(30日分)", "", "90g(3g×30袋)"],
        ["販売内容", "", ""],
        ["商品画像", "", "https://example.com/a.png"],
        ["発売日", "2024/1/16", ""],
    ]
    docs, note = sheets._read_key_value(source, rows, config, None)
    titles = [d["title"] for d in docs]
    check("1行目のバナー行を読み飛ばす", not any("商品研修資料_青汁" in d["body"] for d in docs), str(titles[:2]))
    check("C列の内容を読める", "爽軽青汁／名称" in titles, str(titles))
    check("B列の内容も読める", "爽軽青汁／発売日" in titles, str(titles))
    check("見出しだけの行は資料にしない", "爽軽青汁／販売内容" not in titles, str(titles))
    check("読み飛ばす項目を除外できる", "爽軽青汁／商品画像" not in titles, str(titles))
    check("本文に商品名が入る", docs[0]["body"].startswith("商品: 爽軽青汁"), docs[0]["body"][:40])
    check("資料IDにシート名が入る（複数シート統合用）", docs[0]["id"].startswith("product-"), docs[0]["id"])


def test_column_resolution() -> None:
    print("\n[列の指定] 列名と列位置")
    header = ["No", "共有日", "カテゴリ"]
    check("列名で引ける", sheets.resolve_column("共有日", header) == 1)
    check("列位置(A)で引ける", sheets.resolve_column("A", header) == 0)
    check("列位置(E)で引ける", sheets.resolve_column("E", header) == 4)
    check("列位置(AA)で引ける", sheets.resolve_column("AA", header) == 26)
    check("無い列は None", sheets.resolve_column("存在しない列", header) is None)


# ── 5.4 テナントの二重チェック ─────────────────────────────
def test_tenant() -> None:
    print("\n[5.4] ブランド識別番号と個人情報の点検（取り込み済みデータ）")
    config = load_config()
    unexpected = [
        d
        for genre in GENRES
        for d in cache_store.read_genre(genre).get("docs", [])
        if str(d.get("tenant_id", config.tenant_id)) not in config.allowed_tenant_ids
    ]
    check("想定外の識別番号のデータが無い", not unexpected, f"{len(unexpected)}件")

    leaked = [
        (genre, d.get("row"))
        for genre in GENRES
        for d in cache_store.read_genre(genre).get("docs", [])
        if find_leaks(f"{d.get('title','')}\n{d.get('body','')}")
    ]
    check("資料に個人情報らしき表記が残っていない", not leaked, str(leaked[:5]))


def main() -> int:
    print("コールセンターAIアシスタント：動作確認")
    test_masking()
    test_search()
    test_truncation()
    test_date_filter()
    test_column_resolution()
    test_table_layout()
    test_key_value_layout()
    test_tenant()
    print("\n" + "=" * 50)
    if _failures:
        print(f"失敗 {len(_failures)}件:")
        for name in _failures:
            print(f"  - {name}")
        return 1
    print("すべて成功しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
