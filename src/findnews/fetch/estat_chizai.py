"""地方財政状況調査(決算統計)市町村分の取得(e-Stat のファイル、DESIGN.md 17 節、data_inventory.md A1)。

- 一覧: https://www.e-stat.go.jp/stat-search/files?toukei=00200251&tstat=000001077755(市町村分)。
  調査年ごとに一覧ページ(year=<調査年>0)から表のタイトルで statInfId を探す(年ごとに ID が変わるため)。
  決算年度 = 調査年 − 1。e-Stat API は使わない(ユーザー登録が必要なため)。
- robots.txt は /search/ 等のみ拒否で /stat-search/ は対象外。利用規約は公共データ利用規約(出典記載)。
- 全国一括 CSV のため全市区町村分を DB に入れる。保存する行・列は KEEP で絞る(全列を保存すると数百万行になるため)。
- 合併で団体コードが変わった団体は、資料の団体コードをそのまま使う(名寄せしない)。
"""

from __future__ import annotations

import re
import sqlite3

from bs4 import BeautifulSoup

from .. import db, http
from ..parse import estat_chizai as P

SOURCE = "estat_chizai"
LIST = ("https://www.e-stat.go.jp/stat-search/files?page={page}&layout=datalist&toukei=00200251&tstat=000001077755"
        "&cycle=7&year={year}0&tclass1=000001077756&tclass2=000001077757&tclass3val=0")
DL = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={sid}&fileKind=1"

# 04 表で保存する列(行番号 01: 交付税・国庫支出金の区分。行番号 02: 都道府県支出金の区分)
T04_R01 = {"023", "024", "025", "026"} | {f"{i:03d}" for i in range(43, 70)}
T04_R02 = {"001", "002", "016", "052"}
FIN_ROWS = {"25", "26", "27", "34", "35"}        # 07〜13 表: 歳出合計・国庫支出金・都道府県支出金・地方債・一般財源等


def keep(table_no, row_no, row_name, item_code, item_name) -> bool:
    if table_no == "04":
        return (row_no == "01" and item_code in T04_R01) or (row_no == "02" and item_code in T04_R02)
    if table_no in ("07", "08", "09", "10", "11", "12", "13"):
        if row_no not in FIN_ROWS:
            return False
        return table_no == "10" or "総額" in item_name or "・" not in item_name   # 10 表(土木費)は全項目、他は款の総額
    if table_no == "21":
        return row_name in ("補助事業費", "土木費", "道路", "橋りょう", "河川", "都市計画", "農業農村整備") and \
            item_code in ("001", "005", "006", "008", "010")
    if table_no == "70":
        return row_name in ("道路橋りょう費", "合計")
    return False


def _wanted(title: str) -> str | None:
    t = re.sub(r"\s+", "", title)
    if t == "歳入内訳":
        return "04"
    m = re.search(r"（その([１-７1-7])）_(\d)", t)
    if ("歳出内訳及び財源内訳" in t or t.startswith("〃")) and m and "復旧" not in t and "防災" not in t:
        return "07-13"
    if "普通建設事業費の状況" in t and "補助事業費" in t:
        return "21"
    if t == "道路関係経費の状況":
        return "70"
    return None


def list_files(survey_year: int, max_pages: int = 4) -> list[dict]:
    out, seen = [], set()
    in_sai = False
    for page in range(1, max_pages + 1):
        url = LIST.format(page=page, year=survey_year)
        s = BeautifulSoup(http.get_html(url), "html.parser")
        got = 0
        for a in s.find_all("a"):
            m = re.search(r"statInfId=(\d+)&fileKind=1", a.get("href", ""))
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            got += 1
            li = a.find_parent("li")
            title = li.get_text(" ", strip=True).split(" 調査年月")[0] if li else ""
            # 「〃」の行は直前の見出し(歳出内訳/復旧・復興/全国防災)を引き継ぐ
            nt = re.sub(r"\s+", "", title)
            if not nt.startswith("〃"):
                in_sai = nt.startswith("歳出内訳及び財源内訳")
            kind = _wanted(title)
            if kind == "07-13" and not in_sai:
                kind = None
            if kind:
                out.append({"survey_year": survey_year, "fiscal_year": survey_year - 1, "sid": m.group(1), "title": title,
                            "kind": kind, "url": DL.format(sid=m.group(1)), "list_url": url,
                            "filename": f"s{survey_year}_{m.group(1)}.csv"})
        if not got:
            break
    return out


def load_file(conn: sqlite3.Connection, path, meta: dict) -> int:
    n = 0
    labels = {}
    ents = {}
    rows = []
    for c in P.iter_cells(path, keep):
        rows.append((c.fiscal_year, c.code, c.table_no, c.row_no, c.item_code, c.value))
        labels[(c.fiscal_year, c.table_no, c.row_no, c.item_code)] = (c.row_name, c.item_name)
        ents[(c.fiscal_year, c.code)] = (c.pref_name, c.name, c.kubun)
    conn.executemany("INSERT OR REPLACE INTO estat_values VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT OR REPLACE INTO estat_labels VALUES (?,?,?,?,?,?,?,?)",
                     [(k[0], k[1], k[2], k[3], v[0], v[1], meta.get("url"), meta.get("retrieved_at")) for k, v in labels.items()])
    conn.executemany("INSERT OR REPLACE INTO estat_entities VALUES (?,?,?,?,?)",
                     [(k[0], k[1], v[0], v[1], v[2]) for k, v in ents.items()])
    conn.commit()
    n = len(rows)
    return n


def run(conn: sqlite3.Connection, fiscal_years: list[int], offline: bool = False, force: bool = False) -> dict:
    import json
    res = {}
    for fy in fiscal_years:
        sy = fy + 1
        idx = http.raw_dir(SOURCE) / f"list_{sy}.json"
        if not offline:
            try:
                files = list_files(sy)
                idx.write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
                db.log_fetch(conn, SOURCE, "list", "ok" if files else "error", f"調査年 {sy}: {len(files)} files")
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "list", "error", f"調査年 {sy}: {e!r}")
                files = []
            for f in files:
                try:
                    http.download(f["url"], SOURCE, f["filename"], force=force)
                except Exception as e:  # noqa: BLE001
                    db.log_fetch(conn, SOURCE, "download", "error", f"{f['title']}: {e!r}", f["url"])
        files = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
        n = 0
        kinds = {}
        for f in files:
            p = http.raw_dir(SOURCE) / f["filename"]
            if not p.exists():
                continue
            meta = http.manifest_meta(p) or {}
            k = load_file(conn, p, {"url": f["url"], "retrieved_at": meta.get("retrieved_at")})
            n += k
            kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
        db.log_fetch(conn, SOURCE, "load", "ok" if n else "error", f"FY{fy}(調査年 {sy}): {kinds} rows={n}")
        res[fy] = {"files": kinds, "rows": n}
    return res
