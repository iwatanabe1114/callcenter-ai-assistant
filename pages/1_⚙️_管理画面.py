"""管理画面（設計書 3.2 / 6.3）。

・「今すぐ資料を取り直す」ボタン（普段は自動更新だが、急いで最新版を反映したいとき用）
・どんな質問がよく来ているか、答えが見つかった割合はどれくらいか、といった簡単な集計
・個人情報の点検（5.2 マスキングの取りこぼしを目視で確認する）
"""

from __future__ import annotations

import streamlit as st

from core import cache_store, ui, usage_log
from core.auth import LEVEL_ADMIN, logout_button, require_login
from core.config import GENRES, get_config
from core.incident import load_from_gspread, load_from_xlsx, save_cache as save_incident_cache
from core.masking import find_leaks
from core.retrieval import clear_index, index_stats
from core.sources import sheets

ui.page_setup("管理画面")

config = get_config()

st.title("⚙️ 管理画面")

# 5.1 お客様の生データを見られる機能なので、必ずパスワードでログインさせる
if not require_login(LEVEL_ADMIN):
    st.stop()
logout_button(LEVEL_ADMIN)
ui.sidebar_footer(config)

tab_data, tab_incident, tab_usage, tab_privacy, tab_config = st.tabs(
    ["資料の状態", "インシデント", "利用状況", "個人情報の点検", "設定の確認"]
)

# ── 資料の状態・再取得 ───────────────────────────────────
with tab_data:
    st.subheader("キャッシュの状態")
    st.dataframe(cache_store.cache_status(), use_container_width=True, hide_index=True)

    st.caption(
        f"「{config.cache_ttl_hours:.0f}時間」を超えたら古いとみなす設定です。"
        " 短くしすぎると重い処理が何度も走り、長すぎると情報が古いままになります（6.1）。"
    )

    st.divider()
    st.subheader("今すぐ資料を取り直す")
    st.caption(
        "普段は自動更新（バッチ処理）ですが、急いで最新版を反映したいときに使います。"
        " アプリ本体で重い処理が走るので、対応が落ち着いているときに実行してください。"
    )

    if not config.has_sheets:
        st.info(
            "スプレッドシートの設定がありません。"
            " Secrets の [gcp_service_account] と [[sources]] を設定してください。"
        )
    else:
        targets = st.multiselect(
            "取り直す種類",
            options=config.configured_genres,
            default=config.configured_genres,
            format_func=lambda g: GENRES.get(g, g),
        )
        st.caption(
            "1つの種類に複数のシートを割り当てている場合は、まとめて取り直します。"
        )
        if st.button("取り直す", type="primary", disabled=not targets):
            with st.spinner("スプレッドシートから取得しています…"):
                results = sheets.fetch_all(config, targets)
            for genre in targets:
                item = results.get(genre, {})
                if item.get("error"):
                    st.error(f"{GENRES.get(genre, genre)}: {item['error']}")
                    continue
                cache_store.write_genre(genre, item["docs"], note=item.get("note", ""))
                message = f"{GENRES.get(genre, genre)}: {len(item['docs'])}件を取得しました。"
                if item.get("note"):
                    message += f"（{item['note']}）"
                st.success(message)
            clear_index()

    st.divider()
    if st.button("検索の索引だけ作り直す"):
        clear_index()
        st.success("次の検索で索引が作り直されます。")
    st.json(index_stats(list(GENRES)))

# ── インシデントテンプレート ──────────────────────────────
with tab_incident:
    st.subheader("インシデントテンプレートの取得")

    incident_worksheet = st.text_input(
        "シート名",
        value="インシデントテンプレ （202411~）",
        help="スプレッドシート内のインシデントテンプレートのシートタブ名",
    )

    # スプレッドシートIDは既存ソースから取得
    incident_spreadsheet_id = ""
    if config.sources:
        incident_spreadsheet_id = config.sources[0].spreadsheet_id

    col_gs, col_xl = st.columns(2)

    with col_gs:
        st.caption("スプレッドシートから取得（サービスアカウントが必要）")
        gs_disabled = not config.has_sheets or not incident_spreadsheet_id
        if st.button("スプレッドシートから取得", disabled=gs_disabled, type="primary"):
            with st.spinner("取得中…"):
                try:
                    tmpls = load_from_gspread(
                        config.service_account_info,
                        incident_spreadsheet_id,
                        incident_worksheet,
                    )
                    if tmpls:
                        save_incident_cache(tmpls)
                        st.success(f"{len(tmpls)}件のテンプレートを取得しました。")
                    else:
                        st.warning("テンプレートが見つかりませんでした。シート名を確認してください。")
                except Exception as exc:
                    st.error(f"取得に失敗しました: {exc}")
        if gs_disabled:
            st.caption("サービスアカウントが未設定です。")

    with col_xl:
        st.caption("Excelファイルから取得（動作確認用）")
        uploaded = st.file_uploader("xlsx ファイル", type=["xlsx"], key="incident_xlsx")
        if uploaded is not None:
            import tempfile
            from pathlib import Path
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                f.write(uploaded.read())
                tmp_path = Path(f.name)
            try:
                tmpls = load_from_xlsx(tmp_path, incident_worksheet)
                if tmpls:
                    save_incident_cache(tmpls)
                    st.success(f"{len(tmpls)}件のテンプレートを取得しました。")
                else:
                    st.warning("テンプレートが見つかりませんでした。シート名を確認してください。")
            except Exception as exc:
                st.error(f"読み込みに失敗しました: {exc}")
            finally:
                tmp_path.unlink(missing_ok=True)

    # 現在のキャッシュ状況
    st.divider()
    from core.incident import load_cache as load_incident_cache, get_categories
    cached = load_incident_cache()
    if cached:
        cats = get_categories(cached)
        st.success(f"キャッシュ済み: {len(cached)}件（{', '.join(cats)}）")
    else:
        st.info("まだ取得されていません。")

