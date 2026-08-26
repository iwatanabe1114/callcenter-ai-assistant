"""インシデント報告（対応記録テンプレート）。

OPがリピートプラスに貼り付ける対応記録を、テンプレートから作成する。
カテゴリ→テンプレート選択→商品名・日付を入力→コピーして貼り付け。
Claude APIは使用しない。
"""

from __future__ import annotations

import streamlit as st

from core import ui
from core.auth import LEVEL_CHAT, logout_button, require_login
from core.config import get_config
from core.incident import (
    fill_content,
    fill_title,
    get_categories,
    load_cache,
)

ui.page_setup("インシデント報告")

config = get_config()

st.title("📝 インシデント報告")
st.caption(
    "テンプレートを選んで対応記録を作成します。"
    "完成したらコピーしてリピートプラスに貼り付けてください。"
)

if not require_login(LEVEL_CHAT):
    st.stop()
logout_button(LEVEL_CHAT)

# ── テンプレート読み込み ──────────────────────────────────────
templates = load_cache()
if not templates:
    st.warning(
        "インシデントテンプレートが読み込まれていません。"
        " 管理画面の「資料の状態」タブから「インシデントテンプレートを取り直す」を実行してください。"
    )
    st.stop()

# ── 商品名リスト（設定済みの商品ソースから取得） ──────────────
product_names = [s.name for s in config.sources if s.genre == "product" and s.name]
if not product_names:
    product_names = ["（商品名を入力）"]

product = st.selectbox("商品名", options=product_names, index=None, placeholder="-- 選択してください --")
if not product:
    product = ""

ui.sidebar_footer(config)

# ── カテゴリ選択（「その他」はVOCで細分化） ──────────────────
display_categories: list[str] = []
for cat in get_categories(templates):
    if cat == "その他":
        vocs_seen: list[str] = []
        for t in templates:
            if t.category == "その他" and t.voc and t.voc not in vocs_seen:
                vocs_seen.append(t.voc)
        display_categories.extend(vocs_seen)
    else:
        display_categories.append(cat)

selected_display_cat = st.selectbox("対応カテゴリ", display_categories, index=None, placeholder="-- 選択してください --")

# カテゴリに該当するテンプレートを絞り込む
cat_templates: list = []
if selected_display_cat:
    original_categories = get_categories(templates)
    if selected_display_cat in original_categories:
        cat_templates = [t for t in templates if t.category == selected_display_cat]
    else:
        cat_templates = [t for t in templates if t.category == "その他" and t.voc == selected_display_cat]

# ── テンプレート選択 ─────────────────────────────────────────
selected = None
if cat_templates:
    raw_titles = [fill_title(t.title, product) for t in cat_templates]
    template_titles = []
    for i, t in enumerate(cat_templates):
        title = raw_titles[i]
        if "初回キャンセル" in title and raw_titles.count(title) > 1:
            if "回数便" in t.retention_result or "回数便" in t.content:
                title = f"{title}（回数便）"
            else:
                title = f"{title}（通常便）"
        template_titles.append(title)
    selected_idx = st.selectbox(
        "テンプレート",
        range(len(cat_templates)),
        index=None,
        format_func=lambda i: template_titles[i],
        placeholder="-- 選択してください --",
    )
    if selected_idx is not None:
        selected = cat_templates[selected_idx]
else:
    st.selectbox("テンプレート", [], placeholder="-- 選択してください --", disabled=True)

# ── 注文状況 ─────────────────────────────────────────────────
ORDER_STATUS_OPTIONS = ["F1", "F2", "F3", "F4~", "発送前"]
order_status = st.selectbox("注文状況", ORDER_STATUS_OPTIONS, index=None, placeholder="-- 選択してください --", key="order_status_select")

# ── 継続応援結果 ─────────────────────────────────────────────
RETENTION_OPTIONS = [
    "通常便成功", "通常便失敗", "通常便未案内",
    "回数便成功（未満了）", "回数便失敗（未満了）", "回数便未案内（未満了）",
    "回数便成功（満了）", "回数便失敗（満了）", "回数便未案内（満了）",
]
retention_result = st.selectbox("継続応援結果", RETENTION_OPTIONS, index=None, placeholder="-- 選択してください --", key="retention_result_select")

