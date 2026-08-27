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

st.title("☎️ コールセンターAIアシスタント")
st.caption("質問を入力すると、社内の資料から関係する情報を探して答えます。")

if not require_login(LEVEL_CHAT):
    st.stop()
logout_button(LEVEL_CHAT)

ui.config_warnings(config)
ui.freshness_notice(config, CHAT_GENRES)

# ── サイドバー ───────────────────────────────────────────
st.sidebar.header("検索の設定")
selected_genres = st.sidebar.multiselect(
    "探す資料の種類",
    options=CHAT_GENRES,
    default=CHAT_GENRES,
    format_func=lambda g: GENRES.get(g, g),
)
use_expansion = st.sidebar.checkbox(
    "言い換えも探す",
    value=True,
    help="「返品」と「返送」のように、言い方が違っても見つかるようにします（4.3）。",
)
top_k = st.sidebar.slider("AIに渡す資料の数", 3, 12, config.top_k)
ui.sidebar_footer(config)

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

question = st.chat_input("例：定期便を2回で解約したいと言われました。どうすればいいですか？")

if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("資料を探しています…"):
            retrieval = retrieve(
                config,
                question,
                genres=selected_genres or CHAT_GENRES,
                top_k=top_k,
                use_expansion=use_expansion,
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
        else:
            with st.spinner("答えを作っています…"):
                result = llm.answer_question(config, question, retrieval.hits)

            if not result.ok:
                # 4.4 途中で打ち切られた場合などは、結果を使わず安全な表示に切り替える
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

            # 8.3 対策②：参照元はAIに書かせず、検索結果側の情報を機械的に表示する
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

if st.session_state["messages"]:
    if st.button("会話をリセット"):
        st.session_state["messages"] = []
        st.rerun()
