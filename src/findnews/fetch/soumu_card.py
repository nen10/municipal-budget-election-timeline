"""総務省 市町村決算カード(都道府県別 Excel)の取得。都道府県コードを引数に取る(既定は栃木県 09)。

一覧ページ: https://www.soumu.go.jp/iken/zaisei/card.html
  → 年度別ページ(例: 令和6年度 https://www.soumu.go.jp/iken/zaisei/card-25.html)
  → 「■栃木県」の PDF リンクの直後にある Excel(.xlsx)リンク。
2026-10-08 時点の最新は令和6年度(2024 年度)決算。令和7年度(2026 年 2 月の衆院選を含む年度)の
決算カードは未公表のため、選挙後の決算値はこのソースからは得られない。

robots.txt(https://www.soumu.go.jp/robots.txt)は ia_archiver のみ拒否。総務省サイトの
コンテンツは政府標準利用規約に基づき出典明記で利用可。
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..parse import soumu_card as P
from ..parse.common import era_year_to_ad
from ..prefs import name as pref_name

SOURCE = "soumu_card"
INDEX_URL = "https://www.soumu.go.jp/iken/zaisei/card.html"
def list_sources(n_years: int = 5, pref_code: str = "09", years: list[int] | None = None) -> list[dict]:
    pref_label = pref_name(pref_code)
    soup = BeautifulSoup(http.get_html(INDEX_URL), "html.parser")
    year_pages = []
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        if t.endswith("市町村決算カード") and (fy := era_year_to_ad(t)):
            year_pages.append((fy, urljoin(INDEX_URL, a["href"])))
    year_pages.sort(reverse=True)
    out = []
    pages = [yp for yp in year_pages if yp[0] in years] if years else year_pages[:n_years]
    for fy, page in pages:
        psoup = BeautifulSoup(http.get_html(page), "html.parser")
        anchors = psoup.find_all("a")
        for i, a in enumerate(anchors):
            if pref_label in a.get_text():
                for b in anchors[i + 1:i + 4]:
                    href = b.get("href", "")
                    if re.search(r"\.xlsx?$", href):
                        url = urljoin(page, href)
                        ext = href.rsplit(".", 1)[1]
                        out.append({"fiscal_year": fy, "url": url, "page": page,
                                    "filename": f"card_{fy}_{pref_code}.{ext}", "pref_code": pref_code})
                        break
                break
    return out


def download(sources: list[dict], force: bool = False) -> list[http.RawFile]:
    return [http.download(s["url"], SOURCE, s["filename"], force=force) for s in sources]


def local_files(pref_code: str = "09"):
    d = http.raw_dir(SOURCE)
    return sorted(p for p in d.glob(f"card_*_{pref_code}.xls*"))


def parse(paths, pref_code: str = "09") -> list[tuple[P.CardRecord, dict]]:
    out = []
    for p in paths:
        meta = http.manifest_meta(p) or {"url": None, "retrieved_at": None}
        for rec in P.parse_workbook(p, pref_code):
            out.append((rec, meta))
    return out


def load(conn: sqlite3.Connection, parsed) -> int:
    db.ensure_municipalities(conn)
    n = 0
    latest: dict[str, int] = {}
    for rec, meta in parsed:
        conn.execute(
            """INSERT OR REPLACE INTO municipality_fiscal
               (code, fiscal_year, item, value, unit, source, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (rec.code, rec.fiscal_year, rec.item, rec.value, rec.unit, SOURCE, meta["url"], meta["retrieved_at"]),
        )
        latest[rec.code] = max(latest.get(rec.code, 0), rec.fiscal_year)
        n += 1
    # municipalities の最新人口・財政力指数を更新
    for code, fy in latest.items():
        row = {r["item"]: r for r in conn.execute(
            "SELECT item, value, source_url, retrieved_at FROM municipality_fiscal WHERE code=? AND fiscal_year=? AND source=?",
            (code, fy, SOURCE))}
        pop = row.get("住民基本台帳人口")
        fci = row.get("財政力指数")
        conn.execute(
            """UPDATE municipalities SET population=?, population_date=?, fiscal_capability_index=?,
               source_url=?, retrieved_at=? WHERE code=?""",
            (pop["value"] if pop else None, f"{fy + 1}-01-01" if pop else None,
             fci["value"] if fci else None,
             (fci or pop)["source_url"] if (fci or pop) else None,
             (fci or pop)["retrieved_at"] if (fci or pop) else None, code),
        )
    conn.commit()
    return n


def run(conn: sqlite3.Connection, n_years: int = 5, offline: bool = False, force: bool = False,
        pref_code: str = "09", years: list[int] | None = None) -> dict:
    if not offline:
        try:
            srcs = list_sources(n_years, pref_code, years)
            db.log_fetch(conn, SOURCE, "list", "ok", f"{len(srcs)} files: " + ", ".join(str(s['fiscal_year']) for s in srcs), INDEX_URL)
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, SOURCE, "list", "error", repr(e), INDEX_URL)
            raise
        for s in srcs:
            try:
                http.download(s["url"], SOURCE, s["filename"], force=force)
                db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", repr(e), s["url"])
    files = local_files(pref_code)
    parsed = parse(files, pref_code)
    years = sorted({r.fiscal_year for r, _ in parsed})
    codes = {r.code for r, _ in parsed}
    db.log_fetch(conn, SOURCE, "parse", "ok" if parsed else "error",
                 f"{len(files)} files, {len(codes)} municipalities, years={years}")
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(files), "municipalities": len(codes), "years": years, "rows": n}
