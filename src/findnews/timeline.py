"""差分時系列と政局イベントの対応(DESIGN.md 第13節)。

各自治体 × 指標について、観測時点(observations)を decided_date 順に並べ、直前の観測時点との差分
Δx_t = x_t − x_{t−1}、変化率 Δx_t / x_{t−1}、方向(第10節の閾値)を出す。平均との比較はしない。
差分の対応期間は decided_date(t−1) の翌日から decided_date(t) まで。期間と重なる日付のイベントを対応付ける
(月単位・期間のイベントは期間が重なれば対応)。最初の観測時点は対応期間なし。

イベントがその自治体に関係するかは scope で決める:
  national → 全自治体 / prefecture → 同じ都道府県 / district → その選挙区に属する自治体
  (イベント日以前で最も近い選挙の区割り。なければ最も古い選挙)/ municipality → その自治体
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import settings as settings_mod
from .municipalities import master
from .prefs import name as pref_name
from .sources.registry import INDICATORS

MISSING = "未取得"
TIMELINE_COLUMNS = ["pref_code", "municipality_code", "municipality_name", "indicator_id", "indicator_no", "indicator_label",
                    "period_start", "period_end", "decided_date", "decided_date_is_proxy", "decided_date_basis",
                    "published_date", "value", "unit", "count", "missing_reason", "delta", "delta_pct", "direction",
                    "window_start", "window_end", "event_ids", "source_url"]


@dataclass
class Row:
    pref_code: str
    municipality_code: str
    municipality_name: str
    indicator_id: str
    indicator_no: str
    indicator_label: str
    period_start: str
    period_end: str
    decided_date: str | None
    decided_date_is_proxy: int
    decided_date_basis: str
    published_date: str | None
    value: float | None
    unit: str
    count: int | None
    missing_reason: str | None
    delta: float | None = None
    delta_pct: float | None = None
    direction: str = MISSING
    window_start: str | None = None
    window_end: str | None = None
    event_ids: list = field(default_factory=list)
    source_url: str | None = None


def direction(pct, threshold: float) -> str:
    if pct is None:
        return MISSING
    return "増加" if pct >= threshold else "減少" if pct <= -threshold else "横ばい"


def _d(s: str | None) -> date | None:
    if not s:
        return None
    p = [int(x) for x in s.split("-")] + [1, 1]
    return date(*p[:3])


def diff_series(obs: list[dict], threshold: float) -> list[dict]:
    """1 つの自治体 × 指標の観測値(decided_date 順)に差分と対応期間を付ける。"""
    out = []
    prev = None
    for o in sorted(obs, key=lambda o: (o["decided_date"] or "9999", o["period_start"])):
        o = dict(o)
        if prev is not None:
            o["window_start"] = (_d(prev["decided_date"]) + timedelta(days=1)).isoformat() if prev["decided_date"] else None
            o["window_end"] = o["decided_date"]
            if o["value"] is not None and prev["value"] is not None:
                o["delta"] = o["value"] - prev["value"]
                o["delta_pct"] = (o["delta"] / prev["value"]) if prev["value"] != 0 else None
        o.setdefault("delta", None)
        o.setdefault("delta_pct", None)
        o.setdefault("window_start", None)
        o.setdefault("window_end", None)
        o["direction"] = direction(o["delta_pct"], threshold)
        out.append(o)
        prev = o
    return out


def district_members(conn: sqlite3.Connection, pref_code: str) -> list[tuple[str, str, set]]:
    """[(election_date, district_label, {municipality_code})] 選挙ごとの区割り。"""
    out = []
    for e in conn.execute("SELECT election_id, election_date, district FROM elections WHERE pref_code=?", (pref_code,)):
        codes = {r[0] for r in conn.execute(
            "SELECT DISTINCT municipality_code FROM election_results WHERE election_id=? AND municipality_code IS NOT NULL",
            (e[0],))}
        out.append((e[1], e[2], codes))
    return sorted(out)


def relevant(ev: dict, code: str, pref_code: str, districts: list) -> bool:
    sc = ev["scope"]
    if sc == "national":
        return True
    if sc == "prefecture":
        return ev["pref_code"] == pref_code
    if sc == "municipality":
        return ev["municipality_code"] == code
    if sc == "district":
        cands = [d for d in districts if d[1] == ev["district"]]
        if not cands:
            return False
        before = [d for d in cands if d[0] <= ev["date"]]
        dd = before[-1] if before else cands[0]
        if ev.get("generated_by") and ev["event_id"].startswith("gen-election-"):
            dd = next((d for d in cands if d[0] == ev["date"]), dd)
        return code in dd[2]
    return False


def overlaps(ev: dict, ws: str | None, we: str | None) -> bool:
    if not ws or not we:
        return False
    s, e = ev["date"], ev["end_date"] or ev["date"]
    return s <= we and e >= ws


def load_events(conn, pref_code: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE pref_code=? OR pref_code IS NULL OR scope='national' ORDER BY date, event_id", (pref_code,))]


def build(conn: sqlite3.Connection, pref_code: str, munis: list[str] | None = None,
          indicators: list[str] | None = None, threshold: float | None = None) -> tuple[list[Row], list[dict]]:
    th = threshold if threshold is not None else float(settings_mod.load()["verification"]["direction_threshold"])
    names = master(pref_code)
    codes = munis or sorted(names)
    inds = indicators or list(INDICATORS)
    events = load_events(conn, pref_code)
    districts = district_members(conn, pref_code)
    rows: list[Row] = []
    for code in codes:
        rel = [e for e in events if relevant(e, code, pref_code, districts)]
        for ind in inds:
            obs = [dict(r) for r in conn.execute(
                "SELECT * FROM observations WHERE municipality_code=? AND indicator_id=?", (code, ind))]
            for o in diff_series(obs, th):
                no, label, _ = INDICATORS[ind]
                ev_ids = [e["event_id"] for e in rel if overlaps(e, o["window_start"], o["window_end"])]
                rows.append(Row(pref_code, code, names.get(code, code), ind, no, label, o["period_start"], o["period_end"],
                                o["decided_date"], o["decided_date_is_proxy"], o["decided_date_basis"], o["published_date"],
                                o["value"], o["unit"], o["count"], o["missing_reason"], o["delta"], o["delta_pct"],
                                o["direction"], o["window_start"], o["window_end"], ev_ids, o["source_url"]))
    return rows, events


def first_after(rows: list[Row], code: str, indicator: str, after: str) -> Row | None:
    """after(選挙投票日)より後に決まった最初の観測時点の行(その差分は直前時点比)。"""
    c = [r for r in rows if r.municipality_code == code and r.indicator_id == indicator and r.decided_date
         and r.decided_date > after]
    return min(c, key=lambda r: r.decided_date) if c else None


# ------------------------------------------------------------------ 出力

def _n(v):
    return "—" if v is None else f"{v:,.0f}"


def _pct(v):
    return "未定義" if v is None else f"{v * 100:+.1f}%"


def _t(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x).replace("|", "/").replace("\n", " ") for x in r) + " |" for r in rows]
    return "\n".join(out)


def _ev_short(e: dict) -> str:
    when = e["date"] + (f"〜{e['end_date']}" if e["end_date"] else "")
    if e["event_type"] in ("衆院選投票", "参院選投票") and e["summary"]:
        return f"{when} [{e['event_type']}] " + e["summary"].split("。候補者")[0]
    who = e["actor_name"] or ""
    if e["actor_party"]:
        who += f"({e['actor_party']})"
    if e["actor_role"]:
        who += f" {e['actor_role']}"
    tail = f" → {e['counterpart_name']}({e['counterpart_role']})" if e["counterpart_name"] else ""
    if not who and e["summary"]:
        who = e["summary"]
    return f"{when} [{e['event_type']}] {who}{tail}".strip()


def write_outputs(conn, pref_code: str, rows: list[Row], events: list[dict], out_dir: Path, munis=None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"timeline_{pref_code}.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(TIMELINE_COLUMNS)
        for r in rows:
            d = asdict(r)
            d["event_ids"] = ";".join(r.event_ids)
            w.writerow(["" if d[c] is None else d[c] for c in TIMELINE_COLUMNS])
    ev = {e["event_id"]: e for e in events}
    mdir = out_dir / f"timeline_{pref_code}"
    mdir.mkdir(exist_ok=True)
    districts = district_members(conn, pref_code)
    th = float(settings_mod.load()["verification"]["direction_threshold"])
    for code in sorted({r.municipality_code for r in rows}):
        rs = [r for r in rows if r.municipality_code == code]
        name = rs[0].municipality_name
        L = [f"# {name}({code}) 差分時系列と政局イベント", "",
             f"- 都道府県: {pref_name(pref_code)}({pref_code})。各指標を直前の観測時点と比べた差分(Δ)。平均との比較はしていない。",
             f"- 方向の閾値 ±{th * 100:.1f}%。前の観測値が 0 または未取得なら変化率は「未定義」、方向は「未取得」。",
             "- 対応期間 = 直前の観測時点の decided_date の翌日 〜 当該観測時点の decided_date。期間と日付が重なるイベントを列挙"
             "(月・期間単位のイベントは重なれば対応)。対応は日付の機械的な重なりで、関係を示すものではない。",
             "- 金額は千円。decided_date の「代用」は公表日で代用したもの(根拠は CSV の decided_date_basis)。", ""]
        for ind in dict.fromkeys(r.indicator_id for r in rs):
            ir = [r for r in rs if r.indicator_id == ind]
            L += [f"## 指標 {ir[0].indicator_no} {ir[0].indicator_label}(`{ind}`)", ""]
            tab = []
            for r in ir:
                val = _n(r.value) if r.value is not None else f"未取得({r.missing_reason or '理由不明'})"
                cnt = f" ({r.count}件)" if r.count is not None and ind.startswith("mlit") else ""
                evs = "<br>".join(_ev_short(ev[i]) for i in r.event_ids) or "—"
                tab.append([f"{r.period_start}〜{r.period_end}", (r.decided_date or "—") + ("(代用)" if r.decided_date_is_proxy else ""),
                            val + cnt, _n(r.delta) if r.delta is not None else "—", _pct(r.delta_pct) if r.delta is not None else "—",
                            r.direction if r.window_start else "—(最初の観測時点)",
                            f"{r.window_start}〜{r.window_end}" if r.window_start else "—", evs])
            L += [_t(["対象期間", "decided_date", "値", "Δ", "変化率", "方向", "対応期間", "対応期間内のイベント"], tab), ""]
        rel = [e for e in events if relevant(e, code, pref_code, districts)]
        L += ["## この自治体に関係する全イベント(日付順)", ""]
        L += [_t(["日付", "種別", "範囲", "当事者(政党、役職)", "相手方", "内容", "引用", "出典"],
                 [[e["date"] + (f"〜{e['end_date']}" if e["end_date"] else ""), e["event_type"], e["scope"],
                   f"{e['actor_name'] or ''}({e['actor_party'] or ''}、{e['actor_role'] or ''})" if e["actor_name"] else "—",
                   f"{e['counterpart_name']}({e['counterpart_role']})" if e["counterpart_name"] else "—",
                   e["summary"] or "", f"「{e['quote']}」" if e["quote"] else "—",
                   f"{e['outlet'] or ''} {e['source_date'] or ''} {e['source_url'] or ''}"] for e in rel]), ""]
        (mdir / f"{code}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_events_md(conn, pref_code, events, out_dir / f"events_{pref_code}.md")
    return {"csv": str(csv_path), "md_dir": str(mdir), "rows": len(rows), "events": len(events)}


def write_events_md(conn, pref_code: str, events: list[dict], path: Path) -> None:
    gen = sum(1 for e in events if e["generated_by"])
    L = [f"# 政局イベント一覧({pref_name(pref_code)}、{pref_code})", "",
         f"- 登録 {len(events)} 件(自動生成 {gen} 件、手作業 {len(events) - gen} 件)。出典のないイベントは登録していない。",
         "- 自動生成: 衆院選投票(県選管)、役職就任・内閣発足(positions.csv の出典つきの行)、予算配分公表(国交省・総務省)。", ""]
    L.append(_t(["日付", "精度", "種別", "範囲", "選挙区/自治体", "当事者(政党、役職)", "相手方(役職)", "内容", "引用",
                 "出典", "備考", "生成"],
                [[e["date"] + (f"〜{e['end_date']}" if e["end_date"] else ""), e["date_precision"] or "", e["event_type"],
                  e["scope"], e["district"] or e["municipality_code"] or "",
                  f"{e['actor_name'] or ''}({e['actor_party'] or ''}、{e['actor_role'] or ''})" if e["actor_name"] else "—",
                  f"{e['counterpart_name']}({e['counterpart_role']})" if e["counterpart_name"] else "—",
                  e["summary"] or "", f"「{e['quote']}」" if e["quote"] else "—",
                  f"{e['outlet'] or ''} {e['source_date'] or ''} {e['source_url'] or ''}", e["note"] or "",
                  e["generated_by"] or "手作業"] for e in events]))
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def run(conn, pref_code: str, munis=None, indicators=None, out_dir: Path | None = None) -> dict:
    from . import config
    rows, events = build(conn, pref_code, munis, indicators)
    if not rows:
        return {"status": "未取得", "reason": f"都道府県 {pref_code} の observations がない(findnews fetch --pref {pref_code} を先に実行)",
                "events": len(events)}
    return write_outputs(conn, pref_code, rows, events, Path(out_dir or config.PROCESSED_DIR), munis)
