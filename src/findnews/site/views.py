"""自治体ビュー(municipalities.html)と全体ビュー(totals.html)のデータ組み立て(DESIGN.md 16・17 節)。

データは JSON として HTML に埋め込み、計算方法の切り替えはクライアント側(static/views.js)で行う。
"""

from __future__ import annotations

import json
import re

from .. import timeline
from ..municipalities import master
from ..sources.registry import INDICATORS

ORDER = ["card_kokko", "card_pref", "tokko_dec", "tokko_march", "mlit_sole_grants", "mlit_road",
         "e_kokko", "e_kokko_futsuken", "e_kokko_saigai", "e_kokko_shasoukou", "e_pref", "e_futsu", "e_tokko",
         "e_road_kokko", "e_hojo_road_kokko", "futsu_decided", "kofu_total", "kofu_shasoukou", "kofu_bouan", "kofu_road"]
LAYER = {"card_kokko": "決算カード", "card_pref": "決算カード", "tokko_dec": "速報(交付決定)", "tokko_march": "速報(交付決定)",
         "mlit_sole_grants": "速報(当初配分)", "mlit_road": "速報(当初配分)", "futsu_decided": "基準(当初決定)",
         "kofu_total": "速報(交付決定)", "kofu_shasoukou": "速報(交付決定)", "kofu_bouan": "速報(交付決定)",
         "kofu_road": "速報(交付決定)"}

METHODS = {
    "raw": {"name": "実額", "def": "x_t", "unit": "千円", "unitLabel": "千円", "signed": False},
    "diff": {"name": "前年差額", "def": "x_t − x_{t−1}", "unit": "千円", "unitLabel": "千円", "signed": True},
    "pct": {"name": "前年比変化率", "def": "(x_t − x_{t−1}) / x_{t−1}", "unit": "%", "unitLabel": "%", "signed": True},
    "total_pct": {"name": "全体の前年比変化率", "def": "(T_t − T_{t−1}) / T_{t−1}", "unit": "%", "unitLabel": "%",
                  "signed": True, "needsTotal": True},
    "rel_pct": {"name": "全体に対する相対変化率", "def": "(1 + pct_t) / (1 + total_pct_t) − 1", "unit": "%", "unitLabel": "%",
                "signed": True, "needsTotal": True, "needsShare": True},
    "share": {"name": "全体に占める割合", "def": "x_t / T_t", "unit": "%", "unitLabel": "%", "signed": False,
              "needsTotal": True, "needsShare": True},
    "share_diff": {"name": "割合の前年差", "def": "share_t − share_{t−1}(ポイント)", "unit": "pt", "unitLabel": "ポイント",
                   "signed": True, "needsTotal": True, "needsShare": True},
    "index": {"name": "指数(基準年 = 100)", "def": "x_t / x_base × 100", "unit": "指数", "unitLabel": "基準年=100", "signed": False},
    "cum_diff": {"name": "基準年からの累積差額", "def": "x_t − x_base", "unit": "千円", "unitLabel": "千円", "signed": True},
    "diff2": {"name": "差分の差分", "def": "(x_t − x_{t−1}) − (x_{t−1} − x_{t−2})", "unit": "千円", "unitLabel": "千円", "signed": True},
}
DEFAULT_METHODS = ["raw", "pct", "rel_pct", "share_diff"]
OTHER_METHODS = ["total_pct", "share", "diff", "index", "cum_diff", "diff2"]
NOTES_165 = [
    "全体に対する相対値は「全体が一様に動いた場合との差」であり、配分の妥当性や意図を示すものではない。",
    "全体の定義(全国・都道府県計、当初・補正・配分・決算)は各グラフの「全体」に系列名で明記している。",
]


def _year(ps: str) -> int:
    return int(ps[:4])


