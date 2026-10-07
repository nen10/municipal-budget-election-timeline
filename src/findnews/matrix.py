"""第11節「国政選挙との分離マトリックス」(`findnews matrix --pref 09 --election <選挙ID>`)。

軸 A は多次元ラベル(11.1)。抽象ラベル(勝者・敗者)は派生列としてのみ持ち、政党名・候補者名・首長名を必ず残す。
  A1 首長の支持表明: data/manual/endorsements.csv の「収集済」行(出典 URL と引用文のあるもの)。
     首長氏名、首長の党派、支持候補の氏名・政党(公認の別)・結果、支持の形態、出典(URL・媒体・日付・引用文)。
  A2 自治体内の得票: 県選管の開票区別得票から 1 位・2 位候補の氏名・政党・得票率、差(ポイント)、
     選挙区当選者の自治体内得票率と順位。
  A3 議員側: 選挙区当選者・比例復活者の氏名・政党、選挙後の役職(positions.csv、就任日)、所管省庁、
     指標 3〜5 の所管(総務省・国土交通省)との一致。
軸 B: 指標 3(3 月分)・4・5 の選挙前後の方向(verify.py と同じ計算。各自治体を自分の過去とだけ比べる)。

選挙前後の年度は投票日から決める(2026-02-08 では 選挙前 = 2023–2025、選挙後 = 2026、指標 3 は 2025 年度 3 月分)。
複数の選挙区にまたがる自治体は選挙区ごとに 1 行。軸 B は自治体全体の値で、部分ごとには分けられない。
件数・割合は記述統計であり、有意性や因果を示すものではない。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import settings as settings_mod
from . import verify as V
from .municipalities import TOCHIGI, normalize_name

B_ORDER = ["増加", "横ばい", "減少", V.MISSING]
B_KEYS = [("3b", "指標 3 特別交付税 3月分"), ("4", "指標 4 社総交・防安交(単独策定主体)"),
          ("5", "指標 5 道路局箇所表(事業主体=市町)")]
TARGET_MINISTRIES = {"総務省": "指標 3(特別交付税)", "国土交通省": "指標 4・5(社総交・防安交・道路局配分)"}
DERIVED_A1 = ["勝者支持", "敗者支持(復活あり)", "敗者支持(復活なし)", "敗者支持(復活未確認)", "中立・非表明", "複数・不明", "未収集"]
DERIVED_A2 = ["勝者トップ", "敗者トップ(復活あり)", "敗者トップ(復活なし)", "敗者トップ(復活未確認)"]


@dataclass
class Cand:
    name: str
    legal_name: str | None
    party: str | None
    nomination: str | None
    result: str
    politician_id: str | None
    votes: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.name}({self.nomination or self.party or '党派不明'})"


@dataclass
class Part:
    code: str
    name: str
    district: int
    election_id: str
    counting_units: str
    # A1
    a1_status: str = "未収集"
    mayor_name: str = ""
    mayor_affiliation: str = ""
    a1_cands: list = field(default_factory=list)       # Cand
    a1_forms: list = field(default_factory=list)
    a1_sources: list = field(default_factory=list)     # (url, outlet, date, quote)
    a1_heading: str = "未収集"
    a1_derived: str = "未収集"
    # A2
    top1: Cand | None = None
    top1_share: float | None = None
    top2: Cand | None = None
    top2_share: float | None = None
    gap_pt: float | None = None
    winner_share: float | None = None
    winner_rank: int | None = None
    a2_heading: str = ""
    a2_derived: str = ""


def _d(s: str) -> date:
    p = [int(x) for x in s.split("-")] + [1, 1]
    return date(*p[:3])


def windows(conn, election_date: str) -> dict:
    from .fetch.mlit_grants import RELEASES
    ed = _d(election_date)
    mlit = sorted(fy for fy, (_, dd, _) in RELEASES.items() if dd and _d(dd) > ed)
    post45 = mlit[0] if mlit else None
    tk = sorted(r[0] for r in conn.execute(
        """SELECT DISTINCT fiscal_year FROM subsidy_allocations WHERE program_id='soumu_tokko' AND item_name='3月交付額'
           AND decision_date > ?""", (election_date,)))
    post3 = tk[0] if tk else None
    pre = [post45 - 3, post45 - 2, post45 - 1] if post45 else []
    pre3 = [post3 - 3, post3 - 2, post3 - 1] if post3 else []
    return {"pre_years": pre, "post_year": post45, "pre_years_tokko": pre3, "post_year_tokko": post3}


def _derive(result: str, prefix: str) -> str:
    if result == "選挙区当選":
        return f"勝者{prefix}"
    return {"落選・比例復活": f"敗者{prefix}(復活あり)", "落選": f"敗者{prefix}(復活なし)"}.get(result, f"敗者{prefix}(復活未確認)")


def districts(conn, group: str) -> dict:
    info = {}
    for r in conn.execute("SELECT election_id, election_date, district FROM elections WHERE election_id LIKE ? ORDER BY 1",
                          (group + "_%",)):
        dist = int(r["election_id"].rsplit("_", 1)[1])
        cands = {}
        for c in conn.execute(
                """SELECT candidate_name, MAX(candidate_legal_name) ln, MAX(party) party, MAX(nomination) nom,
                          MAX(result_label) res, MAX(politician_id) pid, SUM(votes) v
                   FROM election_results WHERE election_id=? GROUP BY candidate_name ORDER BY v DESC""", (r["election_id"],)):
            cands[c["candidate_name"]] = Cand(c["candidate_name"], c["ln"], c["party"], c["nom"], c["res"], c["pid"], c["v"] or 0)
        info[dist] = {"election_id": r["election_id"], "date": r["election_date"], "label": r["district"], "cands": cands,
                      "winner": next((c for c in cands.values() if c.result == "選挙区当選"), None),
                      "revived": [c for c in cands.values() if c.result == "落選・比例復活"]}
    return info


def positions_of(conn, pid: str | None, after: str) -> list[dict]:
    if not pid:
        return []
    out = []
    for r in conn.execute("""SELECT title, organization, ministry, start_date, end_date, verification, source_url
                             FROM positions WHERE politician_id=? ORDER BY start_date""", (pid,)):
        if (r["start_date"] or "") >= after[:len(r["start_date"] or "")]:
            hit = [v for k, v in TARGET_MINISTRIES.items() if k in (r["ministry"] or "")]
            out.append({**dict(r), "match": "、".join(hit) if hit else "一致なし"})
    return out


def a3_text(conn, cand: Cand | None, after: str) -> str:
    if cand is None:
        return "—"
    pos = positions_of(conn, cand.politician_id, after)
    if pos:
        return "; ".join(f"{p['title']}({p['organization'] or ''}、{p['start_date']}就任、所管 {p['ministry']}、"
                         f"指標3〜5の所管との一致: {p['match']})" for p in pos)
    note = conn.execute("SELECT note FROM politicians WHERE politician_id=?", (cand.politician_id,)).fetchone() if cand.politician_id else None
    if note and note[0] and "名簿" in note[0]:
        return "選挙後の役職の登録なし(" + note[0].split("。", 1)[1] + ")"
    return "役職データ未収集"


def build_parts(conn, group: str) -> tuple[list[Part], dict]:
    info = districts(conn, group)
    endorse = conn.execute("""SELECT * FROM endorsements WHERE (election_id=? OR election_id LIKE ?)""",
                           (group, group + "_%")).fetchall()
    parts: list[Part] = []
    for dist, di in sorted(info.items()):
        rows = conn.execute("""SELECT municipality_code, counting_unit, candidate_name, votes FROM election_results
                               WHERE election_id=? AND municipality_code IS NOT NULL""", (di["election_id"],)).fetchall()
        votes: dict[str, dict[str, float]] = {}
        units: dict[str, set] = {}
        for r in rows:
            votes.setdefault(r["municipality_code"], {}).setdefault(r["candidate_name"], 0.0)
            votes[r["municipality_code"]][r["candidate_name"]] += r["votes"] or 0
            units.setdefault(r["municipality_code"], set()).add(r["counting_unit"])
        for code, v in sorted(votes.items()):
            total = sum(v.values())
            order = sorted(v, key=lambda c: -v[c])
            p = Part(code, TOCHIGI.get(code, code), dist, di["election_id"], "、".join(sorted(units[code])))
            p.top1, p.top1_share = di["cands"][order[0]], v[order[0]] / total
            if len(order) > 1:
                p.top2, p.top2_share = di["cands"][order[1]], v[order[1]] / total
                p.gap_pt = (p.top1_share - p.top2_share) * 100
            if di["winner"]:
                p.winner_share = v.get(di["winner"].name, 0) / total
                p.winner_rank = order.index(di["winner"].name) + 1
            p.a2_heading = f"{p.top1.nomination or p.top1.party}・{p.top1.result} の候補が 1 位"
            p.a2_derived = _derive(p.top1.result, "トップ")
            # A1
            names = {normalize_name(c.name): c for c in di["cands"].values()}
            names.update({normalize_name(c.legal_name): c for c in di["cands"].values() if c.legal_name})
            mine = [e for e in endorse if e["municipality_code"] == code]
            collected = [e for e in mine if e["collection_status"] in ("収集済", "中立・非表明(出典あり)")]
            relevant = [e for e in collected if e["collection_status"] != "収集済" or
                        normalize_name(e["candidate_name"] or "") in names]
            if relevant:
                p.mayor_name = "、".join(sorted({e["mayor_name"] for e in relevant if e["mayor_name"]}))
                p.mayor_affiliation = "、".join(sorted({e["mayor_affiliation"] for e in relevant if e["mayor_affiliation"]})) or "未収集"
                p.a1_sources = sorted({(e["source_url"], e["outlet"], e["evidence_date"], e["quote"]) for e in relevant})
                p.a1_forms = sorted({e["endorsement_form"] for e in relevant if e["endorsement_form"]})
                sup = []
                for e in relevant:
                    if e["collection_status"] == "収集済":
                        c = names[normalize_name(e["candidate_name"])]
                        if c not in sup:
                            sup.append(c)
                p.a1_cands = sup
                if sup and len(sup) == 1:
                    p.a1_status, p.a1_heading = "収集済", f"{sup[0].nomination or sup[0].party}・{sup[0].result} を支持"
                    p.a1_derived = _derive(sup[0].result, "支持")
                elif sup:
                    p.a1_status = "収集済"
                    p.a1_heading = "複数を支持: " + " / ".join(f"{c.nomination}・{c.result}" for c in sup)
                    p.a1_derived = "複数・不明"
                else:
                    p.a1_status, p.a1_heading, p.a1_derived = "中立・非表明(出典あり)", "中立・非表明(出典あり)", "中立・非表明"
            elif mine and mine[0]["mayor_name"]:
                p.mayor_name = mine[0]["mayor_name"]
            parts.append(p)
    cnt = {}
    for p in parts:
        cnt[p.code] = cnt.get(p.code, 0) + 1
    for p in parts:
        if cnt[p.code] > 1:
            p.name = f"{p.name}({p.district}区部分)"
    return parts, info


def _t(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
    return "\n".join(out)


def _pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def _a1_entry(p: Part) -> str:
    sup = "、".join(c.label for c in p.a1_cands) or "—"
    return f"{p.name}(首長: {p.mayor_name or '未収集'} / 支持: {sup})"


def _a2_entry(p: Part) -> str:
    s = f"{p.name}(1位 {p.top1.label} {_pct(p.top1_share)}"
    if p.top2:
        s += f" / 2位 {p.top2.label} {_pct(p.top2_share)} / 差 {p.gap_pt:.1f}pt"
    return s + ")"


def _matrix(parts: list[Part], heading_attr: str, dirs: dict, entry, order: list[str] | None = None) -> str:
    cats = order or sorted({getattr(p, heading_attr) for p in parts}, key=lambda c: (c == "未収集", c))
    rows = []
    for cat in cats:
        ps = [p for p in parts if getattr(p, heading_attr) == cat]
        cells = []
        for b in B_ORDER:
            sel = [p for p in ps if dirs[p.code] == b]
            cells.append(f"{len(sel)}: " + "<br>".join(entry(p) for p in sel) if sel else "0")
        n, n_dec = len(ps), sum(1 for p in ps if dirs[p.code] == "減少")
        n_obs = sum(1 for p in ps if dirs[p.code] != V.MISSING)
        rows.append([cat, n] + cells + [f"{n_dec}/{n}" + (f" = {n_dec / n:.0%}" if n else ""),
                                        f"{n_dec}/{n_obs}" + (f" = {n_dec / n_obs:.0%}" if n_obs else "")])
    return _t(["行(軸 A)", "計"] + B_ORDER + ["減少の割合(行の全件中)", f"減少の割合({V.MISSING}を除く)"], rows)


def run(conn: sqlite3.Connection, group: str, pref_code: str = "09", out_dir: str | Path | None = None) -> dict:
    from . import config
    cfg = settings_mod.load()
    th = float(cfg["verification"]["direction_threshold"])
    parts, info = build_parts(conn, group)
    if not parts:
        raise SystemExit(f"選挙 {group} の結果が DB にない(findnews fetch tochigi-election を先に実行)")
    edate = next(iter(info.values()))["date"]
    w = windows(conn, edate)
    yrs = [y for y in w["pre_years"] + w["pre_years_tokko"] + [w["post_year"], w["post_year_tokko"]] if y]
    period = (min(yrs + [cfg["verification"]["period"][0]]), max(yrs + [cfg["verification"]["period"][1]]))
    codes = sorted({p.code for p in parts})
    obs = V.build(conn, codes, period)
    pp = {}
    for c in codes:
        pp[(c, "3b")] = V.pre_post(obs[(c, "3b")], w["pre_years_tokko"], w["post_year_tokko"], th) if w["post_year_tokko"] else None
        for k in ("4", "5"):
            pp[(c, k)] = V.pre_post(obs[(c, k)], w["pre_years"], w["post_year"], th) if w["post_year"] else None
    dirs = {k: {c: (pp[(c, k)]["direction"] if pp[(c, k)] else V.MISSING) for c in codes} for k, _ in B_KEYS}

    out_dir = Path(out_dir or config.PROCESSED_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"matrix_tochigi_{group}.md"
    csv_path = out_dir / f"matrix_tochigi_{group}.csv"
    n_unc = sum(1 for p in parts if p.a1_status == "未収集")

    L = [f"# 国政選挙との分離マトリックス: {group}(投票日 {edate}、都道府県コード {pref_code})", "",
         f"**A1(首長の支持表明)未収集: {n_unc} / {len(parts)} 行**(行 = 自治体。複数の選挙区にまたがる自治体は選挙区ごとに 1 行)", "",
         "- A1: data/manual/endorsements.csv の収集済の行(出典 URL と引用文があるもの)のみ。未収集は「中立・非表明」に含めず別区分。",
         "- A2: 県選管の開票区別得票(確定値)から機械的に算出。得票率 = 候補の得票 ÷ 当該自治体(選挙区内の部分)の候補者得票合計。",
         "- 候補の政党表示は県選管の候補者届出状況公表票による(「◯◯公認」= 政党届出、「無所属」= 本人届出)。他党の推薦は未収集。",
         "- 比例復活は data/manual/election_outcomes.csv に出典つきで登録したもの、重複立候補なしの落選者は「落選」。",
         f"- 軸 B: 各自治体を自分の過去と比べた方向(閾値 ±{th * 100:.1f}%)。指標 4・5 は {w['pre_years']} 年度平均 vs "
         f"{w['post_year']} 年度、指標 3 は {w['pre_years_tokko']} 年度の 3 月分平均 vs {w['post_year_tokko']} 年度 3 月分。"
         "他自治体を基準にした正規化はしていない。",
         "- 「減少の割合」は行内の件数の比(記述統計)。有意性や因果を示すものではない。", ""]

    L += ["## A3 選挙区の当選者・比例復活者と選挙後の役職", ""]
    rows = []
    for dist, di in sorted(info.items()):
        wn = di["winner"]
        rows.append([di["label"], "選挙区当選", wn.label if wn else "—", a3_text(conn, wn, edate)])
        for r in di["revived"]:
            rows.append([di["label"], "落選・比例復活", r.label, a3_text(conn, r, edate)])
    L += [_t(["選挙区", "結果", "氏名(政党)", "選挙後の役職(就任日、所管、指標 3〜5 の所管との一致)"], rows), ""]

    for key, label in B_KEYS:
        d = dirs[key]
        L += [f"## {label}", "",
              "### A1 × B: 行 = 支持候補の政党・結果(全選挙区)", "", _matrix(parts, "a1_heading", d, _a1_entry), "",
              "### A2 × B: 行 = 自治体内 1 位候補の政党・結果(全選挙区)", "", _matrix(parts, "a2_heading", d, _a2_entry), "",
              "### 補助: 勝者・敗者の派生区分", "", "A1(派生)", "", _matrix(parts, "a1_derived", d, _a1_entry, DERIVED_A1), "",
              "A2(派生)", "", _matrix(parts, "a2_derived", d, _a2_entry, DERIVED_A2), ""]
        for dist, di in sorted(info.items()):
            ps = [p for p in parts if p.district == dist]
            wn = di["winner"]
            head = (f"### {di['label']}: 当選 {wn.label if wn else '—'} [{a3_text(conn, wn, edate)}]"
                    + "".join(f" / 比例復活 {r.label} [{a3_text(conn, r, edate)}]" for r in di["revived"]))
            L += [head, "", "A1 × B", "", _matrix(ps, "a1_heading", d, _a1_entry), "",
                  "A2 × B", "", _matrix(ps, "a2_heading", d, _a2_entry), ""]

    L += ["## 自治体別の一覧", ""]
    rows = []
    for p in parts:
        r = [p.name, p.district, p.mayor_name or "未収集", p.mayor_affiliation or "未収集", p.a1_heading,
             "、".join(c.label for c in p.a1_cands) or "—", "、".join(p.a1_forms) or "—",
             f"{p.top1.label} {_pct(p.top1_share)}", f"{p.top2.label} {_pct(p.top2_share)}" if p.top2 else "—",
             f"{p.gap_pt:.1f}" if p.gap_pt is not None else "—",
             f"{_pct(p.winner_share)}({p.winner_rank}位)" if p.winner_rank else "—"]
        for key, _ in B_KEYS:
            x = pp[(p.code, key)]
            r.append(f"{x['direction']}({V._pct(x['rate'])}; 前 {V._n(x['pre'])} → 後 {V._n(x['post'])})" if x else V.MISSING)
        rows.append(r)
    L += [_t(["自治体", "区", "首長", "首長の党派", "A1 行見出し", "支持候補", "支持の形態", "1位候補 得票率", "2位候補 得票率",
              "差(pt)", "当選者の得票率(順位)", "指標3", "指標4", "指標5"], rows), ""]
    L += ["## A1 の出典(引用文は原文)", ""]
    srcs = {}
    for p in parts:
        for s in p.a1_sources:
            srcs.setdefault(s, []).append(p.name)
    for (url, outlet, dt, quote), ps in sorted(srcs.items()):
        L.append(f"- {outlet} {dt} {url}  対象: {'、'.join(ps)}  引用:「{quote}」")
    if not srcs:
        L.append("(出典つきの登録なし)")
    md_path.write_text("\n".join(L) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        wr = csv.writer(f)
        head = ["election", "election_date", "pref_code", "district", "election_id", "municipality_code", "municipality_part",
                "counting_units",
                "A1_collection_status", "A1_mayor_name", "A1_mayor_affiliation", "A1_candidates", "A1_candidate_parties",
                "A1_candidate_results", "A1_forms", "A1_source_urls", "A1_outlets", "A1_dates", "A1_quotes", "A1_heading",
                "A1_derived",
                "A2_top1_name", "A2_top1_party", "A2_top1_nomination", "A2_top1_result", "A2_top1_share",
                "A2_top2_name", "A2_top2_party", "A2_top2_nomination", "A2_top2_result", "A2_top2_share", "A2_gap_pt",
                "A2_winner_share", "A2_winner_rank", "A2_heading", "A2_derived",
                "A3_winner_name", "A3_winner_party", "A3_winner_positions", "A3_revived_names", "A3_revived_parties",
                "A3_revived_positions"]
        for key, _ in B_KEYS:
            head += [f"ind{key}_pre_avg", f"ind{key}_post", f"ind{key}_diff", f"ind{key}_rate", f"ind{key}_direction"]
        head += ["pre_years_ind45", "post_year_ind45", "pre_years_ind3", "post_year_ind3", "threshold"]
        wr.writerow(head)
        for p in parts:
            di = info[p.district]
            wn = di["winner"]
            row = [group, edate, pref_code, p.district, p.election_id, p.code, p.name, p.counting_units,
                   p.a1_status, p.mayor_name, p.mayor_affiliation, "|".join(c.name for c in p.a1_cands),
                   "|".join(c.nomination or "" for c in p.a1_cands), "|".join(c.result for c in p.a1_cands),
                   "|".join(p.a1_forms), "|".join(s[0] for s in p.a1_sources), "|".join(s[1] or "" for s in p.a1_sources),
                   "|".join(s[2] or "" for s in p.a1_sources), "|".join(s[3] or "" for s in p.a1_sources),
                   p.a1_heading, p.a1_derived,
                   p.top1.name, p.top1.party, p.top1.nomination, p.top1.result, round(p.top1_share, 4),
                   p.top2.name if p.top2 else "", p.top2.party if p.top2 else "", p.top2.nomination if p.top2 else "",
                   p.top2.result if p.top2 else "", round(p.top2_share, 4) if p.top2 else "",
                   round(p.gap_pt, 2) if p.gap_pt is not None else "",
                   round(p.winner_share, 4) if p.winner_share is not None else "", p.winner_rank or "",
                   p.a2_heading, p.a2_derived,
                   wn.name if wn else "", wn.party if wn else "", a3_text(conn, wn, edate),
                   "|".join(r.name for r in di["revived"]), "|".join(r.party or "" for r in di["revived"]),
                   " / ".join(a3_text(conn, r, edate) for r in di["revived"])]
            for key, _ in B_KEYS:
                x = pp[(p.code, key)]
                row += ([x["pre"], x["post"], x["diff"], None if x["rate"] is None else round(x["rate"], 4), x["direction"]]
                        if x else ["", "", "", "", V.MISSING])
            row += [json.dumps(w["pre_years"]), w["post_year"], json.dumps(w["pre_years_tokko"]), w["post_year_tokko"], th]
            wr.writerow(["" if v is None else v for v in row])
    return {"md": str(md_path), "csv": str(csv_path), "rows": len(parts), "a1_uncollected": n_unc, "windows": w}