# ── 利用状況 ─────────────────────────────────────────────
with tab_usage:
    st.subheader("使われ方")
    entries = usage_log.load()
    if not entries:
        st.info("まだ記録がありません。")
    else:
        summary = usage_log.summarize(entries)
        col1, col2, col3 = st.columns(3)
        col1.metric("質問件数", summary["件数"])
        col2.metric("答えが見つかった件数", summary["回答できた件数"])
        col3.metric("回答率", f"{summary['回答率']}%")

        col4, col5 = st.columns(2)
        col4.metric("累計のAI費用", f"{summary['累計費用(円)']:,.1f} 円")
        col5.metric("1件あたり", f"{summary['1件あたり費用(円)']:.2f} 円")
        st.caption(
            "1ドル155円換算の概算です。実際の請求額は console.anthropic.com で確認してください。"
            " モデルを変える場合は、この数字と回答内容を見比べて判断できます。"
        )
        if summary["モデル別件数"]:
            st.caption("使ったモデル")
            st.json(summary["モデル別件数"])

        st.caption("機能別の件数")
        st.json(summary["機能別"])

        if summary["日別件数"]:
            st.caption("日別の件数")
            st.bar_chart(summary["日別件数"])

        st.divider()
        st.subheader("答えが見つからなかった質問")
        st.caption("「どんな資料が足りていないか」を知る手がかりになります（6.3）。")
        misses = summary["答えが見つからなかった質問"]
        if misses:
            for question in reversed(misses):
                st.write(f"・{question}")
        else:
            st.caption("該当なし。")

        st.download_button(
            "記録をダウンロード（JSONL）",
            data=usage_log.path().read_bytes(),
            file_name="usage.jsonl",
            mime="application/json",
        )

# ── 個人情報の点検 ───────────────────────────────────────
with tab_privacy:
    st.subheader("マスキングの取りこぼし点検")
    st.caption(
        "マスキング処理は完璧ではありません。100%見つけられる保証はないので、"
        " 実際のデータで、意図しない個人情報が残っていないか時々確認してください（5.2 / 5.4）。"
    )
    genre = st.selectbox("点検する種類", options=list(GENRES), format_func=lambda g: GENRES[g])
    docs = cache_store.read_genre(genre).get("docs", [])

    flagged = []
    unexpected_tenant = []
    for doc in docs:
        text = f"{doc.get('title','')}\n{doc.get('body','')}"
        leaks = find_leaks(text)
        if leaks:
            flagged.append({"行": doc.get("row"), "検出": "、".join(leaks), "タイトル": doc.get("title", "")})
        # 5.4 想定していない識別番号のデータが混ざっていないかの二重チェック
        if str(doc.get("tenant_id", config.tenant_id)) not in config.allowed_tenant_ids:
            unexpected_tenant.append({"行": doc.get("row"), "識別番号": doc.get("tenant_id")})

    if flagged:
        st.warning(f"{len(flagged)}件で個人情報らしき表記が残っています。元データの列指定を見直してください。")
        st.dataframe(flagged, use_container_width=True, hide_index=True)
    else:
        st.success("個人情報らしき表記は検出されませんでした。")

    if unexpected_tenant:
        st.error(
            f"想定外のブランド識別番号のデータが{len(unexpected_tenant)}件混ざっています。"
            " 他社の情報が見えている可能性があります。すぐに取得条件を確認してください（5.4）。"
        )
        st.dataframe(unexpected_tenant, use_container_width=True, hide_index=True)
    else:
        st.caption(f"ブランド識別番号はすべて許可された値です（許可: {', '.join(config.allowed_tenant_ids)}）。")

# ── 設定の確認 ───────────────────────────────────────────
with tab_config:
    st.subheader("いまの設定")
    st.caption("パスワードやAPIキーの値は表示しません。設定されているかどうかだけ表示します（5.3）。")
    st.json(
        {
            "AIのAPIキー": "設定済み" if config.has_llm else "未設定",
            "スプレッドシート連携": "設定済み" if config.has_sheets else "未設定",
            "回答AI": config.answer_model,
            "クエリ拡張AI": config.expansion_model,
            "effort": config.effort,
            "ブランド識別番号": config.tenant_id,
            "許可する識別番号": config.allowed_tenant_ids,
            "キャッシュの有効時間(時)": config.cache_ttl_hours,
            "元の質問の重み": config.original_term_weight,
            "追加した言葉の重み": config.expanded_term_weight,
            "AIに渡す資料の数": config.top_k,
        }
    )

    st.subheader("読み込む列の設定")
    st.caption("ここに書いた列だけを読み込みます。お客様氏名や電話番号の列は入れないでください（5.2）。")
    if config.sources:
        st.dataframe(
            [
                {
                    "種類": GENRES.get(s.genre, s.genre),
                    "シート": s.worksheet,
                    "形": "縦持ち(項目名→値)" if s.is_key_value else "表",
                    "読み込む列": "、".join(s.columns) if not s.is_key_value
                    else f"{s.key_column}列の項目名 → {'/'.join(s.value_columns)}列",
                    "古い行の除外": (
                        f"{s.date_column}が{s.max_age_days}日より前"
                        if s.date_column and s.max_age_days
                        else "-"
                    ),
                    "ブランド列": s.tenant_column or "-",
                }
                for s in config.sources
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("[[sources]] が未設定です。")
