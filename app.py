"""チャット画面（設計書 3.1）。

オペレーターさんが話し言葉に近い自然な文章で質問すると、
アプリが関係のありそうな資料を探し、AIが答えの文章を作ります。
答えと一緒に、元になった資料へのリンクも表示します。
"""

from __future__ import annotations

import streamlit as st

from core import cache_store, llm, ui, usage_log
from core.auth import LEVEL_CHAT, logout_button, require_login
from core.config import CHAT_GENRES, GENRES, get_config
from core.retrieval import clear_index, retrieve
from core.sheet_log import append_feedback, append_log

BRAND_NAME = "みなわ発酵"

PRODUCT_NAMES = [
    "爽軽青汁",
    "メグレアpremium",
    "メグレアlight",
    "糖貫プロネス",
    "肝匠プロネス",
    "アイゼン",
    "アユミルpremium",
    "はつらつコラーゲン(クロス用)",
    "すっぽん黒酢",
    "LIPO CLEAR VITAMIN C",
]

ui.page_setup("コールセンターAIアシスタント")

config = get_config()


# ── 起動時にスプレッドシートから自動取得（1回だけ） ──────────
@st.cache_resource(show_spinner=False)
def _auto_ingest():
    """アプリ起動時にスプレッドシートから最新データを取得する。"""
    if not config.has_sheets:
        return False
    try:
        from core.sources import sheets
        results = sheets.fetch_all(config, config.configured_genres)
        for genre in config.configured_genres:
            item = results.get(genre, {})
            if item.get("error"):
                continue
            cache_store.write_genre(genre, item["docs"], note=item.get("note", ""))
        clear_index()
        return True
    except Exception:
        return False


_auto_ingest()

if not require_login(LEVEL_CHAT):
    st.stop()

# ── カスタムCSS ─────────────────────────────────────────────
st.markdown("""
<style>
    /* バッジ */
    .brand-badge {
        display: inline-block;
        background: #1b3a5c;
        color: #fff;
        font-size: 0.75rem;
        font-weight: 600;
        padding: 3px 12px;
        border-radius: 4px;
        margin-left: 10px;
        vertical-align: middle;
    }
    /* メインヘッダー */
    .main-header {
        font-size: 1.6rem;
        font-weight: 700;
        color: #1b3a5c;
        margin-bottom: 2px;
    }
    .main-subtitle {
        font-size: 0.85rem;
        color: #666;
        margin-bottom: 4px;
    }
    /* 使い方ボックス */
    .usage-box {
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 16px 20px;
        margin: 12px 0;
    }
    .usage-box h4 {
        color: #b8860b;
        font-size: 0.95rem;
        margin-bottom: 8px;
    }
    .usage-box p {
        font-size: 0.85rem;
        color: #333;
        line-height: 1.7;
        margin: 0;
    }
    /* 緑帯 */
    .green-bar {
        background: #2e5c3e;
        color: #fff;
        font-size: 0.8rem;
        font-weight: 600;
        padding: 8px 14px;
        border-radius: 6px;
        margin: 14px 0 6px 0;
    }
    /* サイドバー ブランドボタン */
    .sidebar-brand-active {
        background: #1b3a5c !important;
        color: #fff !important;
        border: none !important;
        font-weight: 600 !important;
    }
</style>
""", unsafe_allow_html=True)

# ── サイドバー ───────────────────────────────────────────
st.sidebar.markdown("### 🖊 AIアシスタント")
st.sidebar.caption("別途業務サポートツール")
st.sidebar.divider()

logout_button(LEVEL_CHAT)

ui.sidebar_footer(config)

# ── メインヘッダー ────────────────────────────────────────
st.markdown(
    f'<div class="main-header">☎️ コールセンター AIアシスタント'
    f'<span class="brand-badge">{BRAND_NAME}</span></div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="main-subtitle">パートスタッフ向け質問サポート ｜ 知識ベースを参照して正確に回答します</div>',
    unsafe_allow_html=True,
)