def build_data(conn, pref, trows, events, districts, reqs_by_code, cfg) -> dict:
    names = master(pref)
    th = cfg["methods"]
    data = {"thresholds": {"pct": float(th["pct"]), "rel_pct": float(th["rel_pct"]), "share_diff": float(th["share_diff"])},
            "methods": METHODS, "defaultLevel": th.get("default_total_level", "national"),
            "indicators": {}, "order": [], "munis": {}, "totals": {}}
    have = {r.indicator_id for r in trows}
    for ind in ORDER:
        if ind not in have:
            continue
        no, label, _ = INDICATORS[ind]
        basis = sorted({r.decided_date_basis for r in trows if r.indicator_id == ind and r.decided_date_basis})
        data["indicators"][ind] = {"no": no, "label": label, "layer": LAYER.get(ind, "基準(決算)" if ind.startswith("e_") else ""),
                                   "basis": "、".join(basis)}
        data["order"].append(ind)
    # 全体系列
    for r in conn.execute("SELECT * FROM totals WHERE pref_code IN ('00', ?) ORDER BY indicator_id, level, period_start", (pref,)):
        t = data["totals"].setdefault(r["indicator_id"], {}).setdefault(
            r["level"], {"series": r["series"], "points": [], "url": r["source_url"], "note": r["note"]})
        t["points"].append({"y": _year(r["period_start"]), "d": r["decided_date"], "v": r["value"]})
    # 国政選挙(全体ビュー用: 投票日ごとに 1 本)
    elec = [e for e in events if e["event_type"] in ("衆院選投票", "参院選投票")]
    seen = {}
    for e in elec:
        seen.setdefault((e["date"], e["event_type"]), e)
    data["prefElections"] = [{"date": d, "text": f"{t}({d})"} for (d, t) in sorted(seen)]
    groups = sorted({e["date"] for e in elec if e["event_type"] == "衆院選投票"})
    last = groups[-1] if groups else None
    data["defaultBase"] = (int(last[:4]) - (1 if int(last[5:7]) >= 4 else 2)) if last else None   # 投票日を含む年度の前年度
    data["election_note"] = f"基準年の既定は直近の衆院選({last})を含む年度の前年度" if last else ""
    gd = {}
    for r in conn.execute("""SELECT municipality_code, fiscal_year, subsidy_name, amount_yen, decided_date, bureau, project_name
                             FROM grant_decisions WHERE municipality_code LIKE ?""", (f"{pref}%",)):
        gd.setdefault(r[0], []).append(r)
    from ..baseline import KOFU
    for code, nm in sorted(names.items()):
        rs = [r for r in trows if r.municipality_code == code]
        series, sources = {}, {}
        for ind in data["order"]:
            ir = [r for r in rs if r.indicator_id == ind]
            series[ind] = [{"y": _year(r.period_start), "d": r.decided_date, "v": r.value, "r": r.missing_reason} for r in ir]
            sources[ind] = sorted({r.source_url for r in ir if r.source_url})
        rel = [e for e in events if e["event_type"] in ("衆院選投票", "参院選投票") and timeline.relevant(e, code, pref, districts)]
        elections = [{"date": e["date"], "text": f"{e['date']} " + (e["summary"] or "").split("。候補者")[0]} for e in rel]
        lanes = {}
        # 申請・要望(関係づけた指標のみ)
        from ..requests import chart_date
        for ind in data["order"]:
            by = {}
            for q in reqs_by_code.get(code, []):
                if q["indicator_link"] not in (ind, "all"):
                    continue
                a, b = chart_date(q)
                key = q["project_key"] or q["project_name"]
                ln = by.setdefault(key, {"label": "", "connect": True, "marks": [], "name": q["project_name"]})
                if a:
                    ln["marks"].append({"date": a, "kind": "apply", "text": f"申請 {a} / {q['project_name']} / 申請先 {q['recipient'] or '—'}"})
                if b:
                    amt = f" 配分 {q['result_amount_thousand_yen']:,.0f} 千円" if q["result_amount_thousand_yen"] else ""
                    ln["marks"].append({"date": b, "kind": "result", "text": f"結果 {b}: {q['result']}{amt} / {q['project_name']}"})
            for k, ln in enumerate(by.values(), 1):
                ln["label"] = f"P{k}"
            if by:
                lanes[ind] = list(by.values())
        # 交付決定のマーカー(指標 10)
        for ind, (pat, _) in KOFU.items():
            marks = []
            for r in gd.get(code, []):
                if pat and not re.search(pat, r[2] or ""):
                    continue
                if not r[4]:
                    continue
                kind = "minus" if (r[3] or 0) < 0 else "plus"
                marks.append({"date": r[4], "kind": kind,
                              "text": f"交付決定 {r[4]} {r[3] / 1000:,.0f} 千円 / {r[2]} / {r[5] or ''} / {r[6] or ''}"})
            if marks:
                lanes[ind] = [{"label": "決定", "connect": False, "marks": [m for m in marks if m["kind"] == "plus"]},
                              {"label": "減額", "connect": False, "marks": [m for m in marks if m["kind"] == "minus"]}]
                lanes[ind] = [ln for ln in lanes[ind] if ln["marks"]]
        data["munis"][code] = {"name": nm, "series": series, "sources": sources, "elections": elections, "lanes": lanes}
    data["firstMuni"] = sorted(names)[0]
    return data


def reconciliation(conn, code: str) -> list[dict]:
    """17.3: 指標 4(当初配分・単独計画)、指標 10(交付決定 社総交+防安交)、指標 6(決算 社会資本整備総合交付金)を年度で並べる。"""
    def obs(ind):
        return {int(r[0][:4]): r[1] for r in conn.execute(
            "SELECT period_start, value FROM observations WHERE municipality_code=? AND indicator_id=?", (code, ind))}
    a, k1, k2, c = obs("mlit_sole_grants"), obs("kofu_shasoukou"), obs("kofu_bouan"), obs("e_kokko_shasoukou")
    years = sorted(set(a) | set(k1) | set(k2) | set(c))
    out = []
    for y in years:
        k = (k1.get(y) or 0) + (k2.get(y) or 0) if (y in k1 or y in k2) else None
        out.append({"y": y, "a": a.get(y), "k": k, "c": c.get(y),
                    "k_a": None if k is None or a.get(y) is None else k - a[y],
                    "c_k": None if k is None or c.get(y) is None else c[y] - k})
    return out


def district_tabs(conn, pref, names) -> list[tuple[str, list[str]]]:
    r = conn.execute("""SELECT election_id FROM elections WHERE pref_code=? AND kind='shugiin_smd'
                        ORDER BY election_date DESC LIMIT 1""", (pref,)).fetchone()
    tabs = []
    if r:
        g = r[0].split("_smd_")[0]
        for e in conn.execute("SELECT election_id, district FROM elections WHERE election_id LIKE ? ORDER BY election_id", (g + "_%",)):
            codes = sorted({x[0] for x in conn.execute(
                "SELECT DISTINCT municipality_code FROM election_results WHERE election_id=? AND municipality_code IS NOT NULL",
                (e[0],))})
            tabs.append((e[1], codes))
    covered = {c for _, cs in tabs for c in cs}
    rest = sorted(set(names) - covered)
    if rest:
        tabs.append(("選挙区の割り当てなし", rest))
    return tabs


def to_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
