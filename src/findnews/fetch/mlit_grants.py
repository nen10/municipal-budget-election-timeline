"""国土交通省 社会資本整備総合交付金・防災・安全交付金(当初予算の配分)の取得。

同じ PDF の道路局箇所表は fetch/mlit_road.py が読む(生データは共通で data/raw/mlit_grants/)。

出典の構造(2026-10-08 確認):
  報道発表「令和N年度国土交通省関係予算の配分について」(4 月上旬)
    → 「事業実施箇所」ページ(都道府県リンク一覧)
    → 都道府県別 PDF(例: 令和8年度 栃木 https://www.mlit.go.jp/page/content/001994561.pdf)
  PDF 末尾に「令和N年度当初予算 社会資本整備総合交付金の配分」表(計画名・計画策定主体・配分国費)がある。

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
from ..parse import mlit_kasho as P
from ..prefs import short as pref_short

SOURCE = "mlit_grants"

# fiscal_year -> (報道発表ページ, 発表日(報道発表ページ本文で確認), 事業実施箇所ページ)
RELEASES: dict[int, tuple[str, str | None, str]] = {
    2021: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000210.html", "2021-03-30", "https://www.mlit.go.jp/page/kanbo05_hy_002213.html"),
    2022: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000226.html", "2022-03-25", "https://www.mlit.go.jp/page/kanbo05_hy_002418.html"),
    2023: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000251.html", "2023-03-31", "https://www.mlit.go.jp/page/kanbo05_hy_003091.html"),
    2024: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000265.html", "2024-04-01", "https://www.mlit.go.jp/page/kanbo05_hy_003249.html"),
    2025: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000289.html", "2025-04-01", "https://www.mlit.go.jp/page/kanbo05_hy_003321.html"),
    2026: ("https://www.mlit.go.jp/report/press/kanbo05_hh_000304.html", "2026-04-07", "https://www.mlit.go.jp/page/kanbo05_hy_003397.html"),
}

PROGRAMS = [
    ("mlit_shasoukou", "社会資本整備総合交付金", "国土交通省"),
    ("mlit_bouan", "防災・安全交付金", "国土交通省"),
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


def list_sources(fiscal_years: list[int], pref_code: str = "09") -> list[dict]:
    link_text = pref_short(pref_code)
    out = []
    for fy in fiscal_years:
        if fy not in RELEASES:
            out.append({"fiscal_year": fy, "url": None, "error": "RELEASES に未登録"})
            continue
        press, date, kasho = RELEASES[fy]
        soup = BeautifulSoup(http.get_html(kasho), "html.parser")
        link = next((a["href"] for a in soup.find_all("a") if a.get_text(strip=True) == link_text), None)
        if not link:
            out.append({"fiscal_year": fy, "url": None, "error": f"{link_text} のリンクなし: {kasho}"})
            continue
        out.append({"fiscal_year": fy, "url": urljoin(kasho, link), "press": press, "date": date,
                    "filename": f"kasho_{fy}_{pref_code}.pdf"})
    return out


def local_files(pref_code: str = "09"):
    return sorted(http.raw_dir(SOURCE).glob(f"kasho_*_{pref_code}.pdf"))


def parse(paths, pref_code: str = "09") -> list[dict]:
    out = []
    for p in paths:
        fy = int(re.search(r"kasho_(\d{4})_", p.name).group(1))
        meta = http.manifest_meta(p) or {"url": None, "retrieved_at": None}
        d = P.parse_pdf(p, pref_code)
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
        conn.execute("""DELETE FROM subsidy_allocations WHERE fiscal_year=? AND program_id IN ('mlit_shasoukou','mlit_bouan')
                        AND source_url IS ?""", (d["fiscal_year"], url))
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
    conn.commit()
    return n


def run_download(conn: sqlite3.Connection, fiscal_years: list[int] | None = None, force: bool = False,
                 pref_code: str = "09") -> None:
    fiscal_years = fiscal_years or sorted(RELEASES)
    for s in list_sources(fiscal_years, pref_code):
        if not s.get("url"):
            db.log_fetch(conn, SOURCE, "list", "error", f"FY{s['fiscal_year']}: {s['error']}")
            continue
        try:
            http.download(s["url"], SOURCE, s["filename"], force=force)
            db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, SOURCE, "download", "error", repr(e), s["url"])


def run(conn: sqlite3.Connection, fiscal_years: list[int] | None = None, offline: bool = False, force: bool = False,
        pref_code: str = "09") -> dict:
    if not offline:
        run_download(conn, fiscal_years, force, pref_code)
    parsed = parse(local_files(pref_code), pref_code)
    for d in parsed:
        ok = d["grants"] and all(abs(v) < 0.5 for v in d["total_check"].values())
        db.log_fetch(conn, SOURCE, "parse", "ok" if ok else "error",
                     f"{d['path'].name}: grants={len(d['grants'])} "
                     f"totals={d['totals']} total_check(差)={d['total_check']}", d["meta"]["url"])
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n, "years": [d["fiscal_year"] for d in parsed]}
