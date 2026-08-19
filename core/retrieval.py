"""検索の入口（設計書 6.2 遅延読み込み・並列処理）。

・索引づくりは重い処理なので、アプリ起動時に無条件で走らせず、
  実際に検索しようとしたタイミングで初めて作ります（遅延読み込み）。
  一度作った索引はキャッシュが更新されるまで使い回します。
・お互いに関係のない処理（クエリ拡張のAI呼び出しと、索引づくり）は
  順番に1つずつではなく、同時に走らせて待ち時間を短くします（並列処理）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import streamlit as st

from . import cache_store
from .config import CHAT_GENRES, AppConfig
from .query_expansion import expand_query
from .search import Hit, SearchIndex, build_index, split_query, weighted_terms


@st.cache_resource(show_spinner=False)
def _index_for(genres_key: str, signature: str) -> SearchIndex:
    """索引を作る。signature（キャッシュ更新の目印）が変わったら作り直される。"""
    genres = genres_key.split(",")
    return build_index(cache_store.load_docs(genres))


def get_index(genres: list[str]) -> SearchIndex:
    return _index_for(",".join(genres), cache_store.cache_signature())


def clear_index() -> None:
    """資料を取り直した直後に呼ぶ。次の検索で索引が作り直される。"""
    _index_for.clear()


@dataclass
class Retrieval:
    hits: list[Hit]
    original_terms: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    terms: dict[str, float] = field(default_factory=dict)

    @property
    def top_score(self) -> float:
        return self.hits[0].score if self.hits else 0.0

    @property
    def doc_ids(self) -> list[str]:
        return [h.id for h in self.hits]


def retrieve(
    config: AppConfig,
    question: str,
    *,
    genres: list[str] | None = None,
    top_k: int | None = None,
    use_expansion: bool = True,
    use_genre_priority: bool = True,
) -> Retrieval:
    """質問文から関連資料を探す。"""
    target_genres = genres or CHAT_GENRES
    if not question.strip():
        return Retrieval(hits=[])

    # 6.2 索引づくりとAI呼び出しを同時に走らせる
    with ThreadPoolExecutor(max_workers=2) as pool:
        index_future = pool.submit(get_index, target_genres)
        expansion_future = (
            pool.submit(expand_query, config, question)
            if (use_expansion and config.has_llm)
            else None
        )
        index = index_future.result()
        expanded = expansion_future.result() if expansion_future else []

    original = split_query(question)
    terms = weighted_terms(
        original,
        expanded,
        original_weight=config.original_term_weight,
        expanded_weight=config.expanded_term_weight,
    )

    hits = index.search(
        terms,
        top_k=top_k or config.top_k,
        genres=target_genres,
        use_genre_priority=use_genre_priority,
    )
    return Retrieval(hits=hits, original_terms=original, expanded_terms=expanded, terms=terms)


def stale_genres(config: AppConfig, genres: list[str] | None = None) -> list[str]:
    """更新が古くなっているジャンルを返す（6.1）。"""
    return [g for g in (genres or CHAT_GENRES) if cache_store.is_stale(g, config.cache_ttl_hours)]


def index_stats(genres: list[str]) -> dict[str, Any]:
    index = get_index(genres)
    return {"件数": index.n, "平均トークン数": round(index.avg_len, 1)}
