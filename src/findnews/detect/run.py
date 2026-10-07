"""`findnews detect run` の本体: パネル構築 → ピア比偏差 → 発言一致 → スコア → CSV/Markdown/DB。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .. import config
from ..http import now_iso
from ..municipalities import TOCHIGI
from . import panel as panel_mod
from . import peers as peers_mod
from . import report
from .score import compute_signals
from .statements import load_statements


def focus_codes(conn: sqlite3.Connection) -> list[str]:
    out = []
    for r in conn.execute("SELECT municipalities FROM cases WHERE label='under_review'"):
        out += json.loads(r[0] or "[]")
    return sorted(set(out))


def run(conn: sqlite3.Connection, pref_code: str = "09", k: int = 5, out_dir: Path | None = None,
        focus: list[str] | None = None) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir or config.PROCESSED_DIR / "reports" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    series = panel_mod.metric_series(conn, pref_code)
    groups, feats = peers_mod.peer_groups(conn, pref_code, k=k)
    dev = peers_mod.peer_deviation(series, groups, feats) if not series.empty else pd.DataFrame()
    pnl = panel_mod.politician_panel(conn)
    st = load_statements(conn)
    positions = pd.read_sql_query("SELECT * FROM positions", conn)
    sig = compute_signals(dev, pnl, st, positions) if len(pnl) and len(dev) else pd.DataFrame()

    names = {c: n for c, n in TOCHIGI.items()}
    if len(dev):
        dev.insert(1, "name", dev.code.map(names))
        dev.to_csv(out_dir / "deviations.csv", index=False, encoding="utf-8-sig")
    if len(sig):
        sig.insert(1, "name", sig.municipality_code.map(names))
        sig.sort_values(["score", "score_ungated"], ascending=False).to_csv(
            out_dir / "signals.csv", index=False, encoding="utf-8-sig")
        created = now_iso()
        conn.execute("DELETE FROM signals WHERE run_id=?", (run_id,))
        for r in sig.itertuples(index=False):
            conn.execute(
                """INSERT INTO signals(run_id, municipality_code, fiscal_year, politician_id, score, contributions,
                   evidence, created_at, source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (run_id, r.municipality_code, None if pd.isna(r.fiscal_year) else int(r.fiscal_year), r.politician_id,
                 float(r.score), r.contributions, r.evidence, created, r.source_url, created))
        conn.commit()
    focus = focus or focus_codes(conn)
    md = report.render(conn, run_id, pref_code, k, series, dev, groups, feats, pnl, sig, st, focus)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    return {"run_id": run_id, "out_dir": str(out_dir), "n_series": len(series), "n_signals": len(sig),
            "focus": focus}
