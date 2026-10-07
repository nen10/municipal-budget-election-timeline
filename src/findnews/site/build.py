"""静的 HTML の生成(DESIGN.md 第15節)。

データは DB(observations、events、elections 等)と timeline / matrix / verify の計算結果から取る。
外部 CDN・通信には依存しない。相対リンクのみで、ファイルを直接開いても表示できる。
"""

from __future__ import annotations

import html
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import config, settings as settings_mod, timeline, verify
from ..municipalities import master
from ..prefs import name as pref_name
from ..sources.registry import INDICATORS, status_for_pref

HERE = Path(__file__).parent
MINUS = "−"


# ------------------------------------------------------------------ 書式

def fmt_num(v, digits=0):
    if v is None or v == "":
        return ""
    s = f"{abs(v):,.{digits}f}"
    return (MINUS + s) if v < 0 else s


def fmt_pct(v):
    if v is None:
        return "未定義"
    s = f"{abs(v) * 100:.1f}%"
    return ("+" if v > 0 else MINUS if v < 0 else "±") + s


def dir_text(direction, pct):
    """方向は文字で示す(例: 減少 −66.1%)。色は dir_class で別に付ける。"""
    if direction in ("増加", "減少", "横ばい"):
        return f"{direction} {fmt_pct(pct)}"
    return direction or ""


def dir_class(direction) -> str:
    return {"増加": "up", "減少": "down"}.get(direction, "flat")


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(str(HERE / "templates")), autoescape=select_autoescape(["html"]),
                    trim_blocks=True, lstrip_blocks=True)
    e.filters["num"] = fmt_num
    e.filters["pct"] = fmt_pct
    e.globals["dir_text"] = dir_text
    e.globals["dir_class"] = dir_class
    return e


# ------------------------------------------------------------------ グラフ(SVG)

