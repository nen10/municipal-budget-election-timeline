"""`findnews fetch --pref --years --source` の本体。

各アダプタについて: 文書一覧 → data/raw に保存 → 既存テーブルへの投入(load_legacy)→ Observation を observations に保存。
--offline では data/raw の既存ファイルだけをパースする(既存の取得データに 13.2 の日付を遡って付けるのにも使う)。
未対応の都道府県・ソースはエラーにせず「未対応」として結果に記録する。
"""

from __future__ import annotations

import sqlite3

from . import db, http, manual
from .municipalities import has_master, load_masters
from .sources import INDICATOR_SOURCES, election_source
from .sources.base import Observation

ORDER = ["soumu_jumin", "soumu_card", "soumu_tokko", "mlit_grants", "mlit_road", "election", "requests", "kokkai"]


def store_observations(conn: sqlite3.Connection, obs: list[Observation]) -> int:
    for o in obs:
        conn.execute(
            """INSERT OR REPLACE INTO observations(pref_code, municipality_code, indicator_id, period_start, period_end,
               decided_date, decided_date_is_proxy, decided_date_basis, published_date, value, unit, count, missing_reason,
               source_url, retrieved_at, generated_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (o.pref_code, o.municipality_code, o.indicator_id, o.period_start, o.period_end, o.decided_date,
             int(o.decided_date_is_proxy), o.decided_date_basis, o.published_date, o.value, o.unit, o.count,
             o.missing_reason, o.source_url, o.retrieved_at, o.generated_by))
    conn.commit()
    return len(obs)


def _in_years(path, years) -> bool:
    import re
    if not years:
        return True
    m = re.search(r"_(\d{4})_", path.name)
    return not m or int(m.group(1)) in years


def run_indicator(conn, source_id: str, pref_code: str, years: list[int], offline: bool, force: bool) -> dict:
    a = INDICATOR_SOURCES[source_id]
    if not has_master(pref_code):
        return {"status": "未対応", "reason": "市区町村マスタ未取得(--source soumu_jumin)"}
    if not offline:
        try:
            docs = a.list_documents(pref_code, years)
            db.log_fetch(conn, source_id, "list", "ok" if docs else "error", f"pref={pref_code} docs={len(docs)}")
            for d in docs:
                try:
                    http.download(d.url, "mlit_grants" if source_id.startswith("mlit") else source_id, d.filename, force=force)
                    db.log_fetch(conn, source_id, "download", "ok", d.filename, d.url)
                except Exception as e:  # noqa: BLE001
                    db.log_fetch(conn, source_id, "download", "error", repr(e), d.url)
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, source_id, "list", "error", f"pref={pref_code}: {e!r}")
            return {"status": "error", "reason": repr(e)}
    paths = [p for p in a.local_files(pref_code) if _in_years(p, years)]
    if not paths:
        return {"status": "未取得", "reason": "data/raw に該当ファイルなし"}
    legacy = a.load_legacy(conn, paths, pref_code)
    n = 0
    for p in paths:
        try:
            n += store_observations(conn, a.parse(p, pref_code))
        except Exception as e:  # noqa: BLE001
            db.log_fetch(conn, source_id, "parse", "error", f"{p.name}: {e!r}")
    db.log_fetch(conn, source_id, "observations", "ok", f"pref={pref_code} files={len(paths)} observations={n}")
    return {"status": "ok", "files": len(paths), "legacy_rows": legacy, "observations": n}


def run(conn: sqlite3.Connection, pref_code: str, years: list[int] | None = None, sources: list[str] | None = None,
        offline: bool = False, force: bool = False) -> dict:
    load_masters(conn)
    sources = sources or ORDER
    out = {}
    manual.load_all(conn)
    for sid in [s for s in ORDER if s in sources] + [s for s in sources if s not in ORDER]:
        if sid == "soumu_jumin":
            from .fetch import soumu_jumin
            out[sid] = soumu_jumin.run(conn, offline, force, pref_code)
        elif sid in INDICATOR_SOURCES:
            out[sid] = run_indicator(conn, sid, pref_code, years or [], offline, force)
        elif sid in ("election", f"election_{pref_code}"):
            es = election_source(pref_code)
            out[sid] = es.run(conn, offline, force) if es else {"status": "未対応",
                                                              "reason": f"都道府県 {pref_code} の選管アダプタが未実装"}
        elif sid == "requests":
            from . import config
            from . import requests as R
            got = {} if offline else R.download_sources(config.MANUAL_DIR, force)
            manual.load_all(conn)
            out[sid] = {"download": got, "generated": R.generate(conn, pref_code, config.MANUAL_DIR)}
        elif sid == "kokkai":
            from .fetch import kokkai
            out[sid] = kokkai.run(conn, offline=offline)
        else:
            out[sid] = {"status": "未対応", "reason": f"ソース {sid} はレジストリにない"}
    manual.load_all(conn)
    return out
