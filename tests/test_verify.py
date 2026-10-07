import math

from findnews import verify as V


def s(vals):
    return {y: V.Obs(v) for y, v in vals.items()}


def test_direction_threshold():
    assert V.direction(0.05, 0.05) == "増加"
    assert V.direction(-0.05, 0.05) == "減少"
    assert V.direction(0.049, 0.05) == "横ばい"
    assert V.direction(None, 0.05) == V.MISSING
    assert V.direction(0.08, 0.10) == "横ばい"   # 閾値は設定で変わる


def test_yoy_and_undefined():
    x = s({2021: 100, 2022: 80, 2023: 0, 2024: 10, 2025: None})
    assert V.yoy(x, 2022) == (-20, -0.2)
    d, g = V.yoy(x, 2024)
    assert d == 10 and g is None          # 前年 0 は未定義
    assert V.yoy(x, 2025) == (None, None)  # 当年未取得


def test_max_decline():
    m = V.max_decline(s({2021: 50, 2022: 120, 2023: 90, 2024: None}))
    assert (m["max"], m["max_year"], m["latest"], m["latest_year"]) == (120, 2022, 90, 2023)
    assert m["decline"] == 30 and abs(m["rate"] - 0.25) < 1e-12


def test_pre_post():
    x = s({2023: 100, 2024: 200, 2025: 300, 2026: 100})
    p = V.pre_post(x, [2023, 2024, 2025], 2026, 0.05)
    assert p["pre"] == 200 and p["post"] == 100 and p["diff"] == -100 and p["rate"] == -0.5
    assert p["direction"] == "減少"
    p = V.pre_post(s({2023: 100, 2024: None, 2025: 300, 2026: 100}), [2023, 2024, 2025], 2026, 0.05)
    assert p["pre"] is None and p["direction"] == V.MISSING


def test_classify_items():
    prev = {"A計画": 100, "B計画（第三期）": 50, "C": 10, "D": 5}
    cur = {"A計画": 80, "B計画（第四期）": 60, "C": 10, "E": 7}
    out = {r["item"]: r for r in V.classify_items(prev, cur)}
    assert out["A計画"]["class"] == "継続(減額)"
    assert out["B計画（第四期）"]["class"] == "継続(増額)" and "名称変更" in out["B計画（第四期）"]["note"]
    assert out["C"]["class"] == "継続(同額)"
    assert out["E"]["class"] == "新規"
    assert out["D"]["class"] == "消滅" and out["D"]["cur"] is None
