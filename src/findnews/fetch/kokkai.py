"""国立国会図書館 国会会議録検索システム API(https://kokkai.ndl.go.jp/api.html)。

- エンドポイント: https://kokkai.ndl.go.jp/api/speech(発言単位、1 リクエスト最大 100 件)
- 利用条件: 「機械的なアクセスは同時に複数リクエストを行わず、取得完了後に数秒の間隔をあける」旨の記載が
  あるため、本モジュールは 3 秒間隔(KOKKAI_INTERVAL)で逐次アクセスする。
- 検索: 設計書 5.3 のキーワードごとに `any`(全文検索)で検索し、さらに politicians.csv の議員名で
  `speaker` を絞った検索も行う。API の全文検索は語の分割で部分一致することがあるため、
  取得後に本文へ文字列一致を再判定し、一致キーワードを matched_keywords に記録する。
- 応答 JSON は data/raw/kokkai/ にクエリごとに保存する。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from .. import db, http
from ..keywords import STATEMENT_KEYWORDS
from ..municipalities import _MASTERS, normalize_name

SOURCE = "kokkai"
API = "https://kokkai.ndl.go.jp/api/speech"
KOKKAI_INTERVAL = 3.0
MAX_PER_QUERY = 300  # 1 キーワードあたりの取得上限(100 件 × 3 ページ)


def _query_name(params: dict) -> str:
    key = json.dumps(params, ensure_ascii=False, sort_keys=True)
    return "q_" + hashlib.sha1(key.encode()).hexdigest()[:12]


def list_queries(conn: sqlite3.Connection | None, since: str, keywords=None) -> list[dict]:
    keywords = keywords or STATEMENT_KEYWORDS
    qs = [{"any": kw, "from": since} for kw in keywords]
    if conn is not None:
        for r in conn.execute("SELECT name FROM politicians"):
            # 議員本人の発言は期間を限定せずキーワード検索
            qs += [{"any": kw, "speaker": r["name"]} for kw in keywords]
    return qs


def download(params: dict, max_records: int = MAX_PER_QUERY) -> list:
    """1 クエリ分をページングして保存。保存先パスのリストを返す。"""
    d = http.raw_dir(SOURCE)
    paths = []
    start = 1
    while start <= max_records:
        p = dict(params, recordPacking="json", maximumRecords=100, startRecord=start)
        time.sleep(max(0.0, KOKKAI_INTERVAL - 1.0))  # http.get の 1 秒と合わせて約 3 秒
        resp = http.get(API, params=p)
        data = resp.json()
        name = f"{_query_name(params)}_{start}.json"
        payload = {"query": params, "request_url": resp.url, "retrieved_at": http.now_iso(), "response": data}
        (d / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths.append(d / name)
        n = int(data.get("numberOfRecords", 0) or 0)
        nxt = data.get("nextRecordPosition")
        if not nxt or nxt > n:
            break
        start = int(nxt)
    return paths


def local_files():
    return sorted(http.raw_dir(SOURCE).glob("q_*.json"))


def match_keywords(body: str, keywords=None) -> list[str]:
    keywords = keywords or STATEMENT_KEYWORDS
    b = normalize_name(body)
    return [k for k in keywords if normalize_name(k) in b]


def target_municipalities(body: str, pref_codes: list[str] | None = None) -> list[str]:
    """本文中の市区町村名から対象自治体を推定(読み込み済みのマスタの都道府県。既定は全部)。
    「那須町」が「那須塩原市」に誤一致しないよう、長い名称から照合して除去していく。
    他県に同名の市町村がある場合は両方のコードが入る(人が確認すること)。"""
    b = normalize_name(body)
    found = []
    by_name: dict[str, list[str]] = {}
    for p, m in _MASTERS.items():
        if pref_codes is None or p in pref_codes:
            for c, n in m.items():
                by_name.setdefault(n, []).append(c)
    for name in sorted(by_name, key=len, reverse=True):
        if name in b:
            found.extend(by_name[name])
            b = b.replace(name, "\0")
    return sorted(set(found))


def parse(paths) -> list[dict]:
    seen = {}
    for p in paths:
        payload = json.loads(p.read_text(encoding="utf-8"))
        for rec in payload["response"].get("speechRecord", []) or []:
            sid = rec.get("speechID")
            if not sid or sid in seen:
                continue
            body = rec.get("speech") or ""
            seen[sid] = {
                "external_id": sid,
                "speaker": rec.get("speaker"),
                "speaker_group": rec.get("speakerGroup"),
                "speaker_position": rec.get("speakerPosition"),
                "date": rec.get("date"),
                "meeting": f"{rec.get('nameOfHouse', '')} {rec.get('nameOfMeeting', '')} {rec.get('issue', '')}".strip(),
                "body": body,
                "matched": match_keywords(body),
                "targets": target_municipalities(body),
                "url": rec.get("speechURL"),
                "retrieved_at": payload.get("retrieved_at"),
                "request_url": payload.get("request_url"),
            }
    return list(seen.values())


def load(conn: sqlite3.Connection, parsed: list[dict]) -> int:
    idx = {}
    for r in conn.execute("SELECT politician_id, name, name_variants FROM politicians"):
        for v in [r["name"]] + (r["name_variants"] or "").split("|"):
            if v.strip():
                idx[normalize_name(v)] = r["politician_id"]
    n = 0
    for s in parsed:
        if not s["matched"]:
            continue  # API 上はヒットしたが本文に完全一致がないもの(語分割による部分一致)は保存しない
        conn.execute(
            """INSERT OR REPLACE INTO statements(source, external_id, speaker, speaker_group, speaker_position,
               politician_id, date, meeting, body, matched_keywords, target_municipalities, source_url, retrieved_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (SOURCE, s["external_id"], s["speaker"], s["speaker_group"], s["speaker_position"],
             idx.get(normalize_name(s["speaker"] or "")), s["date"], s["meeting"], s["body"],
             "|".join(s["matched"]), ",".join(s["targets"]), s["url"], s["retrieved_at"]),
        )
        n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, since: str = "2015-01-01", offline: bool = False, keywords=None,
        max_records: int = MAX_PER_QUERY) -> dict:
    if not offline:
        for q in list_queries(conn, since, keywords):
            try:
                paths = download(q, max_records)
                total = json.loads(paths[0].read_text(encoding="utf-8"))["response"].get("numberOfRecords") if paths else 0
                db.log_fetch(conn, SOURCE, "download", "ok", f"{q} numberOfRecords={total} pages={len(paths)}", API)
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", f"{q}: {e!r}", API)
    parsed = parse(local_files())
    n = load(conn, parsed)
    db.log_fetch(conn, SOURCE, "parse", "ok", f"speeches={len(parsed)} stored(本文一致)={n}")
    return {"speeches": len(parsed), "stored": n}
