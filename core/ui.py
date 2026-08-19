"""画面まわりの共通部品。

参照元の表示は、AIに書かせず、検索結果が持っている情報を
プログラム側が機械的に表示します（設計書 8.3 対策②）。
"""

from __future__ import annotations

import html
import re

import streamlit as st

from . import cache_store
from .config import GENRES, AppConfig
from .search import Hit

PAGE_ICON = "☎️"


def page_setup(title: str, icon: str = PAGE_ICON) -> None:
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")


# 結論の先頭に付きがちな装飾記号（AIがうっかり付けた場合に落とす）
_LEAD_MARK = re.compile(r"^\s*(?:[-*・･]|\d+[.)])\s*")
_EMPHASIS = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def plain_text(text: str) -> str:
    """結論用に、マークダウンの装飾記号を落としてプレーンな文にする。

    結論はHTMLとして描画する（大きく見せるため）ので、
    「**強調**」のような記号が残ると、記号がそのまま画面に出てしまいます。
    """
    # 強調記号を先に外す。先頭記号を先に消すと「**太字**」の片方だけが
    # 落ちて「*太字**」のように壊れる。
    cleaned = _EMPHASIS.sub(lambda m: m.group(1) or m.group(2), str(text or "").strip())
    cleaned = _LEAD_MARK.sub("", cleaned)
    return cleaned.replace("`", "")


def render_answer(conclusion: str, details: str = "", caution: str = "") -> None:
    """AIの回答を描画する。

    チャットの実行時と、あとから会話履歴を再表示するときの両方で、
    必ずこの関数を通します。片方だけ別の書き方をすると、履歴の側で
    HTMLタグがそのまま文字として出るような食い違いが起きます。
    """
    if conclusion:
        # 結論は最初に、大きく。保留中のお客様を待たせないため、
        # ここだけ読めば次の行動が決まるようにする。
        # AIの文章をそのままHTMLに入れないよう、必ずエスケープする。
        st.markdown(
            "<div style='font-size:1.15rem;line-height:1.7;font-weight:600;"
            "padding:2px 0 6px 0;'>"
            + html.escape(plain_text(conclusion)).replace("\n", "<br>")
            + "</div>",
            unsafe_allow_html=True,
        )
    if caution:
        st.warning(caution)
    # 条件・例外は最初から開いておく。金額・日数・例外といった、
    # 実際の応対で必要になる情報がここに入るため。
    if details:
        with st.expander("条件・詳細", expanded=True):
            st.markdown(details)


def render_sources(hits: list[Hit], *, title: str = "参考にした資料", expanded: bool = False) -> None:
    """答えの元になった資料へのリンクを表示する（3.1）。

    オペレーターさんが「本当にそう書いてあるか」を自分の目で確認できるようにします。
    """
    if not hits:
        return
    with st.expander(f"📎 {title}（{len(hits)}件）", expanded=expanded):
        for hit in hits:
            label = GENRES.get(hit.genre, hit.genre)
            st.markdown(f"**{hit.title}** ｜ {label}")
            st.caption(f"{hit.source_label}（一致度 {hit.score:.2f}）")
            if hit.source_url:
                st.markdown(f"[スプレッドシートで開く]({hit.source_url})")
            body = str(hit.doc.get("body", ""))
            st.text(body[:800] + ("…" if len(body) > 800 else ""))
            st.divider()


def freshness_notice(config: AppConfig, genres: list[str]) -> None:
    """資料が古くなっていないかの表示（6.1）。"""
    stale = [g for g in genres if cache_store.is_stale(g, config.cache_ttl_hours)]
    samples = [g for g in genres if cache_store.read_genre(g).get("is_sample")]
    if samples:
        st.info(
            "現在はサンプルデータで動いています。"
            " Secrets にスプレッドシートの設定を入れて `python -m batch.ingest` を実行すると、"
            " 実際の資料に切り替わります。"
        )
    elif stale:
        names = "、".join(GENRES.get(g, g) for g in stale)
        st.warning(
            f"{names} の資料が{config.cache_ttl_hours:.0f}時間以上更新されていません。"
            " 管理画面から「今すぐ資料を取り直す」を実行できます。"
        )


def config_warnings(config: AppConfig) -> None:
    if not config.has_llm:
        st.error(
            "Anthropic の APIキーが設定されていません。"
            " Secrets に anthropic_api_key を入れてください。AIの回答生成は使えません。"
        )


def sidebar_footer(config: AppConfig) -> None:
    st.sidebar.divider()
    st.sidebar.caption(f"ブランド: {config.tenant_id}")
    st.sidebar.caption(f"回答AI: {config.answer_model}")
    st.sidebar.caption(f"検索: キーワード検索（bi-gram BM25）")
