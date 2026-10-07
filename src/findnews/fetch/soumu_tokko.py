"""総務省 報道発表「特別交付税(12 月分・3 月分)交付額の決定」の取得(都道府県コードを引数に取る。既定は栃木県 09)。

URL 一覧の作り方:
  月別報道資料一覧 https://www.soumu.go.jp/menu_news/s-news/{YYMM}m.html
  (12 月分は当該年度の 12 月、3 月分は翌年 3 月)から、リンク文言に
  「令和N年度特別交付税」「交付額」を含み「震災」を含まないものを探し、
  報道資料ページ内の /main_content/*.pdf を取得する。

取得できる範囲と限界:
  - 市(都市分)は市ごとの金額が載る。那須烏山市は取得可能。
  - **町村分は都道府県合計のみ**。那珂川町を含む栃木県の町の個別額は報道資料に載っておらず、
    このソースからは取得できない(決算カードの特別交付税決算額を参照すること)。
  - 総務省サイトの「地方交付税」ページ(c-zaisei/kouhu.html)の「特別交付税3月算定分」リンクは
    算定方法に関する意見処理の資料であり、交付額ではない点に注意。
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..municipalities import master, stem
from ..prefs import short as pref_short
from ..parse import soumu_tokko as P

SOURCE = "soumu_tokko"
PROGRAM_ID = "soumu_tokko"
ARCHIVE = "https://www.soumu.go.jp/menu_news/s-news/{yymm}m.html"


def _reiwa(fy: int) -> str:
    n = fy - 2018
    return "令和元年度" if n == 1 else f"令和{n}年度"


def list_sources(fiscal_years: list[int]) -> list[dict]:
    out = []
    for fy in fiscal_years:
        for kind, yymm in (("12月", f"{fy % 100:02d}12"), ("3月", f"{(fy + 1) % 100:02d}03")):
            idx = ARCHIVE.format(yymm=yymm)
            soup = BeautifulSoup(http.get_html(idx), "html.parser")
            rel = None
            for a in soup.find_all("a"):
                t = a.get_text(strip=True)
                if _reiwa(fy) in t and "特別交付税" in t and "交付額" in t and "震災" not in t and "繰上げ" not in t:
                    rel = urljoin(idx, a["href"])
                    break
            if not rel:
                out.append({"fiscal_year": fy, "kind": kind, "url": None, "error": f"報道資料が見つからない: {idx}"})
                continue
            rsoup = BeautifulSoup(http.get_html(rel), "html.parser")
            pdfs = [urljoin(rel, a["href"]) for a in rsoup.find_all("a") if a.get("href", "").endswith(".pdf")
                    and "main_content" in a.get("href", "")]
            if not pdfs:
                out.append({"fiscal_year": fy, "kind": kind, "url": None, "error": f"PDF リンクなし: {rel}"})
                continue
            out.append({"fiscal_year": fy, "kind": kind, "url": pdfs[0], "release_page": rel,
                        "filename": f"tokko_{fy}_{'12' if kind == '12月' else '03'}.pdf"})
    return out


def local_files():
    return sorted(http.raw_dir(SOURCE).glob("tokko_*.pdf"))


def parse(paths, pref_code: str = "09") -> list[dict]:
    m = master(pref_code)
    stems = {stem(n): c for c, n in m.items() if n.endswith("市")}
    out = []
    for p in paths:
        meta = http.manifest_meta(p) or {"url": None, "retrieved_at": None}
        d = P.parse_pdf(p)
        found, warnings = P.select_pref(d["records"], pref_short(pref_code), stems)
        out.append({"path": p, "meta": meta, "fiscal_year": d["fiscal_year"], "kind": d["kind"],
                    "decision_date": d["decision_date"], "found": found, "warnings": warnings,
                    "missing": sorted(set(stems.values()) - set(found))})
    return out


def load(conn: sqlite3.Connection, parsed: list[dict], pref_code: str = "09") -> int:
    names = master(pref_code)
    conn.execute(
        """INSERT OR IGNORE INTO subsidy_programs(program_id, name, ministry, results_public, granularity, note, source_url)
           VALUES (?,?,?,?,?,?,?)""",
        (PROGRAM_ID, "特別交付税", "総務省", "市は市別、町村は都道府県計のみ", "自治体 × 年度(12 月分・3 月分)",
         "報道発表資料から取得", "https://www.soumu.go.jp/menu_news/s-news/"),
    )
    n = 0
    for d in parsed:
        conn.execute("DELETE FROM subsidy_allocations WHERE program_id=? AND fiscal_year=? AND source_url IS ? "
                     f"AND municipality_code LIKE '{pref_code}%'",
                     (PROGRAM_ID, d["fiscal_year"], d["meta"]["url"]))
        for code, r in d["found"].items():
            if d["kind"] == "12月":
                items = [("12月交付額", r.amounts[0])]
            else:
                items = [("3月交付額", r.amounts[0])] + ([("交付総額", r.amounts[1])] if len(r.amounts) > 1 else [])
            for item, amt in items:
                conn.execute(
                    """INSERT INTO subsidy_allocations(program_id, municipality_code, fiscal_year, item_name, recipients,
                       recipient_codes, attribution, amount_thousand_yen, unit_original, decision_date, raw_ref,
                       source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (PROGRAM_ID, code, d["fiscal_year"], item, names[code], code, "sole", amt, "千円",
                     d["decision_date"], f"p.{r.page}", d["meta"]["url"], d["meta"]["retrieved_at"]),
                )
                n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, fiscal_years: list[int] | None = None, offline: bool = False, force: bool = False,
        pref_code: str = "09") -> dict:
    fiscal_years = fiscal_years or list(range(2020, 2026))
    if not offline:
        srcs = list_sources(fiscal_years)
        for s in srcs:
            if not s.get("url"):
                db.log_fetch(conn, SOURCE, "list", "error", s["error"])
                continue
            try:
                http.download(s["url"], SOURCE, s["filename"], force=force)
                db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", repr(e), s["url"])
    parsed = parse(local_files(), pref_code)
    for d in parsed:
        status = "ok" if d["found"] else "error"
        db.log_fetch(conn, SOURCE, "parse", status,
                     f"{d['path'].name}: FY{d['fiscal_year']} {d['kind']} cities={len(d['found'])} "
                     f"missing={d['missing']} warnings={d['warnings']} (町は報道資料に個別額なし)",
                     d["meta"]["url"])
    n = load(conn, parsed, pref_code)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n,
            "by_file": {d["path"].name: (d["fiscal_year"], d["kind"], len(d["found"])) for d in parsed}}
