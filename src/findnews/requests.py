"""自治体の申請・要望の記録(DESIGN.md 第4節 requests、13.5、13.6)。

収集元(collection_source):
  mlit_jigo_hyoka    国交省「事後評価一覧」(全国)。計画名・計画策定主体・計画期間。申請日は不明(開始年度のみ)
  mlit_haibun        国交省の当初配分資料(全国、取得済みの PDF)。計画が当初配分に計上されたことを「結果」として記録
  municipal_plan_pdf 市町サイトに掲載された社会資本総合整備計画の計画書(全国共通様式)。計画書の日付・全体事業費
  municipal_page     市町サイトの「国・県への要望」等のページ(手作業で data/manual/requests.csv に登録)
道路メンテナンス事業補助などの個別補助の申請は非公開のため request_status に「未取得(非公開)」と記録する。
出典 URL と原文の引用(quote)のない記録は登録しない。
"""

from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from pathlib import Path

from . import http
from .municipalities import lookup, master, normalize_name
from .prefs import name as pref_name

JIGO_URLS = {"jigohyoka_shasoukou.pdf": "https://www.mlit.go.jp/page/content/001966355.pdf",
             "jigohyoka_bouan.pdf": "https://www.mlit.go.jp/page/content/001966356.pdf"}
JIGO_PAGE = "https://www.mlit.go.jp/page/kanbo05_hy_000213.html"
COLS = ["pref_code", "municipality_code", "record_type", "request_date", "date_precision", "fiscal_year", "recipient",
        "program", "project_name", "project_key", "planners", "attribution", "plan_period",
        "requested_amount_thousand_yen", "amount_note", "result", "result_date", "result_amount_thousand_yen",
        "indicator_link", "collection_source", "quote", "note", "source_url", "retrieved_at", "generated_by"]


def plan_key(name: str) -> str:
    s = normalize_name(name).replace("(", "（").replace(")", "）")
    return re.sub(r"[「」『』・･\"“”]", "", s)


def _put(conn, row: dict) -> None:
    conn.execute(f"INSERT INTO requests({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})", [row.get(c) for c in COLS])


def _status(conn, code, src, status, note="", urls="", source_url=None):
    conn.execute("""INSERT OR REPLACE INTO request_status(municipality_code, collection_source, status, note, checked_urls,
                    source_url, retrieved_at) VALUES (?,?,?,?,?,?,?)""", (code, src, status, note, urls, source_url, http.now_iso()))


def download_sources(manual_dir: Path, force: bool = False) -> dict:
    """事後評価一覧(全国)と、request_sources.csv に載った計画書 PDF を data/raw に保存する。"""
    got = {"jigo": 0, "plan_pdf": 0, "errors": []}
    for fn, url in JIGO_URLS.items():
        try:
            http.download(url, "mlit_plans", fn, force=force)
            got["jigo"] += 1
        except Exception as e:  # noqa: BLE001
            got["errors"].append(f"{url}: {e!r}")
    for r in _read(manual_dir / "request_sources.csv"):
        if r.get("kind") == "plan_pdf" and r.get("url"):
            try:
                http.download(r["url"], "municipal_plans", _plan_file(r), force=force)
                got["plan_pdf"] += 1
            except Exception as e:  # noqa: BLE001
                got["errors"].append(f"{r['url']}: {e!r}")
    return got


def _plan_file(r: dict) -> str:
    return f"{r['municipality_code']}_{hashlib.sha1(r['url'].encode()).hexdigest()[:10]}.pdf"


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [{k: (v.strip() if isinstance(v, str) and v.strip() else None) for k, v in r.items()} for r in csv.DictReader(f)]


