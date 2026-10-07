"""手作業データ(data/manual/)の読み込みと DB 投入。

すべてのファイルは出典列(source_url など)を持つ。空欄は「未確認」を意味し、推定値で埋めない。
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import yaml

from . import config, db

REQUIRED = {
    "politicians.csv": ["politician_id", "name", "source_url"],
    "positions.csv": ["politician_id", "title", "ministry", "start_date", "verification", "source_url"],
    "election_outcomes.csv": ["election_id", "candidate_name", "pr_revived", "pr_revived_source_url"],
    "endorsements.csv": ["municipality_code", "election_id", "mayor_name", "mayor_affiliation", "candidate_name",
                         "candidate_party_nomination", "candidate_result", "endorsement_form", "source_url", "outlet",
                         "evidence_date", "quote", "collection_status"],
    "subsidy_programs.csv": ["program_id", "name", "ministry", "discretion_level", "source_url"],
    "statements.csv": ["speaker", "date", "venue", "quote", "outlet", "source_url"],
    "requests.csv": ["municipality_code", "record_type", "request_date", "recipient", "program", "project_name",
                     "result", "result_date", "source_url", "quote"],
}


def _blank(v):
    return None if v is None or str(v).strip() == "" else str(v).strip()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    req = REQUIRED.get(path.name, [])
    if rows or path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), [])
        missing = [c for c in req if c not in header]
        if missing:
            raise ValueError(f"{path.name}: 必須列がない {missing}")
    return [{k: _blank(v) for k, v in r.items()} for r in rows]


def load_all(conn: sqlite3.Connection, manual_dir: Path | None = None) -> dict:
    d = Path(manual_dir or config.MANUAL_DIR)
    db.ensure_municipalities(conn)
    counts = {}

    rows = read_csv(d / "politicians.csv")
    for r in rows:
        conn.execute(
            """INSERT OR REPLACE INTO politicians(politician_id, name, name_variants, party, note, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?)""",
            (r["politician_id"], r["name"], r.get("name_variants"), r.get("party"), r.get("note"),
             r.get("source_url"), r.get("retrieved_at")))
    counts["politicians"] = len(rows)

    conn.execute("DELETE FROM positions")
    rows = read_csv(d / "positions.csv")
    for r in rows:
        conn.execute(
            """INSERT INTO positions(politician_id, title, organization, ministry, start_date, end_date, verification,
               note, source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (r["politician_id"], r["title"], r.get("organization"), r.get("ministry"), r.get("start_date"),
             r.get("end_date"), r.get("verification"), r.get("note"), r.get("source_url"), r.get("retrieved_at")))
    counts["positions"] = len(rows)

    # 比例復活: 出典 URL があるときだけ election_results に反映する(未確認は NULL のまま)
    from .municipalities import normalize_name
    rows = read_csv(d / "election_outcomes.csv")
    applied = 0
    conn.execute("UPDATE election_results SET pr_revived=NULL, pr_revived_source_url=NULL")
    for r in rows:
        if r.get("pr_revived") is not None and r.get("pr_revived_source_url"):
            for er in conn.execute("SELECT rowid, candidate_name, candidate_legal_name FROM election_results WHERE election_id=?",
                                   (r["election_id"],)).fetchall():
                if normalize_name(r["candidate_name"]) in (normalize_name(er[1]), normalize_name(er[2] or "")):
                    conn.execute("UPDATE election_results SET pr_revived=?, pr_revived_source_url=? WHERE rowid=?",
                                 (int(r["pr_revived"]), r["pr_revived_source_url"], er[0]))
                    applied += 1
    counts["election_outcomes"] = len(rows)
    counts["election_outcomes_applied_to_results"] = applied

    conn.execute("DELETE FROM endorsements")
    rows = read_csv(d / "endorsements.csv")
    for r in rows:
        status = r.get("collection_status") or "未収集"
        if status != "未収集" and not (r.get("source_url") and r.get("quote")):
            status = "未収集"  # 出典 URL と引用文のない行は収集済として扱わない
        conn.execute(
            """INSERT INTO endorsements(municipality_code, election_id, endorser_role, mayor_name, mayor_affiliation,
               candidate_name, candidate_party_nomination, candidate_result, endorsement_form, source_url, outlet,
               evidence_date, quote, collection_status, politician_id, note, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("municipality_code"), r.get("election_id"), "首長", r.get("mayor_name"), r.get("mayor_affiliation"),
             r.get("candidate_name"), r.get("candidate_party_nomination"), r.get("candidate_result"),
             r.get("endorsement_form"), r.get("source_url"), r.get("outlet"), r.get("evidence_date"), r.get("quote"),
             status, r.get("politician_id"), r.get("note"), r.get("retrieved_at")))
    counts["endorsements"] = len(rows)

    rows = read_csv(d / "subsidy_programs.csv")
    for r in rows:
        conn.execute(
            """INSERT INTO subsidy_programs(program_id, name, ministry, discretion_level, results_public, granularity,
               note, source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(program_id) DO UPDATE SET name=excluded.name, ministry=excluded.ministry,
               discretion_level=excluded.discretion_level, results_public=excluded.results_public,
               granularity=excluded.granularity, note=excluded.note, source_url=excluded.source_url,
               retrieved_at=excluded.retrieved_at""",
            (r["program_id"], r["name"], r.get("ministry"), r.get("discretion_level"), r.get("results_public"),
             r.get("granularity"), r.get("note"), r.get("source_url"), r.get("retrieved_at")))
    counts["subsidy_programs"] = len(rows)

    from .requests import load_manual
    counts["requests"] = load_manual(conn, d)


    # 報道等で報じられた発言(原文の引用文。要約しない)
    import hashlib
    from .fetch.kokkai import match_keywords, target_municipalities
    conn.execute("DELETE FROM statements WHERE source='press'")
    rows = read_csv(d / "statements.csv") if (d / "statements.csv").exists() else []
    for r in rows:
        if not (r.get("source_url") and r.get("quote")):
            continue
        ext = hashlib.sha1(f"{r['source_url']}|{r['quote']}".encode()).hexdigest()[:16]
        conn.execute(
            """INSERT OR REPLACE INTO statements(source, external_id, speaker, speaker_group, speaker_position, politician_id,
               date, meeting, body, matched_keywords, target_municipalities, source_url, retrieved_at)
               VALUES ('press',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ext, r["speaker"], r.get("outlet"), r.get("published_date"), r.get("politician_id"), r.get("date"),
             r.get("venue"), r["quote"], "|".join(match_keywords(r["quote"])), ",".join(target_municipalities(r["quote"])),
             r["source_url"], r.get("retrieved_at")))
    counts["statements_press"] = len(rows)

    data = yaml.safe_load((d / "cases.yaml").read_text(encoding="utf-8")) or {}
    for c in data.get("cases", []):
        conn.execute(
            """INSERT OR REPLACE INTO cases(case_id, name, period, pattern, label, summary, politicians, municipalities,
               verification, source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (c["case_id"], c["name"], str(c.get("period", "")), c.get("pattern"), c.get("label"), c.get("summary"),
             json.dumps(c.get("politicians", []), ensure_ascii=False),
             json.dumps(c.get("municipalities", []), ensure_ascii=False),
             c.get("verification"), c.get("source_url") or None, None))
    counts["cases"] = len(data.get("cases", []))

    # 既存の選挙結果・発言と議員 ID を再リンク
    idx = {}
    for r in conn.execute("SELECT politician_id, name, name_variants FROM politicians"):
        for v in [r["name"]] + (r["name_variants"] or "").split("|"):
            if v.strip():
                idx[normalize_name(v)] = r["politician_id"]
    for r in conn.execute("SELECT rowid, candidate_name, candidate_legal_name FROM election_results").fetchall():
        pid = idx.get(normalize_name(r[1] or "")) or idx.get(normalize_name(r[2] or ""))
        if pid:
            conn.execute("UPDATE election_results SET politician_id=? WHERE rowid=?", (pid, r[0]))
    for r in conn.execute("SELECT rowid, speaker FROM statements").fetchall():
        pid = idx.get(normalize_name(r[1] or ""))
        if pid:
            conn.execute("UPDATE statements SET politician_id=? WHERE rowid=?", (pid, r[0]))
    conn.commit()
    from .fetch.tochigi_election import finalize_results
    finalize_results(conn)
    # 手作業のイベント(出典 URL のある行のみ)
    if (d / "events.csv").exists():
        from .events import import_csv
        counts["events"] = import_csv(conn, d / "events.csv")["imported"]
    return counts
