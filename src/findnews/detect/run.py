"""`findnews detect run`: パネル構築 → 自治体自身の時系列における減少 → 発言一致 → スコア → CSV/Markdown/DB。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .. import config, verify
from ..http import now_iso
from ..matrix import windows
from ..municipalities import TOCHIGI
from . import report
from .panel import politician_panel
from .score import compute_signals
from .statements import load_statements


def latest_group(conn) -> str | None:
    r = conn.execute("SELECT election_id FROM elections ORDER BY election_date DESC LIMIT 1").fetchone()
    return r[0].split("_smd_")[0] if r else None


def run(conn: sqlite3.Connection, pref_code: str = "09", out_dir: Path | None = None, group: str | None = None) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir or config.PROCESSED_DIR / "reports" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    group = group or latest_group(conn)
    pnl = politician_panel(conn, group) if group else pd.DataFrame()
    edate = pnl.election_date.iloc[0] if len(pnl) else None
    w = windows(conn, edate) if edate else None
    th = float(verify.settings_mod.load()["verification"]["direction_threshold"])
    codes = sorted(TOCHIGI)
    prepost, dd = {}, {}
    if w:
        yrs = [y for y in w["pre_years"] + [w["post_year"], w["post_year_tokko"]] if y]
        obs = verify.build(conn, codes, (min(yrs), max(yrs)))
        for c in codes:
            if w["post_year_tokko"]:
                prepost[(c, "3b")] = verify.pre_post(obs[(c, "3b")], w["pre_years"], w["post_year_tokko"], th)
            for k in ("4", "5"):
                if w["post_year"]:
                    prepost[(c, k)] = verify.pre_post(obs[(c, k)], w["pre_years"], w["post_year"], th)
        from ..fetch.mlit_grants import RELEASES
        rel = RELEASES.get(w["post_year"], (None, None, None))[1]
        dd["4"] = dd["5"] = date.fromisoformat(rel) if rel else None
        r = conn.execute("""SELECT MAX(decision_date) FROM subsidy_allocations WHERE program_id='soumu_tokko'
                            AND item_name='3月交付額' AND fiscal_year=?""", (w["post_year_tokko"],)).fetchone()
        dd["3b"] = date.fromisoformat(r[0]) if r and r[0] else None
    st = load_statements(conn)
    positions = pd.read_sql_query("SELECT * FROM positions", conn)
    sig = compute_signals(prepost, dd, pnl, st, positions) if len(pnl) else pd.DataFrame()
    if len(sig):
        sig.insert(1, "name", sig.municipality_code.map(TOCHIGI))
        sig.sort_values(["score", "score_ungated"], ascending=False).to_csv(out_dir / "signals.csv", index=False,
                                                                             encoding="utf-8-sig")
        created = now_iso()
        for r in sig.itertuples(index=False):
            conn.execute(
                """INSERT INTO signals(run_id, municipality_code, fiscal_year, politician_id, score, contributions,
                   evidence, created_at, source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, r.municipality_code, w["post_year"], r.politician_id, float(r.score), r.contributions,
                 r.evidence, created, r.source_url, created))
        conn.commit()
    md = report.render(conn, run_id, pref_code, group, w, prepost, sig, st, th)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    return {"run_id": run_id, "out_dir": str(out_dir), "election": group, "n_signals": len(sig)}
