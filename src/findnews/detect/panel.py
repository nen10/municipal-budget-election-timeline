"""パネル(自治体 × 年度 × 議員)の構築。

自治体側の値は verify.py(各自治体を自分の過去とだけ比べる時系列記録)を使う。他自治体との比較は行わない。
議員側は選挙結果(election_results)のうち politicians.csv に登録された候補。
"""

from __future__ import annotations

import sqlite3

import pandas as pd


def politician_panel(conn: sqlite3.Connection, group: str) -> pd.DataFrame:
    """選挙 group(例: shugiin_20260208)で、登録議員が立候補した選挙区の全自治体(選挙区内の部分)の行。

    columns: election_id, election_date, district, code, politician_id, candidate_name, nomination, result_label,
             votes, share, rank, top_candidate, top_nomination, top_share, opposed_locally, margin_share, source_url
    """
    df = pd.read_sql_query(
        """SELECT r.election_id, e.election_date, e.district, r.municipality_code AS code, r.candidate_name,
                  r.politician_id, r.nomination, r.result_label, SUM(r.votes) AS votes, MAX(r.source_url) AS source_url
           FROM election_results r JOIN elections e USING(election_id)
           WHERE r.election_id LIKE ? AND r.municipality_code IS NOT NULL
           GROUP BY r.election_id, r.municipality_code, r.candidate_name""", conn, params=(group + "_%",))
    if df.empty:
        return df
    g = df.groupby(["election_id", "code"])
    df["total"] = g["votes"].transform("sum")
    df["share"] = df["votes"] / df["total"]
    df["rank"] = g["votes"].rank(ascending=False, method="min").astype(int)
    top = df.loc[g["votes"].idxmax(), ["election_id", "code", "candidate_name", "nomination", "share"]].rename(
        columns={"candidate_name": "top_candidate", "nomination": "top_nomination", "share": "top_share"})
    df = df.merge(top, on=["election_id", "code"])
    df["opposed_locally"] = df["candidate_name"] != df["top_candidate"]
    df["margin_share"] = df["share"] - df["top_share"]
    return df[df.politician_id.notna()].reset_index(drop=True)
