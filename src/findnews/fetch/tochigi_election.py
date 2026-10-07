"""栃木県選挙管理委員会 衆院選(小選挙区)の開票区別得票の取得。

対応する選挙(ELECTIONS): 2026-02-08(r08syugi)、2024-10-27(r06syugi)、2021-10-31(r03syugi)。
投票日は各 Excel の表題「令和N年M月D日執行」から読み取って elections に登録する(推測しない)。
選挙 ID: shugiin_<YYYYMMDD>_smd_09_<区>。選挙単位のまとめ ID は shugiin_<YYYYMMDD>。

結果一覧の例: https://www.pref.tochigi.lg.jp/senkyo/r08syugi/kekka.html
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
from ..parse import tochigi_kouho as K

SOURCE = "tochigi_election"
BASE = "https://www.pref.tochigi.lg.jp/senkyo/{key}/kekka.html"
ELECTIONS = ["r08syugi", "r06syugi", "r03syugi"]
INDEX_URL = BASE.format(key="r08syugi")


def filename(key: str) -> str:
    return f"shugiin_{key}_smd_kaihyo.xls"


def election_group_id(date_iso: str) -> str:
    return "shugiin_" + date_iso.replace("-", "")


def election_id(date_iso: str, district: int, pref: str = "09") -> str:
    return f"{election_group_id(date_iso)}_smd_{pref}_{district}"


def parse_date(title: str) -> str | None:
    import re
    from ..parse.common import norm
    m = re.search(r"令和(\d+)年(\d+)月(\d+)日執行", norm(title or ""))
    if not m:
        return None
    return f"{2018 + int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def list_sources(keys=None) -> list[dict]:
    """kekka.html から『候補者別開票区別得票数』(確定)の Excel(AS_KAIHYO_K_10_1.xls)リンクを、
    kouho/index.html から選挙区ごとの「候補者届出状況公表票」PDF(rikkouhoN.pdf / kouhosyaN.pdf)を探す。"""
    import re
    out = []
    for key in keys or ELECTIONS:
        index = BASE.format(key=key)
        soup = BeautifulSoup(http.get_html(index), "html.parser")
        url = next((urljoin(index, a["href"]) for a in soup.find_all("a")
                    if a.get("href", "").endswith("AS_KAIHYO_K_10_1.xls")), None)
        out.append({"key": key, "url": url, "filename": filename(key), "index": index,
                    "error": None if url else "AS_KAIHYO_K_10_1.xls へのリンクなし"})
        kidx = urljoin(index, "kouho/index.html")
        ks = BeautifulSoup(http.get_html(kidx), "html.parser")
        for a in ks.find_all("a"):
            m = re.search(r"(rikkouho|kouhosya)(\d)\.pdf$", a.get("href", ""))
            if m:
                out.append({"key": key, "url": urljoin(kidx, a["href"]), "filename": f"kouho_{key}_{m.group(2)}.pdf",
                            "index": kidx, "error": None})
    return out


def local_files():
    return sorted(http.raw_dir(SOURCE).glob("shugiin_*_smd_kaihyo.xls"))


def kouho_files(key: str):
    return sorted(http.raw_dir(SOURCE).glob(f"kouho_{key}_*.pdf"))


def parse(paths, kouho_paths: dict | None = None):
    """kouho_paths: {結果ファイルのパス: [候補者届出状況公表票 PDF]}。省略時は同じ選挙キーのファイルを探す。"""
    import re
    out = []
    for p in paths:
        rows, summaries, meta = P.parse_grid(P.read_xls(p))
        m = re.match(r"shugiin_(.+?)_smd_kaihyo", p.name)
        kps = (kouho_paths or {}).get(p) if kouho_paths is not None else (kouho_files(m.group(1)) if m else [])
        cands = {}
        for kp in kps or []:
            km = http.manifest_meta(kp) or {"url": None}
            for c in K.parse_pdf(kp):
                cands[(c.district, normalize_name(c.name))] = (c, km["url"])
        out.append({"path": p, "rows": rows, "summaries": summaries, "meta": meta, "candidates": cands,
                    "file_meta": http.manifest_meta(p) or {"url": None, "retrieved_at": None}})
    return out


def finalize_results(conn: sqlite3.Connection) -> None:
    """得票率・自治体内順位・比例復活の既定値・結果ラベルを計算する(手作業データの読み込み後にも再実行)。"""
    rows = conn.execute("SELECT rowid, election_id, municipality_code, counting_unit, votes FROM election_results").fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["election_id"], r["municipality_code"] or r["counting_unit"]), []).append(r)
    # 同じ選挙区内で同じ自治体の開票区が複数ある場合は合算した順位・得票率を各行に付ける
    for key, rs in groups.items():
        by_cand = {}
        for r in conn.execute(
                f"""SELECT candidate_name, SUM(votes) v FROM election_results WHERE election_id=? AND
                    {'municipality_code=?' if rs[0]['municipality_code'] else 'counting_unit=?'} GROUP BY candidate_name""",
                (key[0], key[1])):
            by_cand[r["candidate_name"]] = r["v"] or 0
        total = sum(by_cand.values())
        order = sorted(by_cand, key=lambda c: -by_cand[c])
        for c in by_cand:
            conn.execute(
                f"""UPDATE election_results SET vote_share=?, rank_in_municipality=? WHERE election_id=? AND candidate_name=?
                    AND {'municipality_code=?' if rs[0]['municipality_code'] else 'counting_unit=?'}""",
                (by_cand[c] / total if total else None, order.index(c) + 1, key[0], c, key[1]))
    # 重複立候補なしの落選者は比例復活なし(届出状況公表票による)
    conn.execute("""UPDATE election_results SET pr_revived=0, pr_revived_source_url=source_url WHERE is_district_winner=0 AND dual_candidacy=0
                    AND pr_revived IS NULL""")
    conn.execute("""UPDATE election_results SET result_label = CASE
                      WHEN is_district_winner=1 THEN '選挙区当選'
                      WHEN pr_revived=1 THEN '落選・比例復活'
                      WHEN pr_revived=0 THEN '落選'
                      ELSE '落選(比例復活未確認)' END""")
    conn.commit()


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
        date = parse_date(d["meta"]["title"])
        if date is None:
            raise ValueError(f"投票日を表題から読めない: {d['path']}")
        summ = {s.district: s for s in d["summaries"]}
        for dist, s in summ.items():
            conn.execute(
                """INSERT OR REPLACE INTO elections(election_id, kind, election_date, district, pref_code, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (election_id(date, dist), "shugiin_smd", date, f"栃木県第{dist}区", "09", url, ra),
            )
        for r in d["rows"]:
            s = summ.get(r.district)
            c, kurl = d["candidates"].get((r.district, normalize_name(r.candidate)), (None, None))
            party = (c.party if c else None) or (r.party.strip("()（）") if r.party else None)
            pid = pol.get(normalize_name(r.candidate)) or (pol.get(normalize_name(c.legal_name)) if c and c.legal_name else None)
            conn.execute(
                """INSERT OR REPLACE INTO election_results(election_id, municipality_code, counting_unit, candidate_name,
                   candidate_legal_name, politician_id, party, filing_type, nomination, incumbency, dual_candidacy,
                   votes, is_district_winner, pr_revived, sekihai_rate, source_url, retrieved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)""",
                (election_id(date, r.district), lookup(r.counting_unit), r.counting_unit, r.candidate,
                 c.legal_name if c else None, pid, party, c.filing_type if c else None,
                 K.nomination_label(c) if c else None, c.incumbency if c else None,
                 None if not c or c.dual is None else int(c.dual), r.votes,
                 int(s is not None and s.winner == r.candidate), s.sekihai.get(r.candidate) if s else None,
                 " ; ".join(x for x in (url, kurl) if x) or None, ra),
            )
            n += 1
    conn.commit()
    finalize_results(conn)
    return n


def run(conn: sqlite3.Connection, offline: bool = False, force: bool = False, keys=None) -> dict:
    if not offline:
        for s in list_sources(keys):
            if not s["url"]:
                db.log_fetch(conn, SOURCE, "list", "error", f"{s['key']}: {s['error']}", s["index"])
                continue
            try:
                http.download(s["url"], SOURCE, s["filename"], force=force)
                db.log_fetch(conn, SOURCE, "download", "ok", s["filename"], s["url"])
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", repr(e), s["url"])
    parsed = parse(local_files())
    for d in parsed:
        matched = sum(1 for r in d["rows"] if (r.district, normalize_name(r.candidate)) in d["candidates"])
        db.log_fetch(conn, SOURCE, "parse", "ok" if d["rows"] and matched == len(d["rows"]) else "error",
                     f"{d['path'].name}: 候補者属性の突合 {matched}/{len(d['rows'])} 行; {d['meta']['title']} {d['meta']['status']}: districts={len(d['summaries'])} rows={len(d['rows'])}",
                     d["file_meta"]["url"])
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "load", "ok", f"{n} rows")
    return {"files": len(parsed), "rows": n}
