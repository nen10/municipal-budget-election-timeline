"""寄与内訳つきスコア(設計書 5.6)。

score = Σ weight_i × contribution_i(各寄与は 0〜1)
  fiscal_deviation (0.6): 選挙後に決定された指標のうち最も負のピア比偏差 z を min(1, max(0, -z)/3) に変換
  statement_match  (0.3): 当該議員の発言、または当該自治体名を含む発言でキーワードに一致した件数 n を min(1, n/3)
  authority        (0.1): 最も負の指標の所管省庁と、決定日時点で在任中の議員の役職の所管省庁が一致すれば 1
  reversal         (0.0): 反転パターン(第2フェーズで実装。現状は常に 0)

ゲート: 当該議員が当該自治体で最多得票でなかった(opposed_locally)場合のみ score を出し、それ以外は 0。
ゲート前の値は score_ungated として残す。

スコアは「人が検証すべき順番」を付けるためのもので、確率や疑惑の強さを表すものではない。
"""

from __future__ import annotations

import json
import math
from datetime import date

import pandas as pd

from .panel import decision_date
from .statements import by_politician, mentioning

WEIGHTS = {"fiscal_deviation": 0.6, "statement_match": 0.3, "authority": 0.1, "reversal": 0.0}

METRIC_MINISTRY = {"tokko": "総務省", "mlit": "国土交通省", "card": None}


def fiscal_contribution(z: float | None) -> float:
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return 0.0
    return min(1.0, max(0.0, -z) / 3.0)


def statement_contribution(n: int) -> float:
    return min(1.0, n / 3.0)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    parts = [int(x) for x in str(s).split("-")]
    while len(parts) < 3:
        parts.append(1)
    return date(*parts[:3])


def authority_contribution(positions: pd.DataFrame, politician_id: str, metric: str | None,
                           when: date | None) -> tuple[float, str]:
    if metric is None:
        return 0.0, "該当指標なし"
    ministry = METRIC_MINISTRY.get(metric.split(":")[0])
    pos = positions[positions.politician_id == politician_id]
    if pos.empty:
        return 0.0, "役職データなし"
    desc = "; ".join(f"{r.title}({r.ministry}, {r.start_date}〜{r.end_date or ''}, {r.verification})"
                     for r in pos.itertuples())
    if ministry is None:
        return 0.0, f"指標 {metric} は所管省庁が単一でない。役職: {desc}"
    for r in pos.itertuples():
        st, en = _parse_date(r.start_date), _parse_date(r.end_date)
        active = (st is None or when is None or st <= when) and (en is None or when is None or when <= en)
        if r.ministry and ministry in r.ministry and active:
            return 1.0, f"所管一致: {desc}"
    return 0.0, f"所管不一致または決定時点で未就任(指標の所管: {ministry})。役職: {desc}"


def compute_signals(dev: pd.DataFrame, panel: pd.DataFrame, statements: pd.DataFrame,
                    positions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for p in panel.itertuples(index=False):
        edate = _parse_date(p.election_date)
        d = dev[dev.code == p.code].copy()
        d["decision"] = [decision_date(m, int(fy)) for m, fy in zip(d.metric, d.fiscal_year)]
        d["post"] = d["decision"] > edate
        st_pol = by_politician(statements, p.politician_id)
        st_muni = mentioning(statements, p.code)
        st_all = pd.concat([st_pol, st_muni]).drop_duplicates("id")
        n_st = len(st_all)
        post = d[d.post & d.z.notna()]
        pre = d[~d.post & d.z.notna()]
        pre_worst = pre.groupby("fiscal_year")["z"].min().to_dict()
        years = sorted(post.fiscal_year.unique()) or [None]
        for fy in years:
            sub = post[post.fiscal_year == fy] if fy is not None else post.iloc[0:0]
            if len(sub):
                worst = sub.loc[sub.z.idxmin()]
                wz, wm = float(worst.z), worst.metric
                when = worst.decision
            else:
                worst, wz, wm, when = None, math.nan, None, None
            auth, auth_note = authority_contribution(positions, p.politician_id, wm, when)
            contrib = {
                "fiscal_deviation": fiscal_contribution(wz),
                "statement_match": statement_contribution(n_st),
                "authority": auth,
                "reversal": 0.0,
            }
            ungated = sum(WEIGHTS[k] * v for k, v in contrib.items())
            evidence = {
                "worst_post_metric": wm,
                "worst_post_z": None if math.isnan(wz) else round(wz, 3),
                "worst_post_value": None if worst is None else worst.value,
                "worst_post_prev_value": None if worst is None else worst.prev_value,
                "worst_post_peer_median_growth": None if worst is None else round(float(worst.peer_median_growth), 3),
                "post_metrics": {r.metric: round(float(r.z), 3) for r in sub.itertuples()},
                "pre_election_worst_z_by_year": {int(k): round(float(v), 3) for k, v in pre_worst.items()},
                "authority_note": auth_note,
                "reversal_note": "未実装(第2フェーズ)",
                "statement_urls": list(st_all.source_url.head(5)),
                "fiscal_source_url": None if worst is None else worst.source_url,
                "election_source_url": p.source_url,
            }
            rows.append({
                "municipality_code": p.code, "politician_id": p.politician_id, "election_id": p.election_id,
                "fiscal_year": fy, "opposed_locally": bool(p.opposed_locally),
                "candidate_share": round(float(p.share), 4), "margin_share": round(float(p.margin_share), 4),
                "score": round(ungated if p.opposed_locally else 0.0, 4), "score_ungated": round(ungated, 4),
                **{f"c_{k}": round(v, 4) for k, v in contrib.items()},
                "n_statements": n_st,
                "contributions": json.dumps({k: round(WEIGHTS[k] * v, 4) for k, v in contrib.items()}, ensure_ascii=False),
                "evidence": json.dumps(evidence, ensure_ascii=False, default=str),
                "source_url": evidence["fiscal_source_url"] or p.source_url,
            })
    return pd.DataFrame(rows)
