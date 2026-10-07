"""SQLite スキーマと接続。"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

from .. import config

TABLES = [
    "municipalities", "municipality_fiscal", "politicians", "positions", "elections",
    "election_results", "endorsements", "subsidy_programs", "subsidy_allocations",
    "requests", "statements", "cases", "signals", "fetch_log", "observations", "events",
]


def schema_sql() -> str:
    return resources.files("findnews.db").joinpath("schema.sql").read_text(encoding="utf-8")


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or config.DEFAULT_DB)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(schema_sql())
    conn.commit()


def log_fetch(conn: sqlite3.Connection, source: str, step: str, status: str,
              detail: str = "", source_url: str | None = None) -> None:
    from ..http import now_iso
    conn.execute(
        "INSERT INTO fetch_log(source, step, status, detail, source_url, retrieved_at) VALUES (?,?,?,?,?,?)",
        (source, step, status, detail[:2000], source_url, now_iso()),
    )
    conn.commit()


def ensure_municipalities(conn: sqlite3.Connection) -> None:
    """内蔵の栃木県マスタを municipalities に投入(人口・財政力指数は決算カード取込時に更新)。"""
    from ..municipalities import TOCHIGI, PREF_NAMES, kind
    src = "https://www.soumu.go.jp/denshijiti/code.html"
    for code, name in TOCHIGI.items():
        conn.execute(
            """INSERT INTO municipalities(code, name, pref_code, pref_name, kind, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,NULL)
               ON CONFLICT(code) DO NOTHING""",
            (code, name, code[:2], PREF_NAMES.get(code[:2]), kind(name), src),
        )
    conn.commit()
