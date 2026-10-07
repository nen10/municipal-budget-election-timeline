"""国土交通省 社会資本整備総合交付金・防災・安全交付金(当初予算の配分)と道路メンテナンス事業補助の取得。

出典の構造(2026-10-08 確認):
  報道発表「令和N年度国土交通省関係予算の配分について」(4 月上旬)
    → 「事業実施箇所」ページ(都道府県リンク一覧)
    → 都道府県別 PDF(例: 令和8年度 栃木 https://www.mlit.go.jp/page/content/001994561.pdf)
  PDF 末尾に「令和N年度当初予算 社会資本整備総合交付金の配分」表(計画名・計画策定主体・配分国費)があり、
  補助事業の箇所表に「道路メンテナンス事業」(事業主体別、百万円)がある。

依頼文にあった https://www.mlit.go.jp/sogoseisaku/region/ 配下には交付対象事業の一覧は見当たらなかった
(同ディレクトリは「地域づくり」のページ)。上記の報道発表を正とする。
報道発表ページの ID は連番でないため、下の RELEASES に確認済みの URL を列挙している
(新年度分は `discover_releases()` で kanbo05_hh_XXXXXX を走査して追加できる)。

限界:
  - 当初予算の配分のみ。補正予算分(別の報道発表)は未取得。
  - 共同計画(複数自治体が策定主体)の配分額は自治体別内訳が公表されておらず、按分しない。
  - 「計画策定主体」が栃木県のみの計画は県事業として扱い、市町には帰属させない。
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..municipalities import TOCHIGI
from ..parse import mlit_kasho as P

SOURCE = "mlit_grants"
PREF_LINK_TEXT = "栃木"

# fiscal_year -> (報道発表ページ, 発表日(ページ本文で確認できたもののみ。未確認は None), 事業実施箇所ページ)
RELEASES: dict[int, tuple[str, str | None, str]] = {
    2022: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000226.html", None, "https://www.mlit.go.jp/page/kanbo05_hy_002418.html"),
    2023: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000251.html", None, "https://www.mlit.go.jp/page/kanbo05_hy_003091.html"),
    2024: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000265.html", None, "https://www.mlit.go.jp/page/kanbo05_hy_003249.html"),
    2025: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000289.html", "2025-04-01", "https://www.mlit.go.jp/page/kanbo05_hy_003321.html"),
    2026: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000304.html", "2026-04-07", "https://www.mlit.go.jp/page/kanbo05_hy_003397.html"),
}

PROGRAMS = [
    ("mlit_shasoukou", "社会資本整備総合交付金", "国土交通省"),
    ("mlit_bouan", "防災・安全交付金", "国土交通省"),
    ("mlit_road_maintenance", "道路メンテナンス事業補助(箇所表)", "国土交通省 道路局"),
]


def discover_releases(start: int, stop: int) -> list[tuple[int, str, str]]:
    """kanbo05_hh_{start..stop} を 1 秒間隔で走査し、「関係予算の配分について」の報道発表を探す(補助用)。"""
    out = []
    for n in range(start, stop + 1):
        url = f"https://www.mlit.go.jp/report/press/kanbo05_hh_{n:06d}.html"
        try:
            html = http.get_html(url)
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m and "関係予算の配分について" in m.group(1) and "補正" not in m.group(1):
            hy = re.search(r'href="(/page/kanbo05_hy_\d+\.html)"[^>]*>\s*(?:<strong>)?事業実施箇所', html)
            out.append((n, m.group(1).strip(), urljoin(url, hy.group(1)) if hy else ""))
    return out


def list_sources(fiscal_years: list[int]) -> list[dict]:
    out = []
    for fy in fiscal_years:
        if fy not in RELEASES:
            out.append({"fiscal_year": fy, "url": None, "error": "RELEASES に未登録"})
            continue
        press, date, kasho = RELEASES[fy]
        soup = BeautifulSoup(http.get_html(kasho), "html.parser")
        link = next((a["href"] for a in soup.find_all("a") if a.get_text(strip=True) == PREF_LINK_TEXT), None)
        if not link:
            out.append({"fiscal_year": fy, "url": None, "error": f"{PREF_LINK_TEXT} のリンクなし: {kasho}"})
            continue
        out.append({"fiscal_year": fy, "url": urljoin(kasho, link), "press": press, "date": date,
                    "filename": f"kasho_{fy}_09.pdf"})
    return out


def local_files():
    return sorted(http.raw_dir(SOURCE).glob("kasho_*_09.pdf"))


def parse(paths) -> list[dict]:
    out = []
    for p in paths:
        fy = int(re.search(r"kasho_(\d{4})_", p.name).group(1))
        meta = http.manifest_meta(p) or {"url": None, "retrieved_at": None}
        d = P.parse_pdf(p)
        d.update({"path": p, "fiscal_year": fy, "meta": meta,
                  "decision_date": RELEASES.get(fy, (None, None, None))[1]})
        out.append(d)
    return out


def load(conn: sqlite3.Connection, parsed: list[dict]) -> int:
    for pid, name, ministry in PROGRAMS:
        conn.execute(
            """INSERT OR IGNORE INTO subsidy_programs(program_id, name, ministry, results_public, granularity, note, source_url)
               VALUES (?,?,?,?,?,?,?)""",
            (pid, name, ministry, "当初配分を都道府県別 PDF で公表", "計画 × 策定主体 × 年度",
             "共同計画は自治体別内訳なし", "https://www.mlit.go.jp/report/press/"),
        )
    n = 0
    for d in parsed:
        url, ra = d["meta"]["url"], d["meta"]["retrieved_at"]
        conn.execute("DELETE FROM subsidy_allocations WHERE fiscal_year=? AND program_id LIKE 'mlit_%'", (d["fiscal_year"],))
        for g in d["grants"]:
            conn.execute(
                """INSERT INTO subsidy_allocations(program_id, municipality_code, fiscal_year, item_name, recipients,
                   recipient_codes, attribution, amount_thousand_yen, unit_original, decision_date, raw_ref, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (P.PROGRAM_IDS[g.program], g.municipality_code, d["fiscal_year"], g.plan_name, ",".join(g.recipients),
                 ",".join(g.recipient_codes), g.attribution, g.amount_thousand_yen, "千円", d["decision_date"],
                 f"p.{g.page}", url, ra),
            )
            n += 1
        for r in d["road_maintenance"]:
            amt = r.amount_million_yen * 1000 if r.amount_million_yen is not None else None
            conn.execute(
                """INSERT INTO subsidy_allocations(program_id, municipality_code, fiscal_year, item_name, recipients,
                   recipient_codes, attribution, amount_thousand_yen, unit_original, n_locations, decision_date, raw_ref,
                   source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("mlit_road_maintenance", r.municipality_code, d["fiscal_year"], r.item_name, r.entity,
                 r.municipality_code or "", "sole" if r.municipality_code else "prefecture", amt, "百万円", 1,
                 d["decision_date"], f"p.{r.page}", url, ra),
            )
            n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, fiscal_years: list[int] | None = None, offline: bool = False, force: bool = False) -> dict:
    fiscal_years = fiscal_years or sorted(RELEASES)
    if not offline:
        for s in list_sources(fiscal_years):
            if not s.get("url"):
                db.log_fetch(conn, SOURCE, "list", "error", f"FY{s['fiscal_year']}: {s['error']}")
                continue
            try:
                http.download(s["url"], SOURCE, s["filename"], force=force)
                db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", repr(e), s["url"])
    parsed = parse(local_files())
    for d in parsed:
        ok = d["grants"] and all(abs(v) < 0.5 for v in d["total_check"].values())
        db.log_fetch(conn, SOURCE, "parse", "ok" if ok else "error",
                     f"{d['path'].name}: grants={len(d['grants'])} road_maint={len(d['road_maintenance'])} "
                     f"totals={d['totals']} total_check(差)={d['total_check']}", d["meta"]["url"])
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n, "years": [d["fiscal_year"] for d in parsed]}
