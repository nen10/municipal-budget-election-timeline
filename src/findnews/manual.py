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
    "election_outcomes.csv": ["politician_id", "election_id", "district_result", "pr_revived",
                              "pr_revived_source_url", "district_result_source_url"],
    "endorsements.csv": ["municipality_code", "candidate_name", "stance", "source_url"],
    "subsidy_programs.csv": ["program_id", "name", "ministry", "discretion_level", "source_url"],
    "requests.csv": ["municipality_code", "fiscal_year", "project", "result", "source_url"],
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
    rows = read_csv(d / "election_outcomes.csv")
    applied = 0
    for r in rows:
        if r.get("pr_revived") is not None and r.get("pr_revived_source_url"):
            conn.execute("UPDATE election_results SET pr_revived=? WHERE election_id=? AND politician_id=?",
                         (int(r["pr_revived"]), r["election_id"], r["politician_id"]))
            applied += 1
    counts["election_outcomes"] = len(rows)
    counts["election_outcomes_applied_to_results"] = applied

    conn.execute("DELETE FROM endorsements")
    rows = read_csv(d / "endorsements.csv")
    for r in rows:
        conn.execute(
            """INSERT INTO endorsements(endorser_name, endorser_role, municipality_code, candidate_name, politician_id,
               election_id, stance, evidence_date, source_title, note, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.get("endorser_name"), r.get("endorser_role"), r.get("municipality_code"), r.get("candidate_name"),
             r.get("politician_id"), r.get("election_id"), r.get("stance"), r.get("evidence_date"),
             r.get("source_title"), r.get("note"), r.get("source_url"), r.get("retrieved_at")))
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

    conn.execute("DELETE FROM requests")
    rows = read_csv(d / "requests.csv")
    for r in rows:
        conn.execute(
            """INSERT INTO requests(municipality_code, fiscal_year, project, result, note, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?)""",
            (r.get("municipality_code"), r.get("fiscal_year"), r.get("project"), r.get("result"), r.get("note"),
             r.get("source_url"), r.get("retrieved_at")))
    counts["requests"] = len(rows)

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
    from .municipalities import normalize_name
    idx = {}
    for r in conn.execute("SELECT politician_id, name, name_variants FROM politicians"):
        for v in [r["name"]] + (r["name_variants"] or "").split("|"):
            if v.strip():
                idx[normalize_name(v)] = r["politician_id"]
    for table, col in (("election_results", "candidate_name"), ("statements", "speaker")):
        for r in conn.execute(f"SELECT rowid, {col} FROM {table}").fetchall():
            pid = idx.get(normalize_name(r[1] or ""))
            if pid:
                conn.execute(f"UPDATE {table} SET politician_id=? WHERE rowid=?", (pid, r[0]))
    conn.commit()
    return counts
