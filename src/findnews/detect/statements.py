"""発言キーワード一致の集計(設計書 5.3)。取得・本文一致の判定は fetch/kokkai.py と manual.py で行う。"""

from __future__ import annotations

import sqlite3

import pandas as pd


NOT_INDEPENDENT = "由来事例のため独立検証にならない"


def load_statements(conn: sqlite3.Connection) -> pd.DataFrame:
    """一致した発言。キーワードの由来事例(config/keywords.yaml の origin_case_id)に関わる議員・自治体の発言では、
    そのキーワードを derived_keywords に分け、independent_keywords には残りだけを入れる。"""
    import json

    from ..keywords import ORIGIN
    st = pd.read_sql_query(
        """SELECT id, source, speaker, speaker_group, politician_id, date, meeting, matched_keywords,
                  target_municipalities, source_url, body
           FROM statements WHERE matched_keywords IS NOT NULL AND matched_keywords <> ''""", conn)
    cases = {r[0]: (set(json.loads(r[1] or "[]")), set(json.loads(r[2] or "[]")))
             for r in conn.execute("SELECT case_id, politicians, municipalities FROM cases")}
    derived, indep, note = [], [], []
    for r in st.itertuples():
        d, i, n = [], [], []
        targets = set((r.target_municipalities or "").split(",")) - {""}
        for kw in (r.matched_keywords or "").split("|"):
            cid = ORIGIN.get(kw, {}).get("origin_case_id")
            pols, munis = cases.get(cid, (set(), set())) if cid else (set(), set())
            if cid and ((r.politician_id and r.politician_id in pols) or (targets & munis)):
                d.append(kw)
                n.append(f"「{kw}」は事例 {cid} から追加したキーワード({NOT_INDEPENDENT})")
            else:
                i.append(kw)
        derived.append("|".join(d)); indep.append("|".join(i)); note.append(" / ".join(n))
    st["derived_keywords"], st["independent_keywords"], st["origin_note"] = derived, indep, note
    return st


def independent(st: pd.DataFrame) -> pd.DataFrame:
    """由来事例への一致を除いた発言(スコアの発言一致に数えるもの)。"""
    return st[st.independent_keywords.fillna("") != ""] if "independent_keywords" in st else st


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
