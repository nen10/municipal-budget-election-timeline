"""指標系列とパネル(自治体 × 年度 × 議員)の構築。

指標(metric)は「出典:項目」の形で表す。金額の単位は千円。
  card:*            総務省 決算カード(決算額、年度末で確定)
  tokko:交付総額     総務省 特別交付税 報道発表(市のみ。町は個別額が公表されていない)
  tokko:3月交付額
  mlit:road_maint    国交省 道路メンテナンス事業(事業主体が当該市町のもの、当初配分)
  mlit:sole_grants   国交省 社総交+防安交のうち当該市町が単独で策定主体の計画(当初配分)
  mlit:joint_plans   国交省 社総交+防安交で当該市町が策定主体に含まれる共同計画の数(件数、金額ではない)
"""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

CARD_METRICS = ["国庫支出金", "都道府県支出金", "特別交付税", "普通建設事業費_うち補助", "土木費"]

# 指標の「決定(確定)時期」: fiscal_year から決定日を返す。選挙前後の判定に使う。
def decision_date(metric: str, fy: int) -> date:
    if metric.startswith("mlit:"):
        return date(fy, 4, 1)          # 当初配分は 4 月上旬に公表
    if metric == "tokko:12月交付額":
        return date(fy, 12, 1)
    if metric.startswith("tokko:"):
        return date(fy + 1, 3, 15)     # 3 月分の決定(交付総額は 3 月分で確定)
    return date(fy + 1, 3, 31)         # 決算: 年度末


def metric_series(conn: sqlite3.Connection, pref_code: str = "09") -> pd.DataFrame:
    """columns: code, fiscal_year, metric, value, source_url"""
    frames = []
    q = f"""SELECT code, fiscal_year, item, value, source_url FROM municipality_fiscal
            WHERE source='soumu_card' AND code LIKE '{pref_code}%' AND item IN ({','.join('?' * len(CARD_METRICS))})"""
    df = pd.read_sql_query(q, conn, params=CARD_METRICS)
    if not df.empty:
        df["metric"] = "card:" + df.pop("item")
        frames.append(df)

    df = pd.read_sql_query(
        f"""SELECT municipality_code AS code, fiscal_year, item_name, amount_thousand_yen AS value, source_url
            FROM subsidy_allocations WHERE program_id='soumu_tokko' AND municipality_code LIKE '{pref_code}%'""", conn)
    if not df.empty:
        df["metric"] = "tokko:" + df.pop("item_name")
        frames.append(df[df.metric.isin(["tokko:交付総額", "tokko:3月交付額"])])

    codes = [r[0] for r in conn.execute("SELECT code FROM municipalities WHERE pref_code=?", (pref_code,))]
    mlit_years = [r[0] for r in conn.execute(
        "SELECT DISTINCT fiscal_year FROM subsidy_allocations WHERE program_id LIKE 'mlit_%'")]
    if mlit_years:
        base = pd.MultiIndex.from_product([codes, sorted(mlit_years)], names=["code", "fiscal_year"]).to_frame(index=False)
        src = pd.read_sql_query(
            "SELECT fiscal_year, MIN(source_url) AS source_url FROM subsidy_allocations WHERE program_id LIKE 'mlit_%' GROUP BY 1", conn)
        rm = pd.read_sql_query(
            """SELECT municipality_code AS code, fiscal_year, SUM(amount_thousand_yen) AS value FROM subsidy_allocations
               WHERE program_id='mlit_road_maintenance' AND attribution='sole' GROUP BY 1,2""", conn)
        sg = pd.read_sql_query(
            """SELECT municipality_code AS code, fiscal_year, SUM(amount_thousand_yen) AS value FROM subsidy_allocations
               WHERE program_id IN ('mlit_shasoukou','mlit_bouan') AND attribution='sole' GROUP BY 1,2""", conn)
        joint = pd.read_sql_query(
            """SELECT recipient_codes, fiscal_year FROM subsidy_allocations
               WHERE program_id IN ('mlit_shasoukou','mlit_bouan') AND attribution='joint'""", conn)
        jrows = [(c, r.fiscal_year) for r in joint.itertuples() for c in (r.recipient_codes or "").split(",") if c]
        jp = pd.DataFrame(jrows, columns=["code", "fiscal_year"]).value_counts().rename("value").reset_index()
        for name, d in (("mlit:road_maint", rm), ("mlit:sole_grants", sg), ("mlit:joint_plans", jp)):
            m = base.merge(d, on=["code", "fiscal_year"], how="left").fillna({"value": 0.0})
            m = m.merge(src, on="fiscal_year", how="left")
            m["metric"] = name
            frames.append(m)
    if not frames:
        return pd.DataFrame(columns=["code", "fiscal_year", "metric", "value", "source_url"])
    out = pd.concat(frames, ignore_index=True)
    return out[["code", "fiscal_year", "metric", "value", "source_url"]]


def politician_panel(conn: sqlite3.Connection) -> pd.DataFrame:
    """選挙結果から 自治体 × 議員(politicians.csv に登録された候補)の対立指標を作る。

    columns: election_id, election_date, district, code, politician_id, candidate_name, votes, share,
             top_candidate, top_votes, opposed_locally(当該自治体で最多得票でない), margin_share
    """
    df = pd.read_sql_query(
        """SELECT r.election_id, e.election_date, e.district, r.municipality_code AS code, r.counting_unit,
                  r.candidate_name, r.politician_id, r.votes, r.is_district_winner, r.pr_revived, r.sekihai_rate, r.source_url
           FROM election_results r JOIN elections e USING(election_id)""", conn)
    if df.empty:
        return df
    g = df.groupby(["election_id", "code"])
    df["total"] = g["votes"].transform("sum")
    df["share"] = df["votes"] / df["total"]
    top = df.loc[g["votes"].idxmax(), ["election_id", "code", "candidate_name", "votes"]].rename(
        columns={"candidate_name": "top_candidate", "votes": "top_votes"})
    df = df.merge(top, on=["election_id", "code"])
    df["opposed_locally"] = df["candidate_name"] != df["top_candidate"]
    df["margin_share"] = (df["votes"] - df["top_votes"]) / df["total"]
    return df[df.politician_id.notna()].reset_index(drop=True)
