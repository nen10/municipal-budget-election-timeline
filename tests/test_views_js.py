"""第16節の計算方法(static/views.js の FNCalc)を node で実行して、定義どおりの値になることを確かめる。
node がない環境ではスキップする(ページの描画には node は不要)。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from findnews.site.views import METHODS

JS = Path(__file__).resolve().parents[1] / "src" / "findnews" / "site" / "static" / "views.js"
NODE = shutil.which("node")
TH = {"pct": 0.05, "rel_pct": 0.05, "share_diff": 0.1}

# 那須烏山市 指標 5 の 2025・2026 年度と全国計(千円)に、2023・2024 年度の仮の値を足した系列
X = [{"y": 2023, "d": "2023-03-31", "v": 10000}, {"y": 2024, "d": "2024-04-01", "v": 40000},
     {"y": 2025, "d": "2025-04-01", "v": 59000}, {"y": 2026, "d": "2026-04-07", "v": 20000}]
T = [{"y": 2023, "v": 800000000}, {"y": 2024, "v": 820000000},
     {"y": 2025, "v": 855950000}, {"y": 2026, "v": 860753000}]


def _run(expr: str):
    code = f"const C = require({json.dumps(str(JS))}); const X = {json.dumps(X)}, T = {json.dumps(T)}, " \
           f"TH = {json.dumps(TH)}; process.stdout.write(JSON.stringify({expr}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


pytestmark = pytest.mark.skipif(NODE is None, reason="node が見つからない")


def test_all_methods_match_definitions():
    res = _run("Object.fromEntries(" + json.dumps(list(METHODS)) + ".map(m => [m, C.compute(m, X, T, 2024)]))")
    assert set(res) == set(METHODS) and len(METHODS) == 10
    v = {m: {r["y"]: r["v"] for r in rows} for m, rows in res.items()}
    x = {p["y"]: p["v"] for p in X}
    t = {p["y"]: p["v"] for p in T}
    y = 2026
    pct = (x[y] - x[y - 1]) / x[y - 1]
    tpct = (t[y] - t[y - 1]) / t[y - 1]
    assert v["raw"][y] == x[y]
    assert v["diff"][y] == x[y] - x[y - 1]
    assert v["pct"][y] == pytest.approx(pct * 100)
    assert v["total_pct"][y] == pytest.approx(tpct * 100)
    assert v["rel_pct"][y] == pytest.approx(((1 + pct) / (1 + tpct) - 1) * 100)
    assert v["rel_pct"][y] == pytest.approx(-66.29, abs=0.01)
    assert v["share"][y] == pytest.approx(x[y] / t[y] * 100)
    assert v["share_diff"][y] == pytest.approx(x[y] / t[y] * 100 - x[y - 1] / t[y - 1] * 100)
    assert v["index"][y] == pytest.approx(x[y] / x[2024] * 100) and v["index"][2024] == 100
    assert v["cum_diff"][y] == x[y] - x[2024] and v["cum_diff"][2024] == 0
    assert v["diff2"][y] == (x[y] - x[y - 1]) - (x[y - 1] - x[y - 2])
    # 最初の年度は前年がないので前年比系は値なし(推定しない)
    for m in ("diff", "pct", "rel_pct", "share_diff", "diff2"):
        assert v[m][2023] is None, m
    assert v["diff2"][2024] is None


def test_gap_year_and_missing_total_give_no_value():
    gap = [{"y": 2021, "d": "a", "v": 100}, {"y": 2023, "d": "b", "v": 150}]
    res = _run(f"[C.compute('pct', {json.dumps(gap)}, T, 2021), C.compute('rel_pct', X, [], 2024),"
               f" C.compute('share', X, [], 2024)]")
    assert res[0][1]["v"] is None                                    # 前年(2022)がない
    assert all(r["v"] is None for r in res[1]) and all(r["v"] is None for r in res[2])   # 全体がなければ計算しない


def test_direction_thresholds_and_format():
    res = _run("[C.compute('rel_pct', X, T, 2024), C.compute('share_diff', X, T, 2024), C.compute('pct', X, T, 2024)]"
               ".map(rows => rows.map(r => C.direction(r, TH)))")
    rel, sd, pct = res
    assert rel[-1] == "減少" and rel[0] == "未取得"
    assert sd[-1] == "横ばい"                                        # −0.0046 ポイント < 閾値 0.1
    assert pct[2] == "増加"                                          # +47.5% ≥ 5%
    f = _run("[C.fmt(-0.0046, 'pt'), C.fmt(-66.29, '%'), C.fmt(1234567, '千円'), C.fmt(null, '%'), C.fmt(12.5, '%')]")
    assert f == ["−0.0046", "−66.3", "1,234,567", "—", "+12.5"]