# 知識ベース最終更新
ui.freshness_notice(config, CHAT_GENRES)
latest = cache_store.latest_timestamp(CHAT_GENRES) if hasattr(cache_store, "latest_timestamp") else None
if latest:
    st.caption(f"📚 知識ベース最終更新: {latest}")

ui.config_warnings(config)

# ── 使い方 ────────────────────────────────────────────────
st.markdown("""
<div class="usage-box">
    <h4>🔖 使い方</h4>
    <p>
        下の入力欄に質問を入力して送信してください。返品・解約・キャンペーン・料金など、業務に関わることをなんでも聞けます。<br>
        ⚠️ 知識ベースに記載がない場合は「記載がありません」とお伝えし、SVへの確認を促します。
    </p>
</div>
""", unsafe_allow_html=True)

# ── 商品名で絞り込み ──────────────────────────────────────
st.markdown('<div class="green-bar">🎯 商品名で絞り込むと、回答精度が上がります</div>', unsafe_allow_html=True)

selected_product = st.selectbox(
    "商品名で絞り込み",
    options=["指定なし"] + PRODUCT_NAMES,
    index=0,
    label_visibility="collapsed",
)

# ── 検索設定（内部で使用、UIには非表示）────────────────────
selected_genres = CHAT_GENRES
use_expansion = True
top_k = config.top_k

# ── 会話履歴 ─────────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state["messages"] = []

for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            ui.render_answer(
                message.get("content", ""),
                message.get("details", ""),
                message.get("caution", ""),
            )
        if message.get("hits"):
            ui.render_sources(message["hits"])

question = st.chat_input("質問を入力してください（例：返品の手続きを教えて）…")

if question:
    # 商品名が選択されていればクエリに付加
    if selected_product and selected_product != "指定なし":
        full_query = f"【{selected_product}】{question}"
    else:
        full_query = question

    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("資料を探しています…"):
            product_name = selected_product if selected_product != "指定なし" else ""
            retrieval = retrieve(
                config,
                full_query,
                genres=selected_genres,
                top_k=top_k,
                use_expansion=use_expansion,
                product_filter=product_name,
            )

        if retrieval.expanded_terms:
            st.caption("追加で探した言葉: " + "、".join(retrieval.expanded_terms))

        if not retrieval.hits:
            conclusion = "該当する記載が資料内に見つかりませんでした。"
            caution = "言い方を変えて検索するか、上長・本社に確認してください。"
            ui.render_answer(conclusion, "", caution)
            usage_log.record(
                feature="chat",
                question=question,
                answered=False,
                genres=selected_genres,
                note="検索ヒットなし",
            )
            st.session_state["messages"].append(
                {"role": "assistant", "content": conclusion, "details": "", "caution": caution, "hits": []}
            )
            # スプレッドシートにログ保存
            if config.has_sheets:
                append_log(
                    config.service_account_info,
                    question=question,
                    answer=conclusion,
                    caution=caution,
                    answered=False,
                    product=selected_product if selected_product != "指定なし" else "",
                )
        else:
            with st.spinner("答えを作っています…"):
                result = llm.answer_question(config, question, retrieval.hits)

            if not result.ok:
                conclusion = result.failure_message
                details = ""
                caution = "下の参考資料をご自身で確認してください。"
                st.error(result.failure_message)
                answered = False
            else:
                data = result.data
                answered = bool(data.get("has_answer"))
                conclusion = str(data.get("answer", "")).strip()
                details = str(data.get("details", "")).strip()
                caution = str(data.get("caution", "")).strip()

                if not answered:
                    conclusion = "資料内に該当する記載が見つかりませんでした。" + (
                        f" {conclusion}" if conclusion else ""
                    )

                ui.render_answer(conclusion, details, caution)

            cited_ids = set(str(i) for i in (result.data.get("used_doc_ids", []) if result.ok else []))
            cited = [h for h in retrieval.hits if h.id in cited_ids] or retrieval.hits
            ui.render_sources(cited)

            usage_log.record(
                feature="chat",
                question=question,
                answered=answered,
                genres=selected_genres,
                top_score=retrieval.top_score,
                doc_ids=[h.id for h in cited],
                note=result.stop_reason,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                yen=result.yen,
            )
            st.session_state["messages"].append(
                {
                    "role": "assistant",
                    "content": conclusion,
                    "details": details,
                    "caution": caution,
                    "hits": cited,
                }
            )
            # スプレッドシートにログ保存
            if config.has_sheets:
                source_titles = " / ".join(h.title for h in cited[:5])
                append_log(
                    config.service_account_info,
                    question=question,
                    answer=conclusion,
                    details=details,
                    caution=caution,
                    answered=answered,
                    product=selected_product if selected_product != "指定なし" else "",
                    sources=source_titles,
                    model=result.model,
                )

