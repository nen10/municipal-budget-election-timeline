"""発言キーワード一致の集計(設計書 5.3)。取得・本文一致の判定は fetch/kokkai.py と manual.py で行う。"""

from __future__ import annotations

import sqlite3

import pandas as pd


def load_statements(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT id, source, speaker, speaker_group, politician_id, date, meeting, matched_keywords,
                  target_municipalities, source_url, body
           FROM statements WHERE matched_keywords IS NOT NULL AND matched_keywords <> ''""", conn)


def by_politician(st: pd.DataFrame, politician_id: str) -> pd.DataFrame:
    return st[st.politician_id == politician_id]


def mentioning(st: pd.DataFrame, code: str) -> pd.DataFrame:
    return st[st.target_municipalities.fillna("").str.split(",").apply(lambda xs: code in xs)]


def excerpt(body: str, keywords: str, width: int = 80) -> str:
    """一致キーワードの前後の原文(改変しない抜き出し)。全文は DB の statements.body にある。"""
    body = (body or "").replace("\r", "").replace("\n", " ")
    for kw in (keywords or "").split("|"):
        i = body.find(kw)
        if i >= 0:
            s, e = max(0, i - width), min(len(body), i + len(kw) + width)
            return ("…" if s else "") + body[s:e] + ("…" if e < len(body) else "")
    return body[: width * 2]