def _days(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def _nice_ticks(vmax: float, n: int = 4) -> list[float]:
    if vmax <= 0:
        return [0.0]
    raw = vmax / n
    mag = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    out, t = [], 0.0
    while t <= vmax * 1.0001:
        out.append(t)
        t += step
    if out[-1] < vmax:
        out.append(t)
    return out


CHART_EVENT_TYPES = {"衆院選投票", "参院選投票"}   # DESIGN.md 13.5: グラフの縦線は国政選挙の投票日だけ


def chart_svg(rows: list, events: list[dict], unit: str, title: str,
              reqs: list[dict] | None = None) -> tuple[str | None, list[dict], list[dict]]:
    """1 指標の推移グラフ。戻り値: (SVG 文字列 or None, 縦線にした選挙 [番号つき], 申請・要望のレーン [番号つき])。

    線分と点は「その観測時点の直前比の方向」で色分けする(増加=青、減少=赤、横ばい・未取得=灰)。凡例はテンプレートで
    添え、方向は表の文字でも示す。線 2px・点 r=4 と 2px の白い輪。グリッドはヘアライン。
    縦線は国政選挙の投票日のみ(13.5)。申請・要望は横軸を共有する下段のレーンに、申請=白抜きの四角、結果=塗りの菱形で
    置き、同じ事業(計画名)を細線で結ぶ。どちらも同じ番号の表を図の下に置く。
    """
    reqs = reqs or []
    pts = [r for r in rows if r.decided_date]
    vals = [r.value for r in pts if r.value is not None]
    if not vals:
        return None, [], []
    from ..requests import chart_date
    lanes: dict[str, list[dict]] = {}
    for q in reqs:
        a, b = chart_date(q)
        if a or b:
            lanes.setdefault(q["project_key"] or q["project_name"] or "?", []).append(q)
    n_lanes = len(lanes)
    W, L, R, T, B = 880, 84, 64, 26, 40
    PH = 260                                   # 値のグラフの高さ
    LANE = 16
    H = PH + (18 + n_lanes * LANE if n_lanes else 0)
    xs = [_days(r.decided_date) for r in pts]
    for qs in lanes.values():
        for q in qs:
            xs += [_days(d) for d in chart_date(q) if d]
    x0, x1 = min(xs), max(xs)
    pad = max(30, (x1 - x0) * 0.04)
    dx0, dx1 = x0 - pad, x1 + pad
    ticks = _nice_ticks(max(vals))
    ymax = ticks[-1] or 1
    base = PH - B                              # 値グラフの基線

    def X(d):
        return L + (W - L - R) * (_days(d) - dx0) / (dx1 - dx0)

    def Y(v):
        return T + (base - T) * (1 - v / ymax)

    def hit(x, y, lines, r=9):
        return (f'<circle class="event-hit" tabindex="0" cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="transparent" '
                f'data-lines="{html.escape(json.dumps([l for l in lines if l], ensure_ascii=False))}">'
                f'<title>{html.escape(" / ".join(l for l in lines if l))}</title></circle>')

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{html.escape(title)}" class="chart" data-points="__PTS__">']
    for t in ticks:
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>')
        parts.append(f'<g class="axis"><text x="{L - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{fmt_num(t)}</text></g>')
    for y in range(date.fromordinal(int(dx0)).year, date.fromordinal(int(dx1)).year + 1):
        d = f"{y}-01-01"
        if dx0 <= _days(d) <= dx1:
            parts.append(f'<line class="grid" x1="{X(d):.1f}" x2="{X(d):.1f}" y1="{base}" y2="{base + 4}"/>')
            parts.append(f'<g class="axis"><text x="{X(d):.1f}" y="{base + 16}" text-anchor="middle">{y}</text></g>')
    parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{base}" y2="{base}"/>')
    parts.append(f'<g class="axis"><text x="{L - 6}" y="{T - 10}" text-anchor="end">{html.escape(unit)}</text></g>')
    parts.append(f'<rect class="plot-area" x="{L}" y="{T}" width="{W - L - R}" height="{base - T}" fill="transparent"/>')
    # 選挙の縦線(値グラフと申請レーンを貫く)
    drawn = [e for e in events if e["event_type"] in CHART_EVENT_TYPES and dx0 <= _days(e["date"]) <= dx1]
    for i, e in enumerate(drawn, 1):
        x = X(e["date"])
        parts.append(f'<line class="event" x1="{x:.1f}" x2="{x:.1f}" y1="{T - 4}" y2="{H - 4}"/>')
        parts.append(f'<text class="event-label" x="{x + 2:.1f}" y="{T - 6 - (i % 2) * 10}">E{i}</text>')
        summ = (e.get("summary") or "").split("。候補者")[0]
        parts.append(f'<line class="event-hit" tabindex="0" x1="{x:.1f}" x2="{x:.1f}" y1="{T - 4}" y2="{base}" '
                     f'data-lines="{html.escape(json.dumps([f"E{i}. {e["date"]} {e["event_type"]}", summ], ensure_ascii=False))}">'
                     f'<title>{html.escape(f"E{i}. {e["date"]} {summ}")}</title></line>')
    # 値の線分と点
    for a, b in zip(pts, pts[1:]):
        if a.value is None or b.value is None:
            continue
        parts.append(f'<line class="seg {dir_class(b.direction)}" x1="{X(a.decided_date):.1f}" y1="{Y(a.value):.1f}" '
                     f'x2="{X(b.decided_date):.1f}" y2="{Y(b.value):.1f}"/>')
    hover = []
    for r in pts:
        if r.value is None:
            continue
        x, y = X(r.decided_date), Y(r.value)
        parts.append(f'<circle class="dot {dir_class(r.direction if r.window_start else None)}" cx="{x:.1f}" cy="{y:.1f}" r="4">'
                     f'<title>{r.decided_date}: {fmt_num(r.value)} {unit}</title></circle>')
        hover.append({"x": round(x, 1), "lines": [f"{fmt_num(r.value)} {unit}", f"{r.period_start[:4]}年度分 decided {r.decided_date}",
                                                    "直前比 " + (f"{fmt_num(r.delta)}({dir_text(r.direction, r.delta_pct)})"
                                                               if r.delta is not None else "—")]})
    last = [r for r in pts if r.value is not None][-1]
    parts.append(f'<g class="axis"><text x="{X(last.decided_date) + 8:.1f}" y="{Y(last.value) + 4:.1f}" text-anchor="start">'
                 f'{fmt_num(last.value)}</text></g>')
    # 申請・要望のレーン
    lane_list = []
    if n_lanes:
        top = PH + 6
        parts.append(f'<g class="axis"><text x="{L - 6}" y="{top + 6}" text-anchor="end">申請・要望</text></g>')
        for k, (key, qs) in enumerate(lanes.items(), 1):
            y = top + 10 + k * LANE - LANE / 2
            parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
            parts.append(f'<g class="axis"><text x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">P{k}</text></g>')
            marks = []
            for q in qs:
                a, b = chart_date(q)
                if a:
                    marks.append((a, "apply", q))
                if b:
                    marks.append((b, "result", q))
            marks.sort(key=lambda m: m[0])
            for (d1, _, _), (d2, _, _) in zip(marks, marks[1:]):
                parts.append(f'<line class="req-link" x1="{X(d1):.1f}" x2="{X(d2):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>')
            for d, kind, q in marks:
                x = X(d)
                if kind == "apply":
                    parts.append(f'<rect class="req-apply" x="{x - 4:.1f}" y="{y - 4:.1f}" width="8" height="8"/>')
                    lines = [f"P{k} 申請 {d}" + ("(年度のみ判明)" if q["date_precision"] == "fiscal_year" else ""),
                             q["project_name"], f"申請先 {q['recipient'] or '—'} / {q['program'] or ''}"]
                else:
                    parts.append(f'<path class="req-result" d="M{x:.1f},{y - 5:.1f} L{x + 5:.1f},{y:.1f} L{x:.1f},{y + 5:.1f} '
                                 f'L{x - 5:.1f},{y:.1f} Z"/>')
                    amt = f" 配分 {fmt_num(q['result_amount_thousand_yen'])} 千円" if q.get("result_amount_thousand_yen") else ""
                    lines = [f"P{k} 結果 {d}: {q['result']}{amt}", q["project_name"]]
                parts.append(hit(x, y, lines))
            lane_list.append({"n": k, "name": qs[0]["project_name"], "records": sorted(
                qs, key=lambda q: (chart_date(q)[0] or chart_date(q)[1] or ""))})
    parts.append(f'<line class="crosshair" visibility="hidden" x1="0" x2="0" y1="{T}" y2="{base}"/>')
    parts.append("</svg>")
    svg = "\n".join(parts).replace("__PTS__", html.escape(json.dumps(hover, ensure_ascii=False)))
    return svg, [dict(e, n=i) for i, e in enumerate(drawn, 1)], lane_list


# ------------------------------------------------------------------ データ

def _groups(conn, pref):
    out = []
    for g, d in conn.execute("""SELECT DISTINCT substr(election_id, 1, instr(election_id, '_smd_') - 1), election_date
                                FROM elections WHERE pref_code=? ORDER BY election_date DESC""", (pref,)):
        n = conn.execute("SELECT COUNT(*) FROM elections WHERE election_id LIKE ?", (g + "_%",)).fetchone()[0]
        out.append({"id": g, "date": d, "districts": n})
    return out


def coverage(conn, pref) -> tuple[list[int], list[dict]]:
    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT CAST(substr(period_start,1,4) AS INTEGER) FROM observations WHERE pref_code=? ORDER BY 1", (pref,))]
    rows = []
    for ind, (no, label, _) in INDICATORS.items():
        cells = []
        for y in years:
            r = conn.execute("""SELECT COUNT(*), SUM(value IS NOT NULL), MIN(decided_date), MAX(decided_date_is_proxy),
                                       MIN(source_url) FROM observations WHERE pref_code=? AND indicator_id=?
                                       AND substr(period_start,1,4)=?""", (pref, ind, str(y))).fetchone()
            cells.append({"n": r[0], "ok": r[1] or 0, "decided": r[2], "proxy": r[3], "url": r[4]})
        rows.append({"id": ind, "no": no, "label": label, "cells": cells})
    return years, rows


def obs_sentence(r) -> str:
    no, label, _ = INDICATORS[r.indicator_id]
    head = f"指標 {no} {label}: {r.period_start[:4]}年度分"
    if r.value is None:
        return f"{head}は未取得({r.missing_reason or '理由不明'})"
    s = f"{head} {fmt_num(r.value)}千円"
    if r.delta is not None:
        s += f"(直前 {fmt_num(r.value - r.delta)}千円、{dir_text(r.direction, r.delta_pct)})"
    return s + f"。decided_date {r.decided_date}{'(公表日で代用)' if r.decided_date_is_proxy else ''}"


def muni_points(conn, code, latest, groups, endorse_rows, n_events, n_manual) -> list[str]:
    pts = []
    for ind in ("mlit_road", "mlit_sole_grants", "tokko_march", "card_kokko"):
        r = latest.get((code, ind))
        if r is not None:
            pts.append(obs_sentence(r))
    if groups:
        g = groups[0]
        res = conn.execute(
            """SELECT r.candidate_name, MAX(r.nomination) nom, MAX(r.result_label) res, MAX(r.vote_share) sh, e.district
               FROM election_results r JOIN elections e USING(election_id)
               WHERE r.election_id LIKE ? AND r.municipality_code=? GROUP BY r.election_id, r.candidate_name
               ORDER BY sh DESC""", (g["id"] + "_%", code)).fetchall()
        if res:
            pts.append(f"{g['date']} 衆院選({res[0]['district']})の当該自治体内の得票: " + "、".join(
                f"{x['candidate_name']}({x['nom']}、{x['res']}){(x['sh'] or 0) * 100:.1f}%" for x in res[:3]))
    for e in endorse_rows:
        pts.append(f"首長 {e['mayor_name']} 氏が {e['candidate_name']}({e['candidate_party_nomination']}、{e['candidate_result']})"
                   f"を支援({e['endorsement_form']}。{e['outlet']} {e['evidence_date']})")
    pts.append(f"この自治体に関係する登録イベント {n_events} 件(うち報道等から手作業で登録 {n_manual} 件)")
    return pts


def latest_errors(conn) -> list[tuple[str, str, str]]:
    """(source, step, 対象) ごとに最新の記録がエラーのもの(後で成功したものは除く)。"""
    latest: dict[tuple, tuple] = {}
    for r in conn.execute("SELECT source, step, status, detail, source_url FROM fetch_log ORDER BY id"):
        target = (r["detail"] or "").split(":")[0] if r["step"] == "parse" else (r["source_url"] or r["detail"])
        latest[(r["source"], r["step"], target)] = (r["status"], r["detail"] or "")
    return [(k[0], k[1], v[1]) for k, v in latest.items() if v[0] == "error"]


def source_list(urls) -> list[str]:
    return sorted({u for u in urls if u})


def build(conn: sqlite3.Connection, pref: str, out_dir: Path | None = None) -> dict:
    out = Path(out_dir or config.PROCESSED_DIR / "site")
    if out.exists():
        shutil.rmtree(out)
    (out / "municipalities").mkdir(parents=True)
    (out / "matrix").mkdir()
    (out / "static").mkdir()
    for f in ("style.css", "site.js", "views.js"):
        shutil.copy(HERE / "static" / f, out / "static" / f)
    e = env()
    cfg = settings_mod.load()
    th = float(cfg["verification"]["direction_threshold"])
    names = master(pref)
    trows, events = timeline.build(conn, pref, None, None, th)
    districts = timeline.district_members(conn, pref)
    groups = _groups(conn, pref)
    status = status_for_pref(pref)
    common = {"pref": pref, "pref_name": pref_name(pref), "threshold": th, "generated": date.today().isoformat(),
              "indicators": INDICATORS, "groups": groups}
    pages = []

    def write(rel: str, tpl: str, **ctx):
        root = "../" * rel.count("/")
        (out / rel).write_text(e.get_template(tpl).render(root=root, page=rel, **common, **ctx), encoding="utf-8")
        pages.append(rel)

    # 自治体ごとの最新差分
    latest = {}
    for r in trows:
        if r.decided_date:
            k = (r.municipality_code, r.indicator_id)
            if k not in latest or r.decided_date > latest[k].decided_date:
                latest[k] = r
    endorse = {r["municipality_code"]: r["collection_status"] for r in conn.execute(
        "SELECT municipality_code, collection_status FROM endorsements")}
    years, cov = coverage(conn, pref)
    unsupported = [s for s in status if s["status"].startswith("未対応")]
    counts = {
        "endorse_uncollected": sum(1 for c in names if endorse.get(c, "未収集") == "未収集"),
        "endorse_collected": sum(1 for c in names if endorse.get(c) == "収集済"),
        "missing_obs": conn.execute("SELECT COUNT(*) FROM observations WHERE pref_code=? AND value IS NULL", (pref,)).fetchone()[0],
        "obs": conn.execute("SELECT COUNT(*) FROM observations WHERE pref_code=?", (pref,)).fetchone()[0],
        "events": len(events), "events_manual": sum(1 for x in events if not x["generated_by"]),
        "unsupported": unsupported,
    }
    first_counts = {}
    if groups:
        g0 = groups[0]
        for ind in ("tokko_march", "mlit_sole_grants", "mlit_road"):
            cnt = {}
            for c in names:
                r = timeline.first_after(trows, c, ind, g0["date"])
                k = r.direction if r else "未取得"
                cnt[k] = cnt.get(k, 0) + 1
            first_counts[ind] = cnt
    write("index.html", "index.html", munis=sorted(names.items()), latest=latest, years=years, cov=cov, counts=counts,
          errors=latest_errors(conn), first_counts=first_counts)

    # 自治体ページ
    vres = verify.compute(conn, pref)
    for code, nm in sorted(names.items()):
        rs = [r for r in trows if r.municipality_code == code]
        rel = [x for x in events if timeline.relevant(x, code, pref, districts)]
        evmap = {x["event_id"]: x for x in rel}
        from .. import requests as RQ
        reqs = RQ.for_municipality(conn, code)
        req_status = [dict(x) for x in conn.execute(
            "SELECT * FROM request_status WHERE municipality_code=? ORDER BY collection_source", (code,))]
        blocks = []
        for ind, (no, label, ministry) in INDICATORS.items():
            ir = [r for r in rs if r.indicator_id == ind]
            if not ir:
                continue
            linked = [q for q in reqs if q["indicator_link"] in (ind, "all")]
            svg, drawn, lanes = chart_svg(ir, rel, "千円", f"{nm} 指標 {no} {label} の推移", linked)
            req_in = {}
            for r in ir:
                if r.window_start:
                    req_in[r.period_start] = [q for q in reqs if any(
                        dd and r.window_start <= dd <= r.window_end for dd in (q["request_date"], q["result_date"]))]
            blocks.append({"id": ind, "no": no, "label": label, "ministry": ministry, "rows": ir, "svg": svg, "drawn": drawn,
                           "lanes": lanes, "req_in": req_in,
                           "sources": source_list(r.source_url for r in ir),
                           "basis": sorted({r.decided_date_basis for r in ir if r.decided_date_basis})})
        items = [it for it in vres.item_rows if it["code"] == code]
        item_sources = source_list(r[0] for r in conn.execute(
            """SELECT DISTINCT source_url FROM subsidy_allocations WHERE municipality_code=? AND
               program_id IN ('mlit_shasoukou','mlit_bouan','mlit_road')""", (code,)))
        endorse_rows = [dict(x) for x in conn.execute(
            "SELECT * FROM endorsements WHERE municipality_code=? AND collection_status='収集済'", (code,))]
        points = muni_points(conn, code, latest, groups, endorse_rows, len(rel), sum(1 for x in rel if not x["generated_by"]))
        write(f"municipalities/{code}.html", "municipality.html", code=code, name=nm, blocks=blocks, items=items,
              item_sources=item_sources, events=rel, evmap=evmap, endorse=endorse_rows, points=points,
              reqs=reqs, req_status=req_status)

    # マトリックス
    from .. import matrix as M
    for g in groups:
        c = M.compute(conn, g["id"], pref)
        c["by_dir"] = {k: {b: [code for code, d in c["dirs"][k].items() if d == b] for b in M.B_ORDER} for k, _ in M.B_KEYS}
        write(f"matrix/{g['id']}.html", "matrix.html", group=g, m=c, B_KEYS=M.B_KEYS, B_ORDER=M.B_ORDER,
              DERIVED_A1=M.DERIVED_A1, DERIVED_A2=M.DERIVED_A2, a3=lambda cand: M.a3_text(conn, cand, c["edate"]))

    # イベント・ソース・説明
    write("events.html", "events.html", events=events,
          types=sorted({x["event_type"] for x in events}), scopes=sorted({x["scope"] for x in events}))
    from ..keywords import KEYWORDS
    log = [dict(r) for r in conn.execute("""SELECT source, step, status, detail, source_url, retrieved_at FROM fetch_log
                                            WHERE id IN (SELECT MAX(id) FROM fetch_log GROUP BY source, step)
                                            ORDER BY source, step""")]
    write("sources.html", "sources.html", status=status, log=log, cfg=cfg, keywords=KEYWORDS, years=years, cov=cov)
    write("about.html", "about.html")
    # 自治体ビュー・全体ビュー(DESIGN.md 16・17 節)
    from . import views as V
    from .. import requests as RQ
    reqs_by_code = {c: RQ.for_municipality(conn, c) for c in names}
    vdata = V.build_data(conn, pref, trows, events, districts, reqs_by_code, cfg)
    vjson = V.to_json(vdata)
    years_all = sorted({p["y"] for m in vdata["munis"].values() for s in m["series"].values() for p in s})
    recon = {c: V.reconciliation(conn, c) for c in names}
    tabs = V.district_tabs(conn, pref, names)
    vctx = dict(vjson=vjson, cfg_th=vdata["thresholds"], methods=V.METHODS, default_methods=V.DEFAULT_METHODS, other_methods=V.OTHER_METHODS,
                notes=V.NOTES_165, years_all=years_all, default_base=vdata["defaultBase"], default_level=vdata["defaultLevel"],
                election_note=vdata["election_note"])
    write("municipalities.html", "view_munis.html", tabs=tabs, names=names, recon=recon, **vctx)
    write("totals.html", "view_totals.html", **vctx)
    return {"out": str(out), "pages": len(pages), "files": pages}
