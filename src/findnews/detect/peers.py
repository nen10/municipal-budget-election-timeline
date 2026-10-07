"""ピア(同県内で財政力指数と人口規模が近い自治体群)と、ピア比の前年比偏差。

成長率は対称変化率(arc change) g = (x_t - x_{t-1}) / ((|x_t| + |x_{t-1}|) / 2) を使う(範囲 -2〜+2)。
前年・当年ともに 0 の場合は情報がないため NaN。0 を含む系列(単独計画の有無で 0 になる交付金)も扱える。

偏差 z = (g_target - median(g_peers)) / max(sd(g_peers), SCALE_FLOOR)
ピアの有効数が MIN_PEERS 未満なら z は NaN。

ピアは指標ごとに選ぶ: その指標の値が存在する自治体の中から距離の近い k 団体
(例: 特別交付税の報道発表は市のみなので、市のピアは市から選ばれる)。
"""

from __future__ import annotations

import math
import sqlite3

import numpy as np
import pandas as pd

SCALE_FLOOR = 0.05
MIN_PEERS = 3


def peer_groups(conn: sqlite3.Connection, pref_code: str = "09", k: int = 5,
                base_year: int | None = None) -> tuple[dict[str, list[str]], pd.DataFrame]:
    """決算カードの財政力指数と住民基本台帳人口(対数)を県内で標準化し、ユークリッド距離の近い k 団体をピアとする。"""
    if base_year is None:
        base_year = conn.execute("SELECT MAX(fiscal_year) FROM municipality_fiscal WHERE source='soumu_card'").fetchone()[0]
    df = pd.read_sql_query(
        """SELECT code, item, value FROM municipality_fiscal
           WHERE source='soumu_card' AND fiscal_year=? AND code LIKE ? AND item IN ('財政力指数','住民基本台帳人口')""",
        conn, params=(base_year, f"{pref_code}%")).pivot(index="code", columns="item", values="value").dropna()
    if df.empty:
        return {}, df
    feat = pd.DataFrame({"fci": df["財政力指数"], "logpop": np.log(df["住民基本台帳人口"])})
    z = (feat - feat.mean()) / feat.std(ddof=0)
    groups = {}
    for c in z.index:
        d = np.sqrt(((z - z.loc[c]) ** 2).sum(axis=1)).drop(c).sort_values()
        groups[c] = list(d.index[:k])
    feat["base_year"] = base_year
    feat.attrs["z"] = z
    feat.attrs["k"] = k
    return groups, feat


def nearest(feat: pd.DataFrame, code: str, candidates, k: int) -> list[str]:
    """標準化特徴量で code に近い順に、candidates の中から k 団体。"""
    z = feat.attrs["z"]
    if code not in z.index:
        return []
    cands = [c for c in candidates if c != code and c in z.index]
    if not cands:
        return []
    d = np.sqrt(((z.loc[cands] - z.loc[code]) ** 2).sum(axis=1)).sort_values()
    return list(d.index[:k])


def arc_change(prev, cur):
    if prev is None or cur is None or (isinstance(prev, float) and math.isnan(prev)) or (isinstance(cur, float) and math.isnan(cur)):
        return np.nan
    den = (abs(prev) + abs(cur)) / 2
    if den == 0:
        return np.nan
    return (cur - prev) / den


def growth_table(series: pd.DataFrame) -> pd.DataFrame:
    s = series.sort_values(["metric", "code", "fiscal_year"]).copy()
    s["prev_value"] = s.groupby(["metric", "code"])["value"].shift(1)
    s["prev_year"] = s.groupby(["metric", "code"])["fiscal_year"].shift(1)
    s.loc[s["prev_year"] != s["fiscal_year"] - 1, "prev_value"] = np.nan
    s["growth"] = [arc_change(p, c) for p, c in zip(s["prev_value"], s["value"])]
    return s


def peer_deviation(series: pd.DataFrame, groups: dict[str, list[str]], feat: pd.DataFrame | None = None) -> pd.DataFrame:
    """全自治体 × 指標 × 年度について、ピア比の偏差を計算する。

    feat(peer_groups の戻り値)を渡すと、指標ごとに値の存在する自治体からピアを選び直す。
    """
    g = growth_table(series)
    key = g.set_index(["metric", "fiscal_year", "code"])["growth"]
    has_data = g[g["value"].notna()].groupby("metric")["code"].agg(lambda s: sorted(set(s))).to_dict()
    k = feat.attrs.get("k", 5) if feat is not None else 5
    cache: dict[tuple[str, str], list[str]] = {}
    rows = []
    for r in g.itertuples(index=False):
        if feat is not None:
            ck = (r.metric, r.code)
            if ck not in cache:
                cache[ck] = nearest(feat, r.code, has_data.get(r.metric, []), k)
            peers = cache[ck]
        else:
            peers = groups.get(r.code, [])
        pg = [key.get((r.metric, r.fiscal_year, p), np.nan) for p in peers]
        pg = [x for x in pg if not (isinstance(x, float) and math.isnan(x))]
        med = float(np.median(pg)) if pg else np.nan
        sd = float(np.std(pg, ddof=1)) if len(pg) >= 2 else np.nan
        if math.isnan(r.growth) or len(pg) < MIN_PEERS:
            z = np.nan
        else:
            z = (r.growth - med) / max(sd if not math.isnan(sd) else 0.0, SCALE_FLOOR)
        rows.append({**r._asdict(), "peers": ",".join(peers), "n_peers_valid": len(pg),
                     "peer_median_growth": med, "peer_sd_growth": sd, "z": z})
    return pd.DataFrame(rows)