# ── 解約希望理由 ─────────────────────────────────────────────
CANCEL_REASON_OPTIONS = [
    "定期認識なし", "注文認識なし", "変更認識なし", "お試し", "未開封",
    "体に合わない", "味が苦手", "病院通院/薬服用のため", "効果がなかった",
    "経済的理由", "余った", "他商品へ切替", "満足した", "商品未着",
    "理由不明", "その他",
    "※ ドクターストップ", "※ 長期不在", "※ 代理入電", "※ 処理ミス",
    "※ 蕁麻疹", "※ 冒頭切電", "※ 消費者センター発言", "※ カスハラ該当",
]
cancel_reason = st.selectbox("解約希望理由", CANCEL_REASON_OPTIONS, index=None, placeholder="-- 選択してください --", key="cancel_reason_select")

# ── 継続応援成功内訳（成功 + 解約希望理由選択時のみ） ────────
SUCCESS_DETAIL_OPTIONS = [
    "変更なし", "サイクル・袋数変更", "スキップ・延長の提案",
    "1.500pt利用", "ルール上での阻止", "発送15日前メール",
    "新規ポイント利用", "15日分プレゼント", "送料無料",
    "コース切替", "ランク制度", "Lightへ切替", "該当なし",
]
success_detail = None
if retention_result and "成功" in retention_result and cancel_reason:
    success_detail = st.selectbox("継続応援成功内訳", SUCCESS_DETAIL_OPTIONS, index=None, placeholder="-- 選択してください --", key="success_detail_select")

# ── 付帯情報の表示 ──────────────────────────────────────────
if selected and selected.voc:
    st.caption(f"VOC: {selected.voc}")

st.divider()

# ── 編集エリア ──────────────────────────────────────────────
filled_title = fill_title(selected.title, product) if selected else ""

# 内容ヘッダー組み立て
content_header = f"商品名：{product}\nカテゴリ：{selected_display_cat or ''}\n注文状況：{order_status or ''}\n"
if retention_result or cancel_reason or success_detail:
    content_header += "\n"
    if retention_result:
        content_header += f"継続応援結果：{retention_result}\n"
    if cancel_reason:
        content_header += f"解約希望理由：{cancel_reason}\n"
    if success_detail:
        content_header += f"継続応援成功内訳：{success_detail}\n"
content_header += "\n"

template_body = fill_content(selected.content, product) if selected else ""
filled_content = content_header + template_body

st.subheader("タイトル")
title_text = st.text_input(
    "タイトル", value=filled_title, label_visibility="collapsed"
)

st.subheader("内容")
st.caption("○月○日 や【詳細】以下を書き換えてください。")
content_text = st.text_area(
    "内容", value=filled_content, height=300, label_visibility="collapsed"
)

# ── 内部メモ ────────────────────────────────────────────────
st.subheader("内部メモ")
memo_text = st.text_area(
    "内部メモ", height=120, placeholder="対応の詳細や申し送り事項を記入", label_visibility="collapsed", key="memo_area"
)

# ── コピー・リセットボタン ──────────────────────────────────
st.divider()

# ボタンのスタイル
col1, col2, col3 = st.columns(3)
col1.button("タイトルをコピー", key="copy_title", type="primary", use_container_width=True)
col2.button("内容をコピー", key="copy_content", type="primary", use_container_width=True)
col3.button("内部メモをコピー", key="copy_memo", type="primary", use_container_width=True)

# Streamlitにはクリップボード操作がないため、st.codeで表示してコピー可能にする
if st.session_state.get("copy_title"):
    st.code(title_text, language=None)
if st.session_state.get("copy_content"):
    full_content = content_text
    if memo_text.strip():
        full_content += f"\n\n【内部メモ】\n{memo_text.strip()}"
    st.code(full_content, language=None)
if st.session_state.get("copy_memo"):
    st.code(memo_text, language=None)

if st.button("リセット", type="secondary", use_container_width=True, key="reset_btn"):
    for key in ["order_status_select", "retention_result_select", "cancel_reason_select", "success_detail_select", "memo_area"]:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()
