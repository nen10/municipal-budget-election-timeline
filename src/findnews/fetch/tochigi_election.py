"""栃木県選挙管理委員会 2026 年 2 月 8 日執行 衆院選(小選挙区)の市町別得票の取得。

結果一覧: https://www.pref.tochigi.lg.jp/senkyo/r08syugi/kekka.html
  「開票結果『選挙区』候補者別開票区別得票数」の Excel: file/AS_KAIHYO_K_10_1.xls(確定値)
県選管サイトでは PDF の掲載は確認できなかった(HTML と Excel のみ)。依頼では PDF を想定していたが、
同内容の確定値 Excel を一次資料として用いる。

robots.txt(https://www.pref.tochigi.lg.jp/robots.txt)は /koujisoutatsu/ 等のみ拒否で、/senkyo/ は対象外。

限界:
  - 比例復活の有無は県選管の資料には載らない(北関東ブロックの選挙会結果)。election_results.pr_revived は
    NULL のまま残し、手作業データ(data/manual/election_outcomes.csv)で出典つきで補う。
  - 宇都宮市は開票区が「宇都宮市第１」「宇都宮市第２」に分かれ、1 区と 2 区にまたがる。
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..municipalities import lookup, normalize_name
from ..parse import tochigi_election as P

SOURCE = "tochigi_election"
INDEX_URL = "https://www.pref.tochigi.lg.jp/senkyo/r08syugi/kekka.html"
ELECTION_DATE = "2026-02-08"
FILENAME = "shugiin_2026_smd_kaihyo.xls"


def election_id(district: int) -> str:
    return f"shugiin_2026_smd_09_{district}"


def list_sources() -> list[dict]:
    """kekka.html から『候補者別開票区別得票数』の Excel リンクを探す(見つからなければ既知 URL)。"""
    html = http.get_html(INDEX_URL)
    soup = BeautifulSoup(html, "html.parser")
    url = None
    for a in soup.find_all("a"):
        if a.get("href", "").endswith("AS_KAIHYO_K_10_1.xls"):
            url = urljoin(INDEX_URL, a["href"])
            break
    if url is None:
        raise RuntimeError("kekka.html に AS_KAIHYO_K_10_1.xls へのリンクが見つからない")
    return [{"url": url, "filename": FILENAME}]


def local_files():
    p = http.raw_dir(SOURCE) / FILENAME
    return [p] if p.exists() else []


def parse(paths):
    out = []
    for p in paths:
        rows, summaries, meta = P.parse_grid(P.read_xls(p))
        out.append({"path": p, "rows": rows, "summaries": summaries, "meta": meta,
                    "file_meta": http.manifest_meta(p) or {"url": None, "retrieved_at": None}})
    return out


def _politician_index(conn) -> dict[str, str]:
    idx = {}
    for r in conn.execute("SELECT politician_id, name, name_variants FROM politicians"):
        for v in [r["name"]] + (r["name_variants"] or "").split("|"):
            if v.strip():
                idx[normalize_name(v)] = r["politician_id"]
    return idx


def load(conn: sqlite3.Connection, parsed) -> int:
    db.ensure_municipalities(conn)
    pol = _politician_index(conn)
    n = 0
    for d in parsed:
        url, ra = d["file_meta"]["url"], d["file_meta"]["retrieved_at"]
        summ = {s.district: s for s in d["summaries"]}
        for dist, s in summ.items():
            conn.execute(
                """INSERT OR REPLACE INTO elections(election_id, kind, election_date, district, pref_code, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (election_id(dist), "shugiin_smd", ELECTION_DATE, f"栃木県第{dist}区", "09", url, ra),
            )
        for r in d["rows"]:
            s = summ.get(r.district)
            conn.execute(
                """INSERT OR REPLACE INTO election_results(election_id, municipality_code, counting_unit, candidate_name,
                   politician_id, party, votes, is_district_winner, pr_revived, sekihai_rate, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (election_id(r.district), lookup(r.counting_unit), r.counting_unit, r.candidate,
                 pol.get(normalize_name(r.candidate)), r.party, r.votes,
                 int(s is not None and s.winner == r.candidate), s.sekihai.get(r.candidate) if s else None, url, ra),
            )
            n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, offline: bool = False, force: bool = False) -> dict:
    if not offline:
        try:
            for s in list_sources():
                http.download(s["url"], SOURCE, s["filename"], force=force)
                db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, SOURCE, "download", "error", repr(e), INDEX_URL)
    parsed = parse(local_files())
    for d in parsed:
        db.log_fetch(conn, SOURCE, "parse", "ok" if d["rows"] else "error",
                     f"{d['meta']['status']}: districts={len(d['summaries'])} rows={len(d['rows'])}",
                     d["file_meta"]["url"])
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n}
