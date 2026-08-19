"""ログイン・アクセス制限（設計書 5.1）。

1つのアプリにいろいろな機能を詰め込むので、機能ごとに必要なログインレベルを
分けています。「誰でも使ってよい質問応答」と「お客様の生データを扱う機能」では
求めるべき安全性が違うためです。

パスワードはコードの中に直接書かず、Secrets / 環境変数から読みます。
"""

from __future__ import annotations

import hmac

import streamlit as st

from .config import secret

# 機能ごとのログインレベル
LEVEL_CHAT = "chat"    # オペレーター向け。共有パスワードのみ（空なら認証なし）
LEVEL_ADMIN = "admin"  # 資料の再取得・利用状況・個人情報の点検。必須

_LABELS = {
    LEVEL_CHAT: "チャット画面",
    LEVEL_ADMIN: "管理画面",
}


def _expected_password(level: str) -> str:
    return str(secret(f"passwords.{level}", "") or "")


def is_authenticated(level: str) -> bool:
    if not _expected_password(level):
        # パスワード未設定 = 認証なしで通す（チャット画面のみ想定）
        return level == LEVEL_CHAT
    return bool(st.session_state.get(f"auth_{level}", False))


def require_login(level: str) -> bool:
    """ログインしていなければ入力欄を出して False を返す。

    使い方:
        if not require_login(LEVEL_ADMIN):
            st.stop()
    """
    expected = _expected_password(level)
    label = _LABELS.get(level, level)

    if not expected:
        if level == LEVEL_CHAT:
            return True
        st.error(
            f"{label}のパスワードが設定されていません。"
            " Secrets の [passwords] に値を入れてください（5.1）。"
        )
        return False

    if st.session_state.get(f"auth_{level}", False):
        return True

    st.subheader(f"🔒 {label}")
    st.caption("この画面はパスワードでログインしないと使えません。")
    with st.form(f"login_{level}"):
        entered = st.text_input("パスワード", type="password", key=f"pw_input_{level}")
        submitted = st.form_submit_button("ログイン")
    if submitted:
        # タイミング攻撃を避けるため compare_digest を使う
        if hmac.compare_digest(entered, expected):
            st.session_state[f"auth_{level}"] = True
            st.rerun()
        else:
            st.error("パスワードが違います。")
    return False


def logout_button(level: str) -> None:
    if st.session_state.get(f"auth_{level}", False):
        if st.sidebar.button("ログアウト", key=f"logout_{level}"):
            st.session_state[f"auth_{level}"] = False
            st.rerun()
