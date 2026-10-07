"""地方財政状況調査 市町村分 CSV(e-Stat、cp932)のパーサー。

1 ファイル = 1 表 × 全団体。行は「団体 × 行番号」。列見出し(001:地方税 …)は行番号のまとまりごとに見出し行
(行番号の列が「行番号」の行)として繰り返し現れ、まとまりごとに意味が違う(例: 04 表の行番号 01 と 02)。
見出し行が出るたびに列名を切り替えて読む。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .common import to_number


@dataclass
class Cell:
    fiscal_year: int
    code: str
    pref_name: str
    name: str
    kubun: str
    table_no: str
    row_no: str
    row_name: str
    item_code: str
    item_name: str
    value: float | None


def iter_cells(path: str | Path, keep=None):
    """keep(table_no, row_no, row_name, item_code, item_name) -> bool で保存対象を絞る(None なら全部)。"""
    text = Path(path).read_bytes().decode("cp932", errors="replace")
    header: list[str] | None = None
    for r in csv.reader(io.StringIO(text)):
        if len(r) < 11:
            continue
        if r[8].strip() == "行番号":
            header = r
            continue
        if header is None or not r[0].strip().isdigit():
            continue
        fy, code, table_no, row_no, row_name = int(r[0]), r[2].strip(), r[6].strip(), r[8].strip(), r[9].strip()
        for i in range(10, min(len(r), len(header))):
            h = header[i].strip()
            if not h or ":" not in h:
                continue
            ic, iname = h.split(":", 1)
            if keep and not keep(table_no, row_no, row_name, ic, iname):
                continue
            yield Cell(fy, code, r[3].strip(), r[4].strip(), r[5].strip(), table_no, row_no, row_name, ic, iname,
                       to_number(r[i]))