# ── フィードバックボタン ─────────────────────────────────────
if st.session_state["messages"] and st.session_state["messages"][-1]["role"] == "assistant":
    last_msg = st.session_state["messages"][-1]
    # 直前の質問を取得
    last_question = ""
    for m in reversed(st.session_state["messages"]):
        if m["role"] == "user":
            last_question = m["content"]
            break

    fb_key = f"fb_{len(st.session_state['messages'])}"
    already_sent = st.session_state.get(f"{fb_key}_sent", False)

    if not already_sent:
        fb_cols = st.columns(4)
        fb_resolved = fb_cols[0].button("✅ 解決", key=f"{fb_key}_resolved", use_container_width=True)
        fb_unresolved = fb_cols[1].button("❌ 未解決", key=f"{fb_key}_unresolved", use_container_width=True)
        fb_deepdive = fb_cols[2].button("🔍 さらに深ぼる", key=f"{fb_key}_deepdive", use_container_width=True)
        fb_newq = fb_cols[3].button("💬 別の質問をする", key=f"{fb_key}_newq", use_container_width=True)

        fb_value = ""
        if fb_resolved:
            fb_value = "解決"
        elif fb_unresolved:
            fb_value = "未解決"
        elif fb_deepdive:
            fb_value = "さらに深ぼる"
        elif fb_newq:
            fb_value = "別の質問をする"

        if fb_value:
            # 未解決の場合はコメント入力を促す（次回表示時）
            if fb_value == "未解決":
                st.session_state[f"{fb_key}_pending"] = fb_value
                st.rerun()
            else:
                if config.has_sheets:
                    append_feedback(
                        config.service_account_info,
                        question=last_question,
                        answer=last_msg.get("content", ""),
                        feedback=fb_value,
                        product=selected_product if selected_product != "指定なし" else "",
                    )
                st.session_state[f"{fb_key}_sent"] = True
                st.success(f"フィードバック「{fb_value}」を送信しました。")
                st.rerun()

    # 未解決のコメント入力
    if st.session_state.get(f"{fb_key}_pending") == "未解決" and not already_sent:
        with st.form(f"{fb_key}_comment_form"):
            comment = st.text_area("どこが解決しなかったか教えてください（任意）", height=80, key=f"{fb_key}_comment")
            submitted = st.form_submit_button("送信")
        if submitted:
            if config.has_sheets:
                append_feedback(
                    config.service_account_info,
                    question=last_question,
                    answer=last_msg.get("content", ""),
                    feedback="未解決",
                    comment=comment,
                    product=selected_product if selected_product != "指定なし" else "",
                )
            st.session_state[f"{fb_key}_sent"] = True
            del st.session_state[f"{fb_key}_pending"]
            st.success("フィードバックを送信しました。")
            st.rerun()

    if already_sent:
        st.caption("✔ フィードバック送信済み")

if st.session_state["messages"]:
    if st.button("会話をリセット"):
        st.session_state["messages"] = []
        # FB関連のsession_stateもクリア
        for key in list(st.session_state.keys()):
            if key.startswith("fb_"):
                del st.session_state[key]
        st.rerun()
