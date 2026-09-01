"""チャットの質問・回答をGoogle スプレッドシートに自動保存する。

保存先シート名: 「AIログ」（なければ自動作成）
列構成: 日時 / 商品名 / 質問 / 回答 / 詳細 / 注意 / 回答有無 / 参照資料 / モデル
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# ログ用シート名
LOG_SHEET_NAME = "AIログ"

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


def _get_or_create_sheet(gc: Any, spreadsheet_id: str) -> Any:
    """ログ用シートを取得。なければヘッダー付きで自動作成する。"""
    book = gc.open_by_key(spreadsheet_id)
    try:
        ws = book.worksheet(LOG_SHEET_NAME)
    except Exception:
        ws = book.add_worksheet(title=LOG_SHEET_NAME, rows=1000, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="RAW")
    return ws


def append_log(
    service_account_info: dict[str, Any],
    spreadsheet_id: str,
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
        ws = _get_or_create_sheet(gc, spreadsheet_id)

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
