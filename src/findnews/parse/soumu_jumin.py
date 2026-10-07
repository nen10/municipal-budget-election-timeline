"""総務省「住民基本台帳に基づく人口、人口動態及び世帯数」市区町村別(総計)Excel のパーサー。

1 行 = 1 団体(先頭列が 6 桁の団体コード)。「人口 計」列(ヘッダ 4 行目が「計」)を読む。
郡計の行(例: 093009 河内郡)は団体コード表にないため、呼び出し側でマスタにあるコードだけを使う。
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl

from .common import norm, to_number


def parse_names(path: str | Path) -> dict[str, tuple[str, str]]:
    """{code: (都道府県名, 市区町村名)}。都道府県計(xx0000)と郡計(名称が「郡」で終わる)は除く。
    「芳賀郡益子町」のような郡名つきは郡名を除く。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb.worksheets[0].iter_rows(values_only=True))
    wb.close()
    out = {}
    for r in rows:
        code = norm(r[0]) if r and r[0] is not None else ""
        if not re.fullmatch(r"\d{6}", code) or code.endswith("0000"):
            continue
        name = norm(r[2])
        if not name or name == "-" or name.endswith("郡"):
            continue
        m = re.match(r"^.+?郡(.+[町村])$", name)
        out[code] = (norm(r[1]), m.group(1) if m else name)
    return out


def parse_workbook(path: str | Path) -> tuple[str | None, dict[str, float]]:
    """戻り値: (基準日 YYYY-MM-DD, {code: 人口})"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    title = norm(rows[0][0]) if rows and rows[0] else ""
    m = re.search(r"令和(\d+)年1月1日", title)
    as_of = f"{2018 + int(m.group(1))}-01-01" if m else None
    col = None
    for r in rows[:8]:
        cells = [norm(c) for c in r]
        if "計" in cells and "男" in cells:
            col = cells.index("計")
            break
    out = {}
    for r in rows:
        code = norm(r[0]) if r and r[0] is not None else ""
        if re.fullmatch(r"\d{6}", code) and col is not None:
            v = to_number(r[col])
            if v is not None:
                out[code] = v
    return as_of, out
