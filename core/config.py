"""設定の読み込み。

秘密情報（APIキー・パスワード）はコードに書かず、
Streamlit の Secrets / 環境変数 / .streamlit/secrets.toml から読みます（設計書 5.3）。

バッチ処理は Streamlit の外で動くので、streamlit が import できなくても
動くようにしてあります。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
SAMPLE_DIR = DATA_DIR / "sample"
LOG_DIR = DATA_DIR / "logs"
SECRETS_PATH = BASE_DIR / ".streamlit" / "secrets.toml"

# ジャンルと日本語名
GENRES: dict[str, str] = {
    "campaign": "キャンペーン・施策",
    "manual": "マニュアル",
    "price": "料金",
    "product": "商品",
}

# 3.1 抜き出すときの優先順位（キャンペーン > マニュアル > 料金 > 商品）
GENRE_PRIORITY: dict[str, float] = {
    "campaign": 1.30,
    "manual": 1.15,
    "price": 1.00,
    "product": 0.90,
}

# チャット画面で検索する対象
CHAT_GENRES = ["campaign", "manual", "price", "product"]

# シートの形
LAYOUT_TABLE = "table"          # 1行目が列名・1行1件（ふつうの表）
LAYOUT_KEY_VALUE = "key_value"  # A列に項目名・右の列に内容（縦持ち）


def _load_toml() -> dict[str, Any]:
    if SECRETS_PATH.exists():
        with SECRETS_PATH.open("rb") as f:
            return tomllib.load(f)
    return {}


@lru_cache(maxsize=1)
def _raw_secrets() -> dict[str, Any]:
    """Streamlit Secrets を優先し、無ければローカルの secrets.toml を読む。"""
    try:
        import streamlit as st

        if hasattr(st, "secrets") and len(st.secrets) > 0:
            result: dict[str, Any] = {}
            for k in st.secrets:
                v = st.secrets[k]
                # AttrDict等のネストされたオブジェクトをdictに変換
                if hasattr(v, "to_dict"):
                    result[k] = v.to_dict()
                elif hasattr(v, "keys"):
                    result[k] = dict(v)
                else:
                    result[k] = v
            return result
        return _load_toml()
    except Exception:
        return _load_toml()


def secret(path: str, default: Any = None) -> Any:
    """"llm.effort" のようにドット区切りで設定値を取り出す。

    環境変数が優先されます（例: llm.effort → LLM_EFFORT）。
    """
    env_key = path.replace(".", "_").upper()
    if env_key in os.environ:
        return os.environ[env_key]

    node: Any = _raw_secrets()
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            try:  # st.secrets の入れ子は dict ではない場合がある
                node = node[part]
            except Exception:
                return default
    return node


@dataclass(frozen=True)
class SourceConfig:
    """1つのシートの取得設定。

    1つのジャンルに複数のシートを割り当てられます（取り込み時に統合されます）。
    """

    genre: str
    spreadsheet_id: str
    worksheet: str
    # 資料の表示名。省略時はシート名
    name: str = ""
    # シートの形。table（ふつうの表）または key_value（A列に項目名・縦持ち）
    layout: str = LAYOUT_TABLE

    # ── layout = "table" のとき ─────────────────────────
    # 5.2 使う列だけをあらかじめ決めておき、それ以外は最初から読み込まない。
    #     列名（"共有日"）でも、列の位置（"A" "AB"）でも指定できます。
    #     同じ列名が何度も出てくるシートでは、位置で指定してください。
    columns: list[str] = field(default_factory=list)
    title_columns: list[str] = field(default_factory=list)
    body_columns: list[str] = field(default_factory=list)
    # 列名が何行目にあるか（説明文が上にあるシート用）
    header_row: int = 1
    # セル結合などで空になっている列を、上の値で埋める（商材名の列など）
    fill_down_columns: list[str] = field(default_factory=list)
    # 位置で指定した列に付ける表示名 {"E": "初回金額(クレカ)"}
    column_labels: dict[str, str] = field(default_factory=dict)

    # ── layout = "key_value" のとき ─────────────────────
    key_column: str = "A"
    # 内容が入っている列の候補。左から見て最初に値があるものを使う
    # （商材によって B 列だったり C 列だったりするため）
    value_columns: list[str] = field(default_factory=lambda: ["B", "C", "D"])
    # 読み飛ばす項目名（画像欄など）
    skip_keys: list[str] = field(default_factory=list)
    # 何行目から読み始めるか。商材シートは1行目がバナー（見出し・リンク）なので 2 にする
    start_row: int = 1

    # ── 共通 ────────────────────────────────────────────
    # 5.4 複数会社が混在するシートのテナント列
    tenant_column: str | None = None
    # 8.1 セルの色・太字も意味を持つ場合
    read_formatting: bool = False
    # 6.1 古い情報を除外する（例: date_column="共有日", max_age_days=180）
    date_column: str | None = None
    max_age_days: int | None = None

    @property
    def label(self) -> str:
        return self.name or self.worksheet

    @property
    def genre_label(self) -> str:
        return GENRES.get(self.genre, self.genre)

    @property
    def is_key_value(self) -> bool:
        return self.layout == LAYOUT_KEY_VALUE

    @property
    def slug(self) -> str:
        """資料IDに使う短い識別子。"""
        return "".join(ch for ch in self.worksheet if ch.isalnum())[:24] or "sheet"


@dataclass(frozen=True)
class AppConfig:
    anthropic_api_key: str
    answer_model: str
    expansion_model: str
    effort: str
    answer_max_tokens: int
    tenant_id: str
    allowed_tenant_ids: list[str]
    cache_ttl_hours: float
    original_term_weight: float
    expanded_term_weight: float
    top_k: int
    sources: list[SourceConfig]
    service_account_info: dict[str, Any] | None

    @property
    def has_llm(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_sheets(self) -> bool:
        return bool(self.service_account_info) and bool(self.sources)

    def sources_for(self, genre: str) -> list[SourceConfig]:
        return [s for s in self.sources if s.genre == genre]

    def source_for(self, genre: str) -> SourceConfig | None:
        found = self.sources_for(genre)
        return found[0] if found else None

    @property
    def configured_genres(self) -> list[str]:
        seen: list[str] = []
        for s in self.sources:
            if s.genre not in seen:
                seen.append(s.genre)
        return seen


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _as_dict(value: Any) -> dict[str, str]:
    if not value:
        return {}
    try:
        return {str(k): str(v) for k, v in dict(value).items()}
    except Exception:
        return {}


def _parse_sources(raw: Any) -> list[SourceConfig]:
    sources: list[SourceConfig] = []
    for item in raw or []:
        try:
            genre = str(item["genre"]).strip()
            if genre not in GENRES:
                continue
            layout = str(item.get("layout", LAYOUT_TABLE)).strip() or LAYOUT_TABLE
            columns = _as_list(item.get("columns"))
            if layout == LAYOUT_TABLE and not columns:
                continue

            max_age = item.get("max_age_days")
            sources.append(
                SourceConfig(
                    genre=genre,
                    spreadsheet_id=str(item.get("spreadsheet_id", "")).strip(),
                    worksheet=str(item.get("worksheet", "")).strip(),
                    name=str(item.get("name", "")).strip(),
                    layout=layout,
                    columns=columns,
                    title_columns=_as_list(item.get("title_columns")) or columns[:1],
                    body_columns=_as_list(item.get("body_columns")) or columns[1:],
                    header_row=int(item.get("header_row", 1)),
                    fill_down_columns=_as_list(item.get("fill_down_columns")),
                    column_labels=_as_dict(item.get("column_labels")),
                    key_column=str(item.get("key_column", "A")).strip() or "A",
                    value_columns=_as_list(item.get("value_columns")) or ["B", "C", "D"],
                    skip_keys=_as_list(item.get("skip_keys")),
                    start_row=int(item.get("start_row", 1)),
                    tenant_column=(str(item["tenant_column"]) if item.get("tenant_column") else None),
                    read_formatting=bool(item.get("read_formatting", False)),
                    date_column=(str(item["date_column"]) if item.get("date_column") else None),
                    max_age_days=(int(max_age) if max_age not in (None, "") else None),
                )
            )
        except Exception:
            continue
    return sources


def load_config() -> AppConfig:
    tenant_id = str(secret("tenant.id", "default"))
    allowed = _as_list(secret("tenant.allowed_ids")) or [tenant_id]

    # サービスアカウント情報を取得（Streamlit Cloud / ローカル両対応）
    sa_info: dict[str, Any] | None = None
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            sa_info = dict(st.secrets["gcp_service_account"])
    except Exception:
        pass
    if sa_info is None:
        sa = secret("gcp_service_account")
        if sa:
            try:
                sa_info = dict(sa) if hasattr(sa, "keys") else None
            except Exception:
                sa_info = None
    if sa_info and (not sa_info.get("client_email") or not sa_info.get("private_key")):
        sa_info = None

    return AppConfig(
        anthropic_api_key=str(secret("anthropic_api_key", os.environ.get("ANTHROPIC_API_KEY", "")) or ""),
        answer_model=str(secret("llm.answer_model", "claude-sonnet-5")),
        expansion_model=str(secret("llm.expansion_model", "claude-haiku-4-5")),
        effort=str(secret("llm.effort", "medium")),
        answer_max_tokens=int(secret("llm.answer_max_tokens", 8000)),
        tenant_id=tenant_id,
        allowed_tenant_ids=allowed,
        cache_ttl_hours=float(secret("cache.ttl_hours", 24)),
        original_term_weight=float(secret("search.original_term_weight", 1.0)),
        expanded_term_weight=float(secret("search.expanded_term_weight", 0.35)),
        top_k=int(secret("search.top_k", 6)),
        sources=_parse_sources(secret("sources")),
        service_account_info=sa_info,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
