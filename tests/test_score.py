import math

import pandas as pd

from findnews.detect import peers, score


def test_arc_change():
    assert peers.arc_change(100, 100) == 0
    assert peers.arc_change(0, 50) == 2
    assert peers.arc_change(50, 0) == -2
    assert math.isnan(peers.arc_change(0, 0))
    assert math.isnan(peers.arc_change(None, 10))


def test_contribution_functions():
    assert score.fiscal_contribution(-3.0) == 1.0
    assert score.fiscal_contribution(-1.5) == 0.5
    assert score.fiscal_contribution(2.0) == 0.0
    assert score.fiscal_contribution(float("nan")) == 0.0
    assert score.statement_contribution(0) == 0
    assert score.statement_contribution(5) == 1.0


def _series():
    # 6 自治体、2 年度。A だけ 2026 年に半減、他は横ばい(+/-少し)
    rows = []
    for code, v25, v26 in [("A", 100, 50), ("B", 100, 101), ("C", 100, 99), ("D", 100, 102), ("E", 100, 98), ("F", 100, 100)]:
        rows += [(code, 2025, "mlit:road_maint", v25, "u"), (code, 2026, "mlit:road_maint", v26, "u")]
    return pd.DataFrame(rows, columns=["code", "fiscal_year", "metric", "value", "source_url"])


def test_peer_deviation_flags_outlier():
    groups = {c: [x for x in "ABCDEF" if x != c][:5] for c in "ABCDEF"}
    dev = peers.peer_deviation(_series(), groups)
    a = dev[(dev.code == "A") & (dev.fiscal_year == 2026)].iloc[0]
    assert a.z < -3
    b = dev[(dev.code == "B") & (dev.fiscal_year == 2026)].iloc[0]
    assert abs(b.z) < 3
    assert math.isnan(dev[(dev.code == "A") & (dev.fiscal_year == 2025)].iloc[0].z)  # 前年なし


def _panel(opposed):
    return pd.DataFrame([{
        "election_id": "e", "election_date": "2026-02-08", "code": "A", "politician_id": "p",
        "share": 0.3, "margin_share": -0.2, "opposed_locally": opposed, "source_url": "x"}])


def test_compute_signals_contributions_and_gate():
    groups = {c: [x for x in "ABCDEF" if x != c][:5] for c in "ABCDEF"}
    dev = peers.peer_deviation(_series(), groups)
    st = pd.DataFrame(columns=["id", "politician_id", "target_municipalities", "source_url"])
    pos = pd.DataFrame([{"politician_id": "p", "title": "国土交通大臣", "ministry": "国土交通省",
                         "start_date": "2025-10", "end_date": None, "verification": "test"}])
    sig = score.compute_signals(dev, _panel(True), st, pos)
    r = sig[sig.fiscal_year == 2026].iloc[0]
    assert r.c_fiscal_deviation == 1.0
    assert r.c_authority == 1.0          # 所管一致かつ決定日(2026-04-01)に在任
    assert r.c_statement_match == 0.0
    assert abs(r.score - (0.6 * 1.0 + 0.1 * 1.0)) < 1e-9
    gated = score.compute_signals(dev, _panel(False), st, pos).iloc[0]
    assert gated.score == 0 and gated.score_ungated > 0


def test_authority_requires_matching_ministry_and_dates():
    pos = pd.DataFrame([{"politician_id": "p", "title": "農林水産大臣", "ministry": "農林水産省",
                         "start_date": "2026-09", "end_date": None, "verification": "unverified"}])
    from datetime import date
    assert score.authority_contribution(pos, "p", "mlit:road_maint", date(2026, 4, 1))[0] == 0.0
    pos2 = pos.assign(ministry="国土交通省")
    assert score.authority_contribution(pos2, "p", "mlit:road_maint", date(2026, 4, 1))[0] == 0.0  # 未就任
    assert score.authority_contribution(pos2, "p", "mlit:road_maint", date(2026, 10, 1))[0] == 1.0
