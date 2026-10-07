"""アダプタのインターフェイス(DESIGN.md 14.2)。

IndicatorSource: 指標(補助金・交付金・決算)を都道府県単位で取得し、Observation(13.2 の 3 つの日付つき)を返す。
ElectionSource : 都道府県ごとの選管の得票資料。都道府県ごとに実装してレジストリに登録する。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Document:
    url: str
    filename: str
    fiscal_year: int | None = None
    published_date: str | None = None
    retrieved_at: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class Observation:
    pref_code: str
    municipality_code: str
    indicator_id: str
    period_start: str
    period_end: str
    decided_date: str | None
    decided_date_is_proxy: bool
    decided_date_basis: str
    published_date: str | None
    value: float | None            # None = 未取得
    unit: str
    count: int | None = None
    missing_reason: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None
    generated_by: str = ""


@dataclass
class ElectionDoc:
    key: str                        # 例: r08syugi
    url: str
    filename: str
    kind: str                       # results / candidates


@runtime_checkable
class IndicatorSource(Protocol):
    source_id: str
    indicator_ids: tuple[str, ...]
    coverage: str                   # "national" | "prefecture"

    def list_documents(self, pref_code: str, years: list[int]) -> list[Document]: ...

    def fetch(self, doc: Document, dest: Path) -> Path: ...

    def parse(self, path: Path, pref_code: str) -> list[Observation]: ...


@runtime_checkable
class ElectionSource(Protocol):
    pref_code: str

    def list_elections(self) -> list[ElectionDoc]: ...

    def fetch(self, doc: ElectionDoc, dest: Path) -> Path: ...

    def parse(self, path: Path) -> tuple[list[dict], list[dict], list[dict]]: ...


def fy_period(fy: int) -> tuple[str, str]:
    return f"{fy}-04-01", f"{fy + 1}-03-31"