def generate(conn: sqlite3.Connection, pref_code: str, manual_dir: Path) -> dict:
    """機械的に作れる申請・結果の記録を作り直す(手作業の行は残す)。"""
    from .fetch.mlit_grants import RELEASES
    from .parse import mlit_plans, plan_doc
    names = master(pref_code)
    conn.execute("DELETE FROM requests WHERE generated_by IS NOT NULL AND pref_code=?", (pref_code,))
    n = {"mlit_jigo_hyoka": 0, "mlit_haibun": 0, "municipal_plan_pdf": 0}
    # A. 事後評価一覧
    for fn, url in JIGO_URLS.items():
        p = http.raw_dir("mlit_plans") / fn
        if not p.exists():
            continue
        meta = http.manifest_meta(p) or {"retrieved_at": None}
        for r in mlit_plans.parse_pdf(p):
            if r.pref_name != pref_name(pref_code):
                continue
            codes = sorted({c for c in (lookup(x, pref_code) for x in r.planners) if c})
            sole = len(codes) == 1 and len(r.planners) == 1
            for c in codes:
                _put(conn, {"pref_code": pref_code, "municipality_code": c, "record_type": "申請(計画)",
                            "date_precision": "fiscal_year", "fiscal_year": r.start_fy, "recipient": "国土交通大臣",
                            "program": r.program, "project_name": r.plan_name, "project_key": plan_key(r.plan_name),
                            "planners": ",".join(r.planners), "attribution": "sole" if sole else "joint",
                            "plan_period": f"{r.start_fy}–{r.end_fy}年度", "result": "不明",
                            "indicator_link": "mlit_sole_grants" if sole else None, "collection_source": "mlit_jigo_hyoka",
                            "quote": f"{r.plan_name} {r.pref_name} {','.join(r.planners)} 開始{r.start_fy} 終了{r.end_fy}",
                            "note": "国交省「事後評価一覧」掲載の計画(計画期間終了・事後評価公表済み)。申請日は資料になく開始年度のみ"
                                    + (f"。事後評価の掲載先: {r.link}" if r.link else ""),
                            "source_url": url, "retrieved_at": meta["retrieved_at"], "generated_by": "requests"})
                n["mlit_jigo_hyoka"] += 1
    # B. 当初配分に計上(結果)
    for a in conn.execute("""SELECT program_id, fiscal_year, item_name, recipients, recipient_codes, attribution,
                                    amount_thousand_yen, decision_date, raw_ref, source_url, retrieved_at
                             FROM subsidy_allocations WHERE program_id IN ('mlit_shasoukou','mlit_bouan')
                             AND attribution IN ('sole','joint')""").fetchall():
        prog = "社会資本整備総合交付金" if a["program_id"] == "mlit_shasoukou" else "防災・安全交付金"
        for c in [x for x in (a["recipient_codes"] or "").split(",") if x in names]:
            _put(conn, {"pref_code": pref_code, "municipality_code": c, "record_type": "結果", "fiscal_year": a["fiscal_year"],
                        "recipient": "国土交通省", "program": prog, "project_name": a["item_name"],
                        "project_key": plan_key(a["item_name"]), "planners": a["recipients"], "attribution": a["attribution"],
                        "result": "採択", "result_date": a["decision_date"] or RELEASES.get(a["fiscal_year"], (None, None, None))[1],
                        "result_amount_thousand_yen": a["amount_thousand_yen"],
                        "indicator_link": "mlit_sole_grants" if a["attribution"] == "sole" else None,
                        "collection_source": "mlit_haibun",
                        "quote": f"{a['item_name']} | {a['recipients']} | {a['amount_thousand_yen']:,.0f}",
                        "note": "当初配分の配分資料に計上(配分国費は計画全体。共同計画は市町別内訳なし。要望額が公表されていないため"
                                "「一部採択」かは判別できない)。結果判明日は配分の報道発表日",
                        "source_url": a["source_url"], "retrieved_at": a["retrieved_at"], "generated_by": "requests"})
            n["mlit_haibun"] += 1
    # C. 市町サイトの計画書 PDF
    for r in _read(manual_dir / "request_sources.csv"):
        if r.get("kind") != "plan_pdf" or not r.get("url") or not (r.get("municipality_code") or "").startswith(pref_code):
            continue
        p = http.raw_dir("municipal_plans") / _plan_file(r)
        if not p.exists():
            continue
        d = plan_doc.parse_pdf(p)
        meta = http.manifest_meta(p) or {"retrieved_at": None}
        sole = d.grantees == [names.get(r["municipality_code"])]
        _put(conn, {"pref_code": pref_code, "municipality_code": r["municipality_code"], "record_type": "申請(計画)",
                    "request_date": d.doc_date, "date_precision": "day" if d.doc_date else None, "fiscal_year": d.start_fy,
                    "recipient": "国土交通大臣", "program": d.program, "project_name": d.plan_name,
                    "project_key": plan_key(d.plan_name or ""), "planners": ",".join(d.grantees),
                    "attribution": "sole" if sole else "joint", "plan_period": d.period,
                    "requested_amount_thousand_yen": d.total_cost_million_yen * 1000 if d.total_cost_million_yen else None,
                    "amount_note": "計画書の全体事業費(事業費の総額で、国費・要望額そのものではない)",
                    "result": "不明", "indicator_link": "mlit_sole_grants" if sole else None,
                    "collection_source": "municipal_plan_pdf",
                    "quote": f"{d.first_line} / 計画の名称 {d.plan_name} / 計画の期間 {d.period}",
                    "note": "日付は計画書 1 行目の日付(作成・提出・変更のいずれかは様式上区別されない)。掲載ページ: "
                            + (r.get("page_url") or ""),
                    "source_url": r["url"], "retrieved_at": meta["retrieved_at"], "generated_by": "requests"})
        n["municipal_plan_pdf"] += 1
    # 状況
    for code in names:
        for src in ("mlit_jigo_hyoka", "mlit_haibun"):
            k = conn.execute("SELECT COUNT(*) FROM requests WHERE municipality_code=? AND collection_source=?", (code, src)).fetchone()[0]
            _status(conn, code, src, "収集済" if k else "該当なし",
                    f"{k} 件" if k else "全国の資料に当該市町の行なし", source_url=JIGO_PAGE if src == "mlit_jigo_hyoka" else None)
        _status(conn, code, "individual_subsidy_application", "未取得(非公開)",
                "道路メンテナンス事業補助などの個別補助の申請内容は原則非公開")
        for src in ("municipal_plan_pdf", "municipal_page"):
            k = conn.execute("SELECT COUNT(*) FROM requests WHERE municipality_code=? AND collection_source=?", (code, src)).fetchone()[0]
            conn.execute("""INSERT OR REPLACE INTO request_status(municipality_code, collection_source, status, note, retrieved_at)
                            VALUES (?,?,?,?,?)""", (code, src, "収集済" if k else "未収集",
                                                    f"{k} 件" if k else "市町サイトで該当ページを確認できていない", http.now_iso()))
    conn.commit()
    return n


