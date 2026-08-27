"""Google スプレッドシートからの取得（設計書 4.1 / 5.2 / 5.4 / 6.1 / 8.1）。

対応しているシートの形は2つです。

  layout = "table"      ふつうの表。1行目が列名、1行が1件。
                        例）回覧板・施策シートリンク・全商材価格表

  layout = "key_value"  縦持ち。A列に項目名、その右の列に内容。
                        例）商材シート（爽軽青汁・メグレアpremium など）
                        内容がB列の商材とC列の商材が混在しているため、
                        value_columns の左から順に見て、最初に値があるものを使います。

守っていること:
  5.2  columns に書いた列だけを読み込む。お客様氏名や電話番号がそのまま入って
       いる列は、そもそもアプリ側で読み込まない設計にする。
  5.4  取り出す条件に「どの会社・ブランドか」の一致を含め、アプリ側でも
       想定外の識別番号のデータは除外する（二重チェック）。
  6.1  date_column / max_age_days で、古くなった情報を取り込まない。
  8.1  「セルの文字の色や太字」で意味を表しているシートがある。read_formatting を
       true にすると、色・太字の情報も一緒に取得してAIに渡せるようにする。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from ..config import AppConfig, SourceConfig


class SheetsError(RuntimeError):
    pass


# ── 列の指定（列名でも "A" のような位置でも書ける） ─────────
_COLUMN_LETTER = re.compile(r"^[A-Z]{1,3}$")


def _letter_to_index(letter: str) -> int:
    """"A" -> 0, "B" -> 1, "AA" -> 26"""
    value = 0
    for ch in letter:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value - 1


def resolve_column(spec: str, header: list[str]) -> int | None:
    """列の指定を、0始まりの列番号に変換する。

    列名（"共有日"）が優先。見つからず "A" のような形なら位置として扱う。
    """
    spec = spec.strip()
    if not spec:
        return None
    if spec in header:
        return header.index(spec)
    upper = spec.upper()
    if _COLUMN_LETTER.match(upper):
        return _letter_to_index(upper)
    return None


def _cell(row: list[str], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return row[index].strip()


# ── 日付（6.1 古い情報を除外する） ─────────────────────────
_DATE_PATTERNS = ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日", "%y/%m/%d", "%Y/%m", "%Y.%m.%d")


def parse_date(value: str) -> date | None:
    """シートによって書き方が違うので、よくある形をひととおり試す。"""
    text = (value or "").strip()
    if not text:
        return None
    text = text.split(" ")[0].split("～")[0].split("〜")[0].strip()
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    # 「2026/8/6(火)」のような書き方から数字だけ拾う
    numbers = re.findall(r"\d+", text)
    if len(numbers) >= 3:
        try:
            year = int(numbers[0])
            if year < 100:
                year += 2000
            return date(year, int(numbers[1]), int(numbers[2]))
        except ValueError:
            return None
    return None


def is_too_old(value: str, max_age_days: int, today: date | None = None) -> bool:
    """日付が読み取れて、かつ古すぎる場合だけ True。

    日付が空・読み取れない行は「除外しない」（消しすぎないため）。
    """
    parsed = parse_date(value)
    if parsed is None:
        return False
    return parsed < (today or date.today()) - timedelta(days=max_age_days)


# ── Google への接続 ────────────────────────────────────────
def _client(config: AppConfig):
    if not config.service_account_info:
        raise SheetsError(
            "Google のサービスアカウント情報が設定されていません。"
            " Secrets の [gcp_service_account] を埋めてください。"
        )
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:  # pragma: no cover
        raise SheetsError(
            "gspread / google-auth が入っていません。pip install -r requirements.txt を実行してください。"
        ) from exc

    # このアプリはスプレッドシートを読むだけなので、読み取り専用の権限にする。
    # 万一アプリ側に不具合があっても、元のシートを書き換えることはできません。
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(dict(config.service_account_info), scopes=scopes)
    return gspread.authorize(creds)


def _sheet_url(spreadsheet_id: str, gid: int | None, row: int) -> str:
    base = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
    if gid is not None:
        return f"{base}#gid={gid}&range=A{row}"
    return f"{base}#range=A{row}"


# ── 8.1 セルの書式 ────────────────────────────────────────
def _format_hint(fmt: dict[str, Any] | None) -> str:
    if not fmt:
        return ""
    hints: list[str] = []
    text_fmt = fmt.get("textFormat") or {}
    if text_fmt.get("bold"):
        hints.append("太字")
    fg = text_fmt.get("foregroundColor") or {}
    if fg and any(round(float(fg.get(c, 0)), 2) > 0 for c in ("red", "green", "blue")):
        hints.append(
            "文字色rgb({:.2f},{:.2f},{:.2f})".format(
                float(fg.get("red", 0)), float(fg.get("green", 0)), float(fg.get("blue", 0))
            )
        )
    bg = fmt.get("backgroundColor") or {}
    if bg and not all(round(float(bg.get(c, 1)), 2) == 1.0 for c in ("red", "green", "blue")):
        hints.append(
            "背景色rgb({:.2f},{:.2f},{:.2f})".format(
                float(bg.get("red", 1)), float(bg.get("green", 1)), float(bg.get("blue", 1))
            )
        )
    return "／".join(hints)


def _fetch_formatting(client, source: SourceConfig) -> dict[tuple[int, int], str]:
    spreadsheet = client.open_by_key(source.spreadsheet_id)
    meta = spreadsheet.fetch_sheet_metadata(
        params={
            "includeGridData": True,
            "fields": (
                "sheets(properties(title,sheetId),data(rowData(values("
                "userEnteredFormat(textFormat(bold,foregroundColor),backgroundColor)))))"
            ),
        }
    )
    result: dict[tuple[int, int], str] = {}
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("title") != source.worksheet:
            continue
        for grid in sheet.get("data", []):
            for r_idx, row in enumerate(grid.get("rowData", []), start=1):
                for c_idx, cell in enumerate(row.get("values", []), start=1):
                    hint = _format_hint(cell.get("userEnteredFormat"))
                    if hint:
                        result[(r_idx, c_idx)] = hint
    return result


# ── 資料1件を組み立てる ───────────────────────────────────
def _make_doc(
    *,
    source: SourceConfig,
    row_number: int,
    title: str,
    body: str,
    tenant_id: str,
    gid: int | None,
) -> dict[str, Any]:
    return {
        # 同じジャンルに複数シートを割り当てるので、シート名をIDに含める
        "id": f"{source.genre}-{source.slug}-{row_number}",
        "genre": source.genre,
        "source_name": source.label,
        "title": title,
        "body": body,
        "row": row_number,
        # 8.3 対策②：参照元はAIに書かせず、検索結果側に情報として持たせておく
        "source_label": f"{source.label}／{source.worksheet} {row_number}行目",
        "source_url": _sheet_url(source.spreadsheet_id, gid, row_number),
        "tenant_id": tenant_id,
        "meta": {"layout": source.layout},
    }


# ── チャンキング（長いテキストを意味のあるまとまりで分割） ──

# この文字数を超えたドキュメントを分割対象にする
_CHUNK_THRESHOLD = 400

# 分割後の1チャンクの最大文字数（目安）
_CHUNK_MAX = 400

# 見出しパターン（これらで区切る）
_HEADING_RE = re.compile(
    r"(?:^|\n)"
    r"(?:"
    r"[■▼▲●◆★☆【〈＜]"  # 見出し記号で始まる行
    r"|(?:━|─|ー{3,}|={3,}|-{3,})"  # 区切り線
    r"|(?:\d+[\.\)）])"  # 番号付き見出し（1. 2） など）
    r")"
)


def _chunk_doc(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """長いドキュメントを見出し区切りで分割する。

    短いドキュメントはそのまま1件で返す。
    見出しが見つからない場合も分割しない。
    """
    body = doc.get("body", "")
    if len(body) <= _CHUNK_THRESHOLD:
        return [doc]

    # 見出し位置で分割
    splits = list(_HEADING_RE.finditer(body))
    if not splits:
        return [doc]

    # 分割位置のリスト
    positions = [0]
    for m in splits:
        pos = m.start()
        # 改行の直後から始まる場合は改行位置を使う
        if pos > 0 and body[pos] == "\n":
            pos += 1
        if pos > 0 and pos not in positions:
            positions.append(pos)
    positions.append(len(body))

    # チャンクを作成
    chunks: list[dict[str, Any]] = []
    base_title = doc.get("title", "")
    current_text = ""

    for i in range(len(positions) - 1):
        segment = body[positions[i]:positions[i + 1]].strip()
        if not segment:
            continue

        # 短いセグメントは結合する
        if current_text and len(current_text) + len(segment) <= _CHUNK_MAX:
            current_text += "\n" + segment
            continue

        # 前のチャンクを確定
        if current_text:
            chunk_title = _extract_chunk_title(current_text, base_title)
            chunk = dict(doc)
            chunk["id"] = f"{doc['id']}-c{len(chunks)}"
            chunk["title"] = chunk_title
            chunk["body"] = current_text
            chunks.append(chunk)

        current_text = segment

    # 最後のチャンク
    if current_text:
        chunk_title = _extract_chunk_title(current_text, base_title)
        chunk = dict(doc)
        chunk["id"] = f"{doc['id']}-c{len(chunks)}"
        chunk["title"] = chunk_title
        chunk["body"] = current_text
        chunks.append(chunk)

    return chunks if chunks else [doc]


def _extract_chunk_title(text: str, base_title: str) -> str:
    """チャンクの先頭行から見出しを抽出してタイトルに使う。"""
    first_line = text.split("\n")[0].strip()
    # 見出し記号を除去して短いタイトルにする
    cleaned = re.sub(r"^[■▼▲●◆★☆【〈＜\d\.\)）\s]+", "", first_line)
    cleaned = re.sub(r"[】〉＞]\s*$", "", cleaned)
    if cleaned and len(cleaned) <= 50:
        return f"{base_title}／{cleaned}"
    return base_title


# ── layout = "table" ──────────────────────────────────────
def _read_table(
    source: SourceConfig,
    raw_rows: list[list[str]],
    config: AppConfig,
    gid: int | None,
    format_hints: dict[tuple[int, int], str],
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    header_index = max(source.header_row - 1, 0)
    if header_index >= len(raw_rows):
        return [], ["列名の行が見つかりませんでした。"]
    header = [h.strip() for h in raw_rows[header_index]]

    wanted = list(source.columns)
    if source.tenant_column and source.tenant_column not in wanted:
        wanted.append(source.tenant_column)
    if source.date_column and source.date_column not in wanted:
        wanted.append(source.date_column)

    index_of: dict[str, int] = {}
    missing: list[str] = []
    for spec in wanted:
        resolved = resolve_column(spec, header)
        if resolved is None:
            missing.append(spec)
        else:
            index_of[spec] = resolved
    if missing:
        notes.append(f"見つからない列: {', '.join(missing)}")

    def label_of(spec: str) -> str:
        if spec in source.column_labels:
            return source.column_labels[spec]
        idx = index_of.get(spec)
        if idx is not None and idx < len(header) and header[idx]:
            return header[idx]
        return spec

    fill_down = {s: index_of[s] for s in source.fill_down_columns if s in index_of}
    carried: dict[str, str] = {}

    docs: list[dict[str, Any]] = []
    skipped_tenant = 0
    skipped_old = 0

    for row_number, row in enumerate(raw_rows[header_index + 1 :], start=header_index + 2):
        values: dict[str, str] = {spec: _cell(row, idx) for spec, idx in index_of.items()}

        # セル結合などで空になっている列を、上の行の値で埋める（商材名など）
        for spec in fill_down:
            if values.get(spec):
                carried[spec] = values[spec]
            elif carried.get(spec):
                values[spec] = carried[spec]

        # 6.1 古い情報は取り込まない
        if source.date_column and source.max_age_days:
            if is_too_old(values.get(source.date_column, ""), source.max_age_days):
                skipped_old += 1
                continue

        # 5.4 テナントIDの一致を条件に含める + アプリ側でも二重チェック
        row_tenant = config.tenant_id
        if source.tenant_column:
            row_tenant = values.get(source.tenant_column, "")
            if row_tenant != config.tenant_id or row_tenant not in config.allowed_tenant_ids:
                skipped_tenant += 1
                continue
            values.pop(source.tenant_column, None)

        if not any(values.values()):
            continue

        title_parts = [values.get(c, "") for c in source.title_columns if values.get(c)]
        title = " ".join(title_parts).strip() or f"{source.label} {row_number}行目"

        body_lines: list[str] = []
        for spec in source.body_columns:
            value = values.get(spec, "")
            if value:
                body_lines.append(f"{label_of(spec)}: {value}")

        hints = "／".join(
            hint
            for spec, idx in index_of.items()
            if (hint := format_hints.get((row_number, idx + 1)))
        )
        if hints:
            body_lines.append(f"（セルの書式: {hints}）")

        if not body_lines:
            continue

        doc = _make_doc(
            source=source,
            row_number=row_number,
            title=title,
            body="\n".join(body_lines),
            tenant_id=row_tenant,
            gid=gid,
        )
        docs.extend(_chunk_doc(doc))

    if skipped_old:
        notes.append(f"{source.max_age_days}日より古い行を{skipped_old}件除外")
    if skipped_tenant:
        notes.append(f"他ブランドの行を{skipped_tenant}件除外")
    return docs, notes


# ── layout = "key_value" ──────────────────────────────────
def _read_key_value(
    source: SourceConfig,
    raw_rows: list[list[str]],
    config: AppConfig,
    gid: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """A列の項目名と、その右の列の内容を、1行=1件として読む。

    商材シートは内容がB列のものとC列のものが混在しているので、
    value_columns の左から見て最初に値があるものを採用します。
    """
    key_index = _letter_to_index(source.key_column.upper()) if _COLUMN_LETTER.match(
        source.key_column.upper()
    ) else 0
    value_indexes = [
        _letter_to_index(c.upper()) for c in source.value_columns if _COLUMN_LETTER.match(c.upper())
    ] or [1, 2, 3]

    skip = set(source.skip_keys)
    docs: list[dict[str, Any]] = []
    empty_keys = 0

    # 商材シートは1行目がバナー（見出し・各種リンク）なので start_row=2 で読み飛ばす
    start = max(source.start_row - 1, 0)

    for row_number, row in enumerate(raw_rows[start:], start=start + 1):
        key = _cell(row, key_index)
        if not key or key in skip:
            continue
        value = ""
        for idx in value_indexes:
            candidate = _cell(row, idx)
            if candidate:
                value = candidate
                break
        if not value:
            # 「販売内容」のような見出しだけの行。内容が無いので資料にしない
            empty_keys += 1
            continue

        doc = _make_doc(
            source=source,
            row_number=row_number,
            title=f"{source.label}／{key}",
            body=f"商品: {source.label}\n{key}: {value}",
            tenant_id=config.tenant_id,
            gid=gid,
        )
        docs.extend(_chunk_doc(doc))

    notes = [f"内容が空の項目を{empty_keys}件除外"] if empty_keys else []
    return docs, notes


# ── 取得の入口 ────────────────────────────────────────────
def fetch_source(config: AppConfig, source: SourceConfig) -> tuple[list[dict[str, Any]], str]:
    """1つのシートを取得して、キャッシュ用の形に整える。"""
    client = _client(config)
    try:
        spreadsheet = client.open_by_key(source.spreadsheet_id)
        worksheet = spreadsheet.worksheet(source.worksheet)
    except Exception as exc:
        raise SheetsError(f"シートを開けませんでした（{source.label} / {source.worksheet}）: {exc}") from exc

    raw_rows = worksheet.get_all_values()
    if not raw_rows:
        return [], "シートが空でした。"

    gid = getattr(worksheet, "id", None)

    if source.is_key_value:
        docs, notes = _read_key_value(source, raw_rows, config, gid)
    else:
        format_hints: dict[tuple[int, int], str] = {}
        if source.read_formatting:
            try:
                format_hints = _fetch_formatting(client, source)
            except Exception:
                format_hints = {}
        docs, notes = _read_table(source, raw_rows, config, gid, format_hints)

    return docs, " / ".join(notes)


def fetch_all(config: AppConfig, genres: Iterable[str] | None = None) -> dict[str, Any]:
    """ジャンルごとに、割り当てられた全シートを取得して1つにまとめる。

    1ジャンルに複数シートを割り当てられるので、ここで統合します。
    （4.1 の「全部作り直す処理」に相当。部分的な追記を足す場合は、
      必ずこのあとに実行してください。先に追記すると消えます。）
    """
    target = set(genres) if genres is not None else None
    result: dict[str, dict[str, Any]] = {}

    for source in config.sources:
        if target is not None and source.genre not in target:
            continue
        bucket = result.setdefault(source.genre, {"docs": [], "note": "", "error": None, "notes": []})
        try:
            docs, note = fetch_source(config, source)
        except SheetsError as exc:
            bucket["notes"].append(f"{source.label}: {exc}")
            bucket["error"] = "; ".join(bucket["notes"])
            continue
        bucket["docs"].extend(docs)
        detail = f"{source.label} {len(docs)}件"
        if note:
            detail += f"（{note}）"
        bucket["notes"].append(detail)

    for bucket in result.values():
        bucket["note"] = " / ".join(bucket["notes"])
    return result


# 書き込み（update_cell）はありません。
# 資料の修正はスプレッドシート側で行い、このアプリは読むだけです。
