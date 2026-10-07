"""国交省「補助金等に関する情報開示」(交付決定、data_inventory.md B1、DESIGN.md 17 節)の取得。

- 年度一覧: https://www.mlit.go.jp/page/kanbo05_hy_001768-1_00010.html → 年度ページ → 局ごと・半期ごとの Excel。
- 1 行 1 交付決定: 事業名・補助金交付先名・法人番号・交付決定額(円)・支出元会計区分・支出科目(項・目)・交付決定日。
  変更による減額は負の値のまま保存する。
- 地方公共団体の法人番号は「検査用数字 1 桁 + 000020 + 団体コード 6 桁」(例: 那珂川町 2000020094111)。
  この形の行だけを自治体に結合する(福岡県那珂川市 4000020402311 と名前で取り違えないため、名前では結合しない)。
- mlit.go.jp に robots.txt はない(404)。利用規約は公共データ利用規約。
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..parse.common import norm, to_number

SOURCE = "mlit_kofu"
INDEX = "https://www.mlit.go.jp/page/kanbo05_hy_001768-1_00010.html"


def code_from_corporate_number(num) -> str | None:
    """地方公共団体の法人番号から団体コード(6 桁)を返す。地方公共団体でなければ None。"""
    if num is None:
        return None
    s = re.sub(r"\D", "", str(int(num)) if isinstance(num, float) else str(num))
    if len(s) != 13 or s[1:7] != "000020":
        return None
    return s[7:]


def year_pages() -> dict[int, str]:
    s = BeautifulSoup(http.get_html(INDEX), "html.parser")
    out = {}
    for a in s.find_all("a"):
        t = norm(a.get_text())
        m = re.match(r"^(令和|平成)(\d+|元)年度", t)
        if m and "kanbo05_hy" in a.get("href", ""):
            y = (2018 if m.group(1) == "令和" else 1988) + (1 if m.group(2) == "元" else int(m.group(2)))
            if t.startswith("平成31年度"):
                y = 2019
            out[y] = urljoin(INDEX, a["href"])
    return out


def list_files(fiscal_year: int, page: str) -> list[dict]:
    s = BeautifulSoup(http.get_html(page), "html.parser")
    out = []
    for a in s.find_all("a"):
        h = a.get("href", "")
        if h.endswith((".xlsx", ".xls")):
            half = "上半期" if "上半期" in a.get_text() else ("下半期" if "下半期" in a.get_text() else "")
            url = urljoin(page, h)
            out.append({"fiscal_year": fiscal_year, "half": half, "url": url,
                        "filename": f"fy{fiscal_year}_{url.rsplit('/', 1)[1]}"})
    return out


def _date(v):
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    s = norm(v)
    m = re.match(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(令和|平成)(\d+|元)年(\d+)月(\d+)日", s)
    if m:
        y = (2018 if m.group(1) == "令和" else 1988) + (1 if m.group(2) == "元" else int(m.group(2)))
        return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    return None


def parse_workbook(path) -> list[dict]:
    """全シートの交付決定行(地方公共団体以外も含む)。列は見出し行(「法人番号」を含む行)で特定する。"""
    rows = []
    if str(path).endswith(".xls"):
        import xlrd
        wb = xlrd.open_workbook(str(path))
        sheets = [(sh.name, [sh.row_values(i) for i in range(sh.nrows)]) for sh in wb.sheets()]
    else:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in wb.worksheets]
    for sheet, grid in sheets:
        hdr_i, bureau = None, None
        for i, r in enumerate(grid[:15]):
            cells = [norm(c) for c in r]
            if any("法人番号" in c for c in cells):
                hdr_i = i
                break
            texts = [c for c in cells if c and "情報開示" not in c]
            if texts:
                bureau = texts[0]
        if hdr_i is None:
            continue
        h = [norm(c) for c in grid[hdr_i]]
        def col(*keys):
            return next((j for j, c in enumerate(h) if all(k in c for k in keys)), None)
        c_name, c_to, c_num, c_amt = col("事業名"), col("交付先"), col("法人番号"), col("交付", "決定額")
        c_acc, c_item, c_date = col("会計区分"), col("支出科目"), col("交付決定日")
        if c_num is None or c_amt is None:
            continue
        for r in grid[hdr_i + 1:]:
            if len(r) <= c_amt:
                continue
            amt = to_number(r[c_amt])
            if amt is None:
                continue
            item_cells = [norm(x) for x in r[c_item:(c_date or c_item + 1)]] if c_item is not None else []
            item_cells = [x for x in item_cells if x]
            rows.append({"sheet": sheet, "bureau": bureau, "project_name": norm(r[c_name]) if c_name is not None else None,
                         "recipient_name": norm(r[c_to]) if c_to is not None else None,
                         "corporate_number": re.sub(r"\D", "", str(int(r[c_num])) if isinstance(r[c_num], float) else norm(r[c_num])),
                         "amount_yen": amt, "account": norm(r[c_acc]) if c_acc is not None else None,
                         "subsidy_name": item_cells[-1] if item_cells else None,
                         "subsidy_item": " / ".join(item_cells),
                         "decided_date": _date(r[c_date]) if c_date is not None else None})
    return rows


def run(conn: sqlite3.Connection, fiscal_years: list[int], offline: bool = False, force: bool = False,
        only_local_gov: bool = True) -> dict:
    import json
    res = {}
    idx_path = http.raw_dir(SOURCE) / "files.json"
    files = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else []
    if not offline:
        pages = year_pages()
        files = [f for f in files if f["fiscal_year"] not in fiscal_years]
        for fy in fiscal_years:
            if fy not in pages:
                db.log_fetch(conn, SOURCE, "list", "error", f"FY{fy}: 年度ページなし")
                continue
            got = list_files(fy, pages[fy])
            files += got
            for f in got:
                try:
                    http.download(f["url"], SOURCE, f["filename"], force=force)
                except Exception as e:  # noqa: BLE001
                    db.log_fetch(conn, SOURCE, "download", "error", repr(e), f["url"])
        idx_path.write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
    for fy in fiscal_years:
        conn.execute("DELETE FROM grant_decisions WHERE fiscal_year=?", (fy,))
        n = n_local = 0
        errors = []
        for f in [f for f in files if f["fiscal_year"] == fy]:
            p = http.raw_dir(SOURCE) / f["filename"]
            if not p.exists():
                continue
            meta = http.manifest_meta(p) or {}
            try:
                rows = parse_workbook(p)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{f['filename']}: {e!r}")
                continue
            for r in rows:
                code = code_from_corporate_number(r["corporate_number"])
                n += 1
                if only_local_gov and not code:
                    continue
                n_local += 1
                conn.execute(
                    """INSERT INTO grant_decisions(fiscal_year, half, bureau, sheet, project_name, recipient_name,
                       corporate_number, municipality_code, amount_yen, account, subsidy_name, decided_date, source_url,
                       retrieved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (fy, f["half"], r["bureau"], r["sheet"], r["project_name"], r["recipient_name"], r["corporate_number"],
                     code, r["amount_yen"], r["account"], r["subsidy_name"], r["decided_date"], f["url"],
                     meta.get("retrieved_at")))
        conn.commit()
        db.log_fetch(conn, SOURCE, "load", "ok" if n_local else "error",
                     f"FY{fy}: 交付決定 {n} 行(うち地方公共団体 {n_local})" + (f" errors={errors[:3]}" if errors else "")
                     + ("。法人番号の列がない様式(FY2016 以前は交付先名のみ、FY2010 は交付先もない)のため団体コードに"
                        "結び付けられず未取得" if n == 0 else ""))
        res[fy] = {"rows": n, "local_gov_rows": n_local, "errors": len(errors)}
    return res