def load_manual(conn: sqlite3.Connection, manual_dir: Path) -> int:
    conn.execute("DELETE FROM requests WHERE generated_by IS NULL")
    n = 0
    for r in _read(manual_dir / "requests.csv"):
        if not (r.get("source_url") and r.get("quote")):
            continue
        row = {c: r.get(c) for c in COLS}
        row["pref_code"] = (r.get("municipality_code") or "")[:2]
        row["project_key"] = plan_key(r.get("project_name") or "")
        row["collection_source"] = r.get("collection_source") or "municipal_page"
        for k in ("fiscal_year",):
            row[k] = int(row[k]) if row.get(k) else None
        for k in ("requested_amount_thousand_yen", "result_amount_thousand_yen"):
            row[k] = float(row[k]) if row.get(k) else None
        _put(conn, row)
        n += 1
    for r in _read(manual_dir / "requests_status.csv"):
        # 記録が実際にある場合は「収集済」を優先(CSV の未収集メモは古い可能性がある)
        k = conn.execute("SELECT COUNT(*) FROM requests WHERE municipality_code=? AND collection_source=?",
                         (r["municipality_code"], r["collection_source"])).fetchone()[0]
        status = "収集済" if k else r["status"]
        _status(conn, r["municipality_code"], r["collection_source"], status,
                (f"{k} 件。" if k else "") + (r.get("note") or ""), r.get("checked_urls") or "", r.get("source_url"))
    conn.commit()
    return n


def for_municipality(conn, code: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT * FROM requests WHERE municipality_code=?
           ORDER BY COALESCE(request_date, result_date, fiscal_year || '-04-01'), record_type""", (code,))]


def chart_date(r: dict) -> tuple[str | None, str | None]:
    """グラフ上の位置(申請の日付, 結果の日付)。年度のみ判明の申請は年度初日の位置に置く(表示で明記)。"""
    req = r["request_date"] or (f"{r['fiscal_year']}-04-01" if r["date_precision"] == "fiscal_year" and r["fiscal_year"] else None)
    return req, r["result_date"]
