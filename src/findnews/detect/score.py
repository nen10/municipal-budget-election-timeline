"""寄与内訳つきスコア(設計書 5.6)。

score = Σ weight_i × contribution_i(各寄与は 0〜1)
  own_decline      (0.6): 選挙後の値が当該自治体自身の選挙前平均より減った割合のうち最大のもの(指標 3・4・5)。
                          contribution = min(1, max(0, -(選挙後 − 選挙前平均) / 選挙前平均))
                          他自治体との比較は使わない。
  statement_match  (0.3): 当該議員の発言、または当該自治体名を含む発言でキーワードに一致した件数 n → min(1, n/3)。
                          キーワードの由来事例への一致(config/keywords.yaml の origin_case_id)は数えない
  authority        (0.1): 減少が最大の指標の所管省庁(指標 3 = 総務省、4・5 = 国土交通省)と、
                          その配分の決定日時点で在任中の議員の役職(positions.csv)の所管省庁が一致すれば 1
  reversal         (0.0): 反転パターン(第2フェーズ。現状は常に 0)

ゲート: 当該議員が当該自治体で最多得票でなかった(opposed_locally)場合のみ score を出し、それ以外は 0。
ゲート前の値は score_ungated として残す。スコアは確認の優先順位付けのための目安で、確率や疑惑の強さではない。
"""

from __future__ import annotations

import json
import math
from datetime import date

import pandas as pd

from .statements import by_politician, independent, mentioning

WEIGHTS = {"own_decline": 0.6, "statement_match": 0.3, "authority": 0.1, "reversal": 0.0}
INDICATOR_MINISTRY = {"3b": "総務省", "4": "国土交通省", "5": "国土交通省"}


def decline_contribution(rate: float | None) -> float:
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        return 0.0
    return min(1.0, max(0.0, -rate))


def statement_contribution(n: int) -> float:
    return min(1.0, n / 3.0)


def _parse_date(s: str | None) -> date | None:
    if s is None or (isinstance(s, float) and math.isnan(s)) or not str(s).strip():
        return None
    parts = [int(x) for x in str(s).split("-")]
    while len(parts) < 3:
        parts.append(1)
    return date(*parts[:3])


def authority_contribution(positions: pd.DataFrame, politician_id: str, ministry: str | None,
                           when: date | None) -> tuple[float, str]:
    if ministry is None:
        return 0.0, "該当指標なし"
    pos = positions[positions.politician_id == politician_id]
    if pos.empty:
        return 0.0, "役職データなし"
    desc = "; ".join(f"{r.title}({r.ministry}, {r.start_date}〜{r.end_date or ''}, {r.verification})" for r in pos.itertuples())
    for r in pos.itertuples():
        st, en = _parse_date(r.start_date), _parse_date(r.end_date)
        active = (st is None or when is None or st <= when) and (en is None or when is None or when <= en)
        if r.ministry and ministry in r.ministry and active:
            return 1.0, f"所管一致({ministry}): {desc}"
    return 0.0, f"所管不一致または決定時点で未就任(指標の所管: {ministry}、決定日 {when})。役職: {desc}"


def compute_signals(prepost: dict, decision_dates: dict, panel: pd.DataFrame, statements: pd.DataFrame,
                    positions: pd.DataFrame) -> pd.DataFrame:
    """prepost: {(code, key): verify.pre_post の結果}、decision_dates: {key: 選挙後の値の決定日(date)}"""
    rows = []
    for p in panel.itertuples(index=False):
        rates = {k: prepost.get((p.code, k)) for k in ("3b", "4", "5")}
        avail = {k: v for k, v in rates.items() if v and v["rate"] is not None}
        worst = min(avail, key=lambda k: avail[k]["rate"]) if avail else None
        wr = avail[worst]["rate"] if worst else None
        when = decision_dates.get(worst) if worst else None
        auth, auth_note = authority_contribution(positions, p.politician_id, INDICATOR_MINISTRY.get(worst), when)
        st_all = pd.concat([by_politician(statements, p.politician_id), mentioning(statements, p.code)]).drop_duplicates("id")
        st = independent(st_all)
        excluded = st_all[~st_all.id.isin(st.id)]
        contrib = {"own_decline": decline_contribution(wr), "statement_match": statement_contribution(len(st)),
                   "authority": auth, "reversal": 0.0}
        ungated = sum(WEIGHTS[k] * v for k, v in contrib.items())
        evidence = {
            "indicators": {k: (None if v is None else {"pre_years": v["pre_years"], "pre_avg": v["pre"], "post_year": v["post_year"],
                                                     "post": v["post"], "diff": v["diff"], "rate": v["rate"],
                                                     "direction": v["direction"]}) for k, v in rates.items()},
            "largest_decline_indicator": worst, "largest_decline_rate": wr,
            "authority_note": auth_note, "reversal_note": "未実装(第2フェーズ)",
            "statements": [{"date": r.date, "speaker": r.speaker, "venue": r.meeting, "url": r.source_url}
                           for r in st.itertuples()][:10],
            "statements_excluded_not_independent": [
                {"date": r.date, "speaker": r.speaker, "url": r.source_url, "note": r.origin_note}
                for r in excluded.itertuples()],
            "election_source_url": p.source_url,
        }
        rows.append({
            "municipality_code": p.code, "politician_id": p.politician_id, "candidate_name": p.candidate_name,
            "nomination": p.nomination, "result_label": p.result_label, "election_id": p.election_id,
            "opposed_locally": bool(p.opposed_locally), "candidate_share": round(float(p.share), 4),
            "candidate_rank": int(p.rank), "top_candidate": p.top_candidate, "top_nomination": p.top_nomination,
            "top_share": round(float(p.top_share), 4),
            "score": round(ungated if p.opposed_locally else 0.0, 4), "score_ungated": round(ungated, 4),
            **{f"c_{k}": round(v, 4) for k, v in contrib.items()}, "n_statements": len(st),
            "n_statements_not_independent": len(excluded),
            "contributions": json.dumps({k: round(WEIGHTS[k] * v, 4) for k, v in contrib.items()}, ensure_ascii=False),
            "evidence": json.dumps(evidence, ensure_ascii=False, default=str),
            "source_url": p.source_url,
        })
    return pd.DataFrame(rows)
