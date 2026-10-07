"""発言キーワード一致の集計(設計書 5.3)。取得・本文一致の判定は fetch/kokkai.py で行う。"""

from __future__ import annotations

import sqlite3

import pandas as pd


def load_statements(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT id, source, speaker, politician_id, date, meeting, matched_keywords, target_municipalities, source_url,
                  substr(body, 1, 4000) AS body
           FROM statements WHERE matched_keywords IS NOT NULL AND matched_keywords <> ''""", conn)


def by_politician(st: pd.DataFrame, politician_id: str) -> pd.DataFrame:
    return st[st.politician_id == politician_id]


def mentioning(st: pd.DataFrame, code: str) -> pd.DataFrame:
    return st[st.target_municipalities.fillna("").str.split(",").apply(lambda xs: code in xs)]


def snippet(body: str, keywords: str, width: int = 60) -> str:
    """一致キーワードの前後だけを抜き出す(全文転載を避ける)。"""
    body = (body or "").replace("\r", "").replace("\n", " ")
    for kw in (keywords or "").split("|"):
        i = body.find(kw)
        if i >= 0:
            s, e = max(0, i - width), min(len(body), i + len(kw) + width)
            return ("…" if s else "") + body[s:e] + ("…" if e < len(body) else "")
    return body[: width * 2]
