from datetime import date

import pandas as pd

from findnews.detect import score


def test_contribution_functions():
    assert score.decline_contribution(-0.5) == 0.5
    assert score.decline_contribution(-1.0) == 1.0
    assert score.decline_contribution(0.3) == 0.0
    assert score.decline_contribution(None) == 0.0
    assert score.statement_contribution(0) == 0
    assert score.statement_contribution(5) == 1.0


def _pp(rate):
    return {"pre_years": [2023, 2024, 2025], "pre": 100.0, "post_year": 2026, "post": 100 * (1 + rate),
            "diff": 100 * rate, "rate": rate, "direction": "減少" if rate <= -0.05 else "横ばい"}


def _panel(opposed):
    return pd.DataFrame([{"election_id": "e_smd_09_3", "election_date": "2026-02-08", "code": "A", "politician_id": "p",
                          "candidate_name": "候補P", "nomination": "X党公認", "result_label": "落選・比例復活",
                          "share": 0.3, "rank": 2, "top_candidate": "候補Q", "top_nomination": "無所属", "top_share": 0.6,
                          "margin_share": -0.3, "opposed_locally": opposed, "source_url": "u"}])


def test_compute_signals_and_gate():
    prepost = {("A", "3b"): _pp(0.02), ("A", "4"): None, ("A", "5"): _pp(-0.6)}
    dd = {"3b": date(2026, 3, 17), "4": date(2026, 4, 7), "5": date(2026, 4, 7)}
    st = pd.DataFrame(columns=["id", "politician_id", "target_municipalities", "source_url", "date", "speaker", "meeting"])
    pos = pd.DataFrame([{"politician_id": "p", "title": "国土交通大臣", "ministry": "国土交通省",
                         "start_date": "2025-10-21", "end_date": None, "verification": "test"}])
    r = score.compute_signals(prepost, dd, _panel(True), st, pos).iloc[0]
    assert abs(r.c_own_decline - 0.6) < 1e-9
    assert r.c_authority == 1.0          # 指標 5 の所管(国交省)と一致し、2026-04-07 に在任
    assert abs(r.score - (0.6 * 0.6 + 0.1)) < 1e-9
    g = score.compute_signals(prepost, dd, _panel(False), st, pos).iloc[0]
    assert g.score == 0 and g.score_ungated > 0


def test_authority_dates_and_ministry():
    pos = pd.DataFrame([{"politician_id": "p", "title": "農林水産大臣", "ministry": "農林水産省",
                         "start_date": "2026-09-17", "end_date": None, "verification": "verified"}])
    assert score.authority_contribution(pos, "p", "国土交通省", date(2026, 4, 7))[0] == 0.0
    pos2 = pos.assign(ministry="国土交通省")
    assert score.authority_contribution(pos2, "p", "国土交通省", date(2026, 4, 7))[0] == 0.0   # 決定時点で未就任
    assert score.authority_contribution(pos2, "p", "国土交通省", date(2026, 10, 1))[0] == 1.0
