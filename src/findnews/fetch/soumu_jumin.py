"""総務省 住民基本台帳に基づく人口(市区町村別、総計)の取得。検証出力の「参考人口」列に使う。

ページ: https://www.soumu.go.jp/main_sosiki/jichi_gyousei/daityo/jinkou_jinkoudoutai-setaisuu.html
  「【総計】令和N年住民基本台帳人口・世帯数、令和N-1年人口動態(市区町村別)」の xlsx。
同ページに載るのは最新年(2026-10-08 時点で令和8年1月1日現在)だけ。

年度との対応: 決算カードと同じく「調査対象年度の 1 月 1 日現在」とする(令和8年1月1日現在 = 2025 年度)。
2021〜2024 年度の人口は決算カードの値を使い、2026 年度(2027-01-01 現在)はまだ存在しないため未取得。
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..municipalities import kind, load_masters
from ..parse import soumu_jumin as P

SOURCE = "soumu_jumin"
INDEX_URL = "https://www.soumu.go.jp/main_sosiki/jichi_gyousei/daityo/jinkou_jinkoudoutai-setaisuu.html"


def list_sources() -> list[dict]:
    soup = BeautifulSoup(http.get_html(INDEX_URL), "html.parser")
    for a in soup.find_all("a"):
        t = a.get_text(strip=True)
        if t.startswith("【総計】") and "住民基本台帳人口・世帯数" in t and "市区町村別" in t:
            return [{"url": urljoin(INDEX_URL, a["href"]), "title": t}]
    raise RuntimeError("市区町村別(総計)の xlsx リンクが見つからない")


def local_files():
    return sorted(http.raw_dir(SOURCE).glob("jumin_*_shikuchoson.xlsx"))


def load_master(conn: sqlite3.Connection, path) -> int:
    """全国の市区町村を municipalities に投入する(他県展開用のマスタ)。"""
    meta = http.manifest_meta(path) or {"url": None, "retrieved_at": None}
    n = 0
    for code, (pref, name) in P.parse_names(path).items():
        conn.execute(
            """INSERT INTO municipalities(code, name, pref_code, pref_name, kind, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name, pref_name=excluded.pref_name""",
            (code, name, code[:2], pref, kind(name), meta["url"], meta["retrieved_at"]))
        n += 1
    conn.commit()
    load_masters(conn)
    return n


def run(conn: sqlite3.Connection, offline: bool = False, force: bool = False, pref_code: str | None = None) -> dict:
    db.ensure_municipalities(conn)
    if not offline:
        try:
            for s in list_sources():
                tmp = http.download(s["url"], SOURCE, "jumin_latest_shikuchoson.xlsx", force=True)
                as_of, _ = P.parse_workbook(tmp.path)
                name = f"jumin_{as_of[:4]}_shikuchoson.xlsx"
                target = tmp.path.with_name(name)
                if not target.exists() or force:
                    http.download(s["url"], SOURCE, name, force=True)
                tmp.path.unlink(missing_ok=True)
                db.log_fetch(conn, SOURCE, "download", "ok", f"{name} ({s['title']})", s["url"])
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, SOURCE, "download", "error", repr(e), INDEX_URL)
    n = 0
    n_master = 0
    for p in local_files():
        n_master = load_master(conn, p)
        as_of, pops = P.parse_workbook(p)
        meta = http.manifest_meta(p) or {"url": None, "retrieved_at": None}
        fy = int(as_of[:4]) - 1  # 1 月 1 日現在 → その日を含む年度
        for code in pops:
            if (pref_code is None or code.startswith(pref_code)) and not code.endswith("0000") and \
                    conn.execute("SELECT 1 FROM municipalities WHERE code=?", (code,)).fetchone():
                conn.execute(
                    """INSERT OR REPLACE INTO municipality_fiscal(code, fiscal_year, item, value, unit, source, source_url, retrieved_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (code, fy, "住民基本台帳人口", pops[code], "人", SOURCE, meta["url"], meta["retrieved_at"]))
                n += 1
        db.log_fetch(conn, SOURCE, "parse", "ok" if n else "error", f"{p.name}: as_of={as_of} FY{fy} rows={n}", meta["url"])
    conn.commit()
    return {"rows": n, "municipalities_master": n_master}
