"""チャットの質問・回答・フィードバックをGoogle スプレッドシートに自動保存する。

保存先シート:
  「AIログ」— 質問・回答の記録
  「FB」— オペレーターからのフィードバック
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ログ専用スプレッドシート
LOG_SPREADSHEET_ID = "15tx3NQZwlu0vnF-riLDO72oXt_nqD4cTpLEVh3RwMn4"
LOG_SHEET_NAME = "AIログ"
FB_SHEET_NAME = "FB"

# ヘッダー行
HEADERS = [
    "日時",
    "商品名",
    "質問",
    "回答",
    "詳細",
    "注意",
    "回答有無",
    "参照資料",
    "モデル",
]


FB_HEADERS = [
    "日時",
    "商品名",
    "質問",
    "回答（先頭100字）",
    "評価",
    "コメント",
]


def _get_or_create_sheet(gc: Any, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> Any:
    """指定シートを取得。なければヘッダー付きで自動作成する。"""
    book = gc.open_by_key(spreadsheet_id)
    try:
        ws = book.worksheet(sheet_name)
    except Exception:
        ws = book.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
    return ws


def append_log(
    service_account_info: dict[str, Any],
    *,
    question: str,
    answer: str,
    details: str = "",
    caution: str = "",
    answered: bool = False,
    product: str = "",
    sources: str = "",
    model: str = "",
) -> bool:
    """質問・回答を1行追記する。失敗してもアプリを止めない。"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
            ],
        )
        gc = gspread.authorize(creds)
        ws = _get_or_create_sheet(gc, LOG_SPREADSHEET_ID, LOG_SHEET_NAME, HEADERS)

        now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            now,
            product,
            question[:1000],
            answer[:2000],
            details[:2000],
            caution[:500],
            "Yes" if answered else "No",
            sources[:1000],
            model,
        ]
        ws.append_row(row, value_input_option="RAW")
        return True
    except Exception:
        return False


def append_feedback(
    service_account_info: dict[str, Any],
    *,
    question: str,
    answer: str,
    feedback: str,
    comment: str = "",
    product: str = "",
) -> bool:
    """フィードバックを1行追記する。失敗してもアプリを止めない。"""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        gc = gspread.authorize(creds)
        ws = _get_or_create_sheet(gc, LOG_SPREADSHEET_ID, FB_SHEET_NAME, FB_HEADERS)

        now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        row = [
            now,
            product,
            question[:500],
            answer[:100],
            feedback,
            comment[:500],
        ]
        ws.append_row(row, value_input_option="RAW")
        return True
    except Exception:
        return False
