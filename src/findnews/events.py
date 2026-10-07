"""政局イベント(DESIGN.md 13.3)。

generate(): 取得済みデータから機械的に作れるイベントを登録する(generated_by に元のモジュール名)。
  - 衆院選投票(elections / election_results。選挙区ごと、当選者・比例復活者の氏名と政党つき)
  - 役職就任・内閣発足・改造(positions.csv の出典つきの行)
  - 予算配分公表(国交省の当初配分の報道発表日、特別交付税の交付決定日)
import_csv(): data/manual/events.csv(報道由来など)。出典 URL のない行は登録しない。
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .http import now_iso

COLUMNS = ["event_id", "date", "end_date", "date_precision", "event_type", "scope", "pref_code", "district",
           "municipality_code", "actor_name", "actor_party", "actor_role", "counterpart_name", "counterpart_role",
           "summary", "quote", "source_url", "outlet", "source_date", "note", "generated_by", "retrieved_at"]


def _put(conn, ev: dict) -> None:
    row = {c: ev.get(c) for c in COLUMNS}
    conn.execute(f"INSERT OR REPLACE INTO events({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                 [row[c] for c in COLUMNS])


def _reiwa(y: int) -> str:
    return "令和元" if y == 2019 else f"令和{y - 2018}"


def generate(conn: sqlite3.Connection, pref_code: str) -> dict:
    conn.execute("DELETE FROM events WHERE generated_by IS NOT NULL AND (pref_code=? OR scope='national')", (pref_code,))
    n = {"election": 0, "position": 0, "cabinet": 0, "mlit_release": 0, "tokko_decision": 0}
    ra = now_iso()
    # 選挙
    for e in conn.execute("SELECT * FROM elections WHERE pref_code=? ORDER BY election_date, election_id", (pref_code,)).fetchall():
        cands = conn.execute(
            """SELECT candidate_name, MAX(nomination) nom, MAX(result_label) res, SUM(votes) v FROM election_results
               WHERE election_id=? GROUP BY candidate_name ORDER BY v DESC""", (e["election_id"],)).fetchall()
        win = [c for c in cands if c["res"] == "選挙区当選"]
        rev = [c for c in cands if c["res"] == "落選・比例復活"]
        label = "参院選投票" if (e["kind"] or "").startswith("sangiin") else "衆院選投票"
        summary = (f"{e['district']} {label}。選挙区当選: " + "、".join(f"{c['candidate_name']}({c['nom']})" for c in win)
                   + ("。落選・比例復活: " + "、".join(f"{c['candidate_name']}({c['nom']})" for c in rev) if rev else "")
                   + "。候補者: " + "、".join(f"{c['candidate_name']}({c['nom']}、{int(c['v']):,}票)" for c in cands))
        _put(conn, {"event_id": f"gen-election-{e['election_id']}", "date": e["election_date"], "date_precision": "day",
                    "event_type": label, "scope": "district", "pref_code": pref_code, "district": e["district"],
                    "actor_name": "、".join(c["candidate_name"] for c in win), "actor_party": "、".join(c["nom"] or "" for c in win),
                    "actor_role": "選挙区当選", "summary": summary, "source_url": e["source_url"],
                    "outlet": "都道府県選挙管理委員会", "source_date": e["election_date"], "generated_by": "tochigi_election"
                    if pref_code == "09" else f"election_{pref_code}", "retrieved_at": e["retrieved_at"] or ra})
        n["election"] += 1
    # 役職(出典つきのもの)
    cabinets = {}
    for p in conn.execute("""SELECT p.*, po.name pname, po.party FROM positions p JOIN politicians po USING(politician_id)
                             WHERE p.source_url IS NOT NULL AND p.start_date IS NOT NULL""").fetchall():
        _put(conn, {"event_id": f"gen-position-{p['politician_id']}-{p['title']}-{p['start_date']}", "date": p["start_date"],
                    "date_precision": "day" if len(p["start_date"]) == 10 else "month", "event_type": "役職就任",
                    "scope": "national", "actor_name": p["pname"], "actor_party": p["party"], "actor_role": p["title"],
                    "summary": f"{p['pname']}({p['party']})が{p['organization'] or ''}の{p['title']}(所管 {p['ministry']})に就任",
                    "source_url": p["source_url"], "outlet": "首相官邸" if "kantei" in p["source_url"] else None,
                    "source_date": p["start_date"], "note": p["note"], "generated_by": "positions",
                    "retrieved_at": p["retrieved_at"] or ra})
        n["position"] += 1
        if p["organization"] and "内閣" in p["organization"]:
            cabinets[(p["organization"], p["start_date"])] = p["source_url"]
    for (org, d), url in cabinets.items():
        _put(conn, {"event_id": f"gen-cabinet-{d}", "date": d, "date_precision": "day", "event_type": "内閣発足・改造",
                    "scope": "national", "summary": f"{org} 発足(閣僚名簿の日付)", "source_url": url, "outlet": "首相官邸",
                    "source_date": d, "generated_by": "positions", "retrieved_at": ra})
        n["cabinet"] += 1
    # 国交省 当初配分の公表
    from .fetch.mlit_grants import RELEASES
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT CAST(substr(period_start,1,4) AS INTEGER) FROM observations WHERE indicator_id LIKE 'mlit_%' AND pref_code=?",
        (pref_code,))}
    for fy, (press, d, _) in RELEASES.items():
        if d and fy in have:
            _put(conn, {"event_id": f"gen-mlit-release-{fy}", "date": d, "date_precision": "day", "event_type": "予算配分公表",
                        "scope": "national", "actor_name": "国土交通省",
                        "summary": f"{_reiwa(fy)}年度 国土交通省関係予算の配分(当初)公表。社会資本整備総合交付金・防災・安全交付金・道路局箇所表を含む",
                        "source_url": press, "outlet": "国土交通省 報道発表", "source_date": d, "generated_by": "mlit_grants",
                        "retrieved_at": ra})
            n["mlit_release"] += 1
    # 特別交付税の交付決定
    for r in conn.execute("""SELECT DISTINCT indicator_id, substr(period_start,1,4) fy, decided_date, source_url FROM observations
                             WHERE indicator_id IN ('tokko_dec','tokko_march') AND pref_code=?""", (pref_code,)).fetchall():
        kind = "12月分" if r["indicator_id"] == "tokko_dec" else "3月分"
        _put(conn, {"event_id": f"gen-tokko-{r['fy']}-{r['indicator_id']}", "date": r["decided_date"], "date_precision": "day",
                    "event_type": "予算配分公表", "scope": "national", "actor_name": "総務省",
                    "summary": f"{_reiwa(int(r['fy']))}年度 特別交付税 {kind} 交付決定", "source_url": r["source_url"],
                    "outlet": "総務省 報道発表", "source_date": r["decided_date"], "generated_by": "soumu_tokko",
                    "retrieved_at": ra})
        n["tokko_decision"] += 1
    conn.commit()
    return n


def import_csv(conn: sqlite3.Connection, path: str | Path) -> dict:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in ("event_id", "date", "event_type", "scope", "source_url") if rows and c not in rows[0]]
    if missing:
        raise ValueError(f"{path}: 必須列がない {missing}")
    conn.execute("DELETE FROM events WHERE generated_by IS NULL")
    ok, skipped = 0, []
    for r in rows:
        r = {k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in r.items()}
        if not r.get("source_url"):
            skipped.append(r.get("event_id"))
            continue
        r["generated_by"] = None
        _put(conn, r)
        ok += 1
    conn.commit()
    return {"imported": ok, "skipped_without_source": skipped}
