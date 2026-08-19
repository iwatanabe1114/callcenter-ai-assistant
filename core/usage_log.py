"""使われ方の見える化（設計書 6.3）。

「どんな質問が来て、答えが見つかったかどうか」を記録しておくと、
後から「どんな資料が足りていないか」「どのくらい使われているか」を
把握して改善に活かせます。

質問文はマスキングしてから保存します（5.2）。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import LOG_DIR
from .masking import mask_text

_LOG_PATH = LOG_DIR / "usage.jsonl"


def record(
    *,
    feature: str,
    question: str,
    answered: bool,
    genres: list[str] | None = None,
    top_score: float = 0.0,
    doc_ids: list[str] | None = None,
    note: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    yen: float = 0.0,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "feature": feature,
        "question": mask_text(question)[:500],
        "answered": bool(answered),
        "genres": genres or [],
        "top_score": round(float(top_score), 4),
        "doc_ids": doc_ids or [],
        "note": note,
        "model": model,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "yen": round(float(yen), 4),
    }
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load(limit: int = 2000) -> list[dict[str, Any]]:
    if not _LOG_PATH.exists():
        return []
    lines = _LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(entries)
    answered = sum(1 for e in entries if e.get("answered"))
    by_day = Counter(str(e.get("at", ""))[:10] for e in entries if e.get("at"))
    unanswered = [e for e in entries if not e.get("answered")]
    total_yen = sum(float(e.get("yen", 0) or 0) for e in entries)
    by_model = Counter(e.get("model", "-") for e in entries if e.get("model"))
    return {
        "件数": total,
        "回答できた件数": answered,
        "累計費用(円)": round(total_yen, 1),
        "1件あたり費用(円)": round(total_yen / total, 2) if total else 0.0,
        "モデル別件数": dict(by_model),
        "回答率": round(answered / total * 100, 1) if total else 0.0,
        "日別件数": dict(sorted(by_day.items())),
        "答えが見つからなかった質問": [e.get("question", "") for e in unanswered][-50:],
        "機能別": dict(Counter(e.get("feature", "-") for e in entries)),
    }


def path() -> Path:
    return _LOG_PATH
