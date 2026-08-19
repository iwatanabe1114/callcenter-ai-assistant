"""キャッシュ（設計書 2.1 / 4.1 / 6.1）。

毎回スプレッドシートに取りに行くと遅く、アクセス制限も受けるので、
定期的にまとめて取得し、検索しやすい形（JSON）で保存しておきます。

保存の順番に注意（4.1）：
    ジャンルごとに別ファイルへ書くので、あるジャンルの再取得が
    他のジャンルを消すことはありません。
    「全部作り直す処理」は rebuild_all() として最初にまとめて実行し、
    その後に個別の追記を行ってください。逆順にすると追加分が消えます。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, GENRES, SAMPLE_DIR

# キャッシュに入る1件分の形
#   id / genre / title / body / source_label / source_url / row / tenant_id / meta


def _cache_path(genre: str) -> Path:
    return CACHE_DIR / f"{genre}.json"


def _sample_path(genre: str) -> Path:
    return SAMPLE_DIR / f"{genre}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_genre(genre: str, docs: list[dict[str, Any]], note: str = "") -> Path:
    """1ジャンル分を書き出す。書き込み中に壊れないよう一時ファイル経由で置き換える。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "genre": genre,
        "fetched_at": now_iso(),
        "count": len(docs),
        "note": note,
        "docs": docs,
    }
    target = _cache_path(genre)
    fd, tmp = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target


def read_genre(genre: str) -> dict[str, Any]:
    """1ジャンル分を読む。キャッシュが無ければサンプルデータで代用する。"""
    path = _cache_path(genre)
    if not path.exists():
        path = _sample_path(genre)
    if not path.exists():
        return {"genre": genre, "fetched_at": None, "count": 0, "docs": [], "is_sample": True}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    data["is_sample"] = not _cache_path(genre).exists()
    return data


def load_docs(genres: list[str]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    for genre in genres:
        docs.extend(read_genre(genre).get("docs", []))
    return docs


def cache_status() -> list[dict[str, Any]]:
    """管理画面用：ジャンルごとの件数・取得時刻・サンプルかどうか。"""
    rows = []
    for genre, label in GENRES.items():
        data = read_genre(genre)
        rows.append(
            {
                "genre": genre,
                "ジャンル": label,
                "件数": data.get("count", len(data.get("docs", []))),
                "最終取得": data.get("fetched_at") or "-",
                "元データ": "サンプル" if data.get("is_sample") else "スプレッドシート",
                "備考": data.get("note", ""),
            }
        )
    return rows


def is_stale(genre: str, ttl_hours: float) -> bool:
    """取得から ttl_hours 以上経っていれば True。"""
    fetched = read_genre(genre).get("fetched_at")
    if not fetched:
        return True
    try:
        dt = datetime.fromisoformat(fetched)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age_hours >= ttl_hours


def cache_signature() -> str:
    """キャッシュが更新されたかどうかを判定するための短い文字列。

    検索インデックスのキャッシュキーに使います（更新されたら作り直す）。
    """
    parts = []
    for genre in GENRES:
        path = _cache_path(genre)
        if path.exists():
            stat = path.stat()
            parts.append(f"{genre}:{int(stat.st_mtime)}:{stat.st_size}")
        else:
            sample = _sample_path(genre)
            parts.append(f"{genre}:sample:{int(sample.stat().st_mtime) if sample.exists() else 0}")
    return "|".join(parts)
