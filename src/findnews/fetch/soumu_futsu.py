"""総務省 普通交付税 市町村別決定額(A9)と市町村別(費目別)基準財政需要額(A10)の取得。

- ページ: https://www.soumu.go.jp/main_sosiki/c-zaisei/kouhu.html
  「(参考:令和N年度 市町村別交付決定額)」「(参考:令和N年度 市町村別変更決定額)」の xlsx(R3 以降)。
  決定日は同じ行の「令和N年度普通交付税の算定結果」「…再算定結果」の報道資料ページの日付から取る。
- 基準財政需要額の xlsx は同ページに都道府県分・市町村分が並ぶため、ファイル 1 行目の表題
  (「令和８年度 市町村別(費目別)基準財政需要額」)で年度と市町村分かを判定する。
- 決定額の表は団体コードがなく「都道府県名(各都道府県の先頭行のみ)+ 市町村名」なので、
  市区町村マスタ(住民基本台帳人口の団体コード)で名前からコードを引く。引けない行は記録して捨てる。
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import db, http
from ..municipalities import lookup, normalize_name
from ..parse.common import norm, to_number
from ..prefs import PREFS

SOURCE = "soumu_futsu"
PAGE = "https://www.soumu.go.jp/main_sosiki/c-zaisei/kouhu.html"


def _fy(text: str) -> int | None:
    m = re.search(r"令和(\d+|元|[０-９]+)年度", norm(text))
    if not m:
        return None
    g = m.group(1)
    return 2019 if g == "元" else 2018 + int(g)


def list_sources() -> dict:
    s = BeautifulSoup(http.get_html(PAGE), "html.parser")
    decision, juyo, press = [], [], {}
    for a in s.find_all("a"):
        t = norm(a.get_text())
        h = urljoin(PAGE, a.get("href", ""))
        if re.match(r"令和.+年度普通交付税の(再)?算定結果", t):
            press[(_fy(t), "変更" if "再算定" in t else "当初")] = h
        if "市町村別交付決定額" in t or "市町村別変更決定額" in t:
            decision.append({"fiscal_year": _fy(t), "kind": "変更" if "変更" in t else "当初", "url": h,
                             "filename": f"decision_{h.rsplit('/', 1)[1]}"})
        if t in ("基準財政需要額",) and h.endswith((".xlsx", ".xls")):
            prev = a.find_previous(string=lambda x: x and "《" in x and "年度" in x)
            juyo.append({"url": h, "filename": f"juyo_{h.rsplit('/', 1)[1]}", "heading": norm(prev or "")})
    return {"decision": decision, "juyo": juyo, "press": press}


def press_date(url: str) -> str | None:
    t = norm(BeautifulSoup(http.get_html(url), "html.parser").get_text(" "))
    m = re.search(r"令和(\d+|元)年(\d+)月(\d+)日", t)
    if not m:
        return None
    y = 2019 if m.group(1) == "元" else 2018 + int(m.group(1))
    return f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _grid(path):
    if str(path).endswith(".xls"):
        import xlrd
        sh = xlrd.open_workbook(str(path), logfile=__import__("io").StringIO()).sheet_by_index(0)
        return [sh.row_values(i) for i in range(sh.nrows)]
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]


def parse_decision(path) -> tuple[int | None, list[tuple[str, str, float]], list[str]]:
    """戻り値: (年度, [(都道府県名, 市町村名, 決定額 千円)], 解決できない行のメモ)"""
    g = _grid(path)
    title = next((norm(c) for r in g[:4] for c in r if c and "普通交付税" in norm(c)), "")
    fy = _fy(title)
    out, pref = [], None
    for r in g:
        cells = [c for c in r if c not in (None, "")]
        if len(cells) < 2:
            continue
        names = [norm(c) for c in cells if isinstance(c, str)]
        nums = [to_number(c) for c in cells if not isinstance(c, str)]
        if names and names[0] in PREFS.values():
            pref = names[0]
            names = names[1:]
        if pref and names and nums and nums[0] is not None:
            out.append((pref, names[0], nums[0]))
    return fy, out, []


def parse_juyo(path) -> dict:
    """戻り値: {fiscal_year, municipal: bool, rows: {code: {費目: 千円}}}"""
    g = _grid(path)
    title = norm(g[0][0] if g and g[0] else "")
    fy = _fy(title)
    municipal = "市町村" in title
    hdr = None
    for i, r in enumerate(g[:8]):
        if any(norm(c) == "道路橋りょう費" for c in r):
            hdr = i
            break
    cols = {}
    if hdr is not None:
        for j, c in enumerate(g[hdr]):
            n = norm(c)
            if n == "道路橋りょう費":
                cols["道路橋りょう費"] = j
        for i in range(max(0, hdr - 2), hdr + 1):
            for j, c in enumerate(g[i]):
                n = norm(c)
                if n.startswith("基準財政需要額") or n in ("合計", "需要額合計"):
                    cols.setdefault("基準財政需要額 計", j)
    rows = {}
    for r in g:
        c0 = norm(r[0]) if r else ""
        m = re.fullmatch(r"C(\d{6})\d*", c0)
        if not m:
            continue
        rows[m.group(1)] = {k: to_number(r[j]) for k, j in cols.items() if j < len(r)}
    return {"fiscal_year": fy, "municipal": municipal, "rows": rows, "title": title, "cols": cols}


def run(conn: sqlite3.Connection, fiscal_years: list[int], offline: bool = False, force: bool = False) -> dict:
    import json
    from ..municipalities import load_masters
    load_masters(conn)
    idx = http.raw_dir(SOURCE) / "sources.json"
    if not offline:
        src = list_sources()
        dates = {f"{k[0]}_{k[1]}": press_date(u) for k, u in src["press"].items() if k[0] in fiscal_years}
        for f in src["decision"]:
            if f["fiscal_year"] in fiscal_years:
                http.download(f["url"], SOURCE, f["filename"], force=force)
        for f in src["juyo"]:
            try:
                http.download(f["url"], SOURCE, f["filename"], force=force)
            except Exception as e:  # noqa: BLE001
                db.log_fetch(conn, SOURCE, "download", "error", repr(e), f["url"])
        idx.write_text(json.dumps({"decision": src["decision"], "juyo": src["juyo"], "dates": dates,
                                   "press": {f"{k[0]}_{k[1]}": v for k, v in src["press"].items()}},
                                  ensure_ascii=False), encoding="utf-8")
    src = json.loads(idx.read_text(encoding="utf-8"))
    res = {"decision": {}, "juyo": {}, "unresolved": 0}
    for f in src["decision"]:
        fy, kind = f["fiscal_year"], f["kind"]
        p = http.raw_dir(SOURCE) / f["filename"]
        if fy not in fiscal_years or not p.exists():
            continue
        if p.suffix == ".pdf":
            db.log_fetch(conn, SOURCE, "parse", "skipped", f"{f['filename']}: FY{fy} {kind} は PDF のみ(未取得として扱う)", f["url"])
            res["decision"][f"{fy}_{kind}"] = "PDF のため未取得"
            continue
        meta = http.manifest_meta(p) or {}
        _, rows, _ = parse_decision(p)
        pref_code = {v: k for k, v in PREFS.items()}
        n = bad = 0
        for pref, name, amt in rows:
            pc = pref_code[pref]
            try:
                code = lookup(name, pc)
            except LookupError:
                code = None
            if not code or normalize_name(name) not in normalize_name(_name_of(conn, code) or ""):
                bad += 1
                continue
            conn.execute("""INSERT OR REPLACE INTO municipality_fiscal(code, fiscal_year, item, value, unit, source, source_url,
                            retrieved_at) VALUES (?,?,?,?,?,?,?,?)""",
                         (code, fy, f"普通交付税 {kind}決定額", amt, "千円", SOURCE, f["url"], meta.get("retrieved_at")))
            n += 1
        res["decision"][f"{fy}_{kind}"] = n
        res["unresolved"] += bad
        db.log_fetch(conn, SOURCE, "parse", "ok" if n else "error",
                     f"{f['filename']}: FY{fy} {kind} {n} 団体(名前で団体コードを引けなかった行 {bad})", f["url"])
    for f in src["juyo"]:
        p = http.raw_dir(SOURCE) / f["filename"]
        if not p.exists():
            continue
        d = parse_juyo(p)
        if not d["municipal"] or d["fiscal_year"] not in fiscal_years or not d["rows"]:
            continue
        meta = http.manifest_meta(p) or {}
        head = f.get("heading", "")
        kind = "再算定後" if ("再算定" in head or "再算定" in d["title"]) else "当初"
        known = {r[0] for r in conn.execute("SELECT code FROM municipalities")}
        skipped = [c for c in d["rows"] if c not in known]
        for code, vals in d["rows"].items():
            if code not in known:
                continue
            for k, v in vals.items():
                conn.execute("""INSERT OR REPLACE INTO municipality_fiscal(code, fiscal_year, item, value, unit, source,
                                source_url, retrieved_at) VALUES (?,?,?,?,?,?,?,?)""",
                             (code, d["fiscal_year"], f"基準財政需要額 {k}({kind})", v, "千円", SOURCE, f["url"],
                              meta.get("retrieved_at")))
        res["juyo"][f"{d['fiscal_year']}_{kind}"] = len(d["rows"]) - len(skipped)
        if skipped:
            db.log_fetch(conn, SOURCE, "parse", "ok", f"{f['filename']}: 市区町村マスタにない団体コード {len(skipped)} 件は保存しない(例 {skipped[:3]})")
    conn.commit()
    (http.raw_dir(SOURCE) / "dates.json").write_text(json.dumps(src.get("dates", {})), encoding="utf-8")
    return res


def _name_of(conn, code):
    r = conn.execute("SELECT name FROM municipalities WHERE code=?", (code,)).fetchone()
    return r[0] if r else None
