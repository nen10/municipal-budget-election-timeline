"""国交省 道路局の年度当初配分「箇所表」(都道府県別。既定は栃木県 09)の取得。

出典は fetch/mlit_grants.py と同じ「国土交通省関係予算の配分について」→「事業実施箇所」→ 都道府県別 PDF の
先頭にある道路局セクション(「令和N年度 箇所表」)。依頼文の「道路関係予算 配分」の都道府県別箇所表に相当する。
道路局の報道発表 PDF(例: 令和8年度 https://www.mlit.go.jp/report/press/content/001994794.pdf)は
整備局別の総括で、市町別の箇所は載っていない。

ダウンロードは mlit_grants と共通(data/raw/mlit_grants/kasho_<年度>_09.pdf)。
事業主体の判定規則は findnews.parse.mlit_road の docstring を参照。単位は百万円で、DB には千円で保存する。

確認できた限界:
  - 令和7年度(FY2025)の栃木県 PDF の道路局セクションには「地方創生道整備推進交付金(市町村道分)」の表がない
    (他年度にはある)。
  - 補助事業のうち国道・県道・都市計画道路の行は事業主体の記載がなく、市町には帰属させない(attribution='unknown')。
"""

from __future__ import annotations

import re
import sqlite3

from .. import db, http
from ..municipalities import master
from ..parse import mlit_road as P
from . import mlit_grants

SOURCE = "mlit_road"
PROGRAM_ID = "mlit_road"


def local_files(pref_code: str = "09"):
    return mlit_grants.local_files(pref_code)


def parse(paths, pref_code: str = "09") -> list[dict]:
    out = []
    for p in paths:
        fy = int(re.search(r"kasho_(\d{4})_", p.name).group(1))
        out.append({"path": p, "fiscal_year": fy, "rows": P.parse_pdf(p, pref_code),
                    "meta": http.manifest_meta(p) or {"url": None, "retrieved_at": None},
                    "decision_date": mlit_grants.RELEASES.get(fy, (None, None, None))[1]})
    return out


def load(conn: sqlite3.Connection, parsed: list[dict]) -> int:
    conn.execute(
        """INSERT OR IGNORE INTO subsidy_programs(program_id, name, ministry, results_public, granularity, note, source_url)
           VALUES (?,?,?,?,?,?,?)""",
        (PROGRAM_ID, "道路関係予算 当初配分(道路局 箇所表)", "国土交通省 道路局", "都道府県別 PDF で箇所ごとに公表",
         "箇所 × 事業主体 × 年度(百万円)", "事業主体の記載がない行は自治体に帰属させない",
         "https://www.mlit.go.jp/report/press/kanbo05_hh_000304.html"))
    n = 0
    for d in parsed:
        url, ra = d["meta"]["url"], d["meta"]["retrieved_at"]
        conn.execute("DELETE FROM subsidy_allocations WHERE program_id=? AND fiscal_year=? AND source_url IS ?",
                     (PROGRAM_ID, d["fiscal_year"], url))
        for r in d["rows"]:
            amt = r.amount_million_yen * 1000 if r.amount_million_yen is not None else None
            recips = r.entity or r.location or ""
            codes = [r.municipality_code] if r.municipality_code else r.location_codes
            conn.execute(
                """INSERT INTO subsidy_allocations(program_id, municipality_code, fiscal_year, item_name, recipients,
                   recipient_codes, attribution, amount_thousand_yen, unit_original, n_locations, decision_date, raw_ref,
                   source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (PROGRAM_ID, r.municipality_code, d["fiscal_year"], f"{r.work_type}|{r.item_name}", recips,
                 ",".join(c for c in codes if c), r.attribution, amt, "百万円", 1, d["decision_date"],
                 f"p.{r.page} table={r.table} basis={r.basis} route={r.route or ''} location={r.location or ''}",
                 url, ra))
            n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, offline: bool = False, force: bool = False, pref_code: str = "09",
        fiscal_years: list[int] | None = None) -> dict:
    if not offline:
        mlit_grants.run_download(conn, fiscal_years, force, pref_code)
    parsed = parse(local_files(pref_code), pref_code)
    for d in parsed:
        sole = sum(1 for r in d["rows"] if r.attribution == "sole")
        db.log_fetch(conn, SOURCE, "parse", "ok" if d["rows"] else "error",
                     f"{d['path'].name}: rows={len(d['rows'])} sole={sole} "
                     f"tables={sorted({r.table for r in d['rows']})}", d["meta"]["url"])
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n, "years": [d["fiscal_year"] for d in parsed],
            "sole_rows": sum(1 for d in parsed for r in d["rows"] if r.attribution == "sole")}
