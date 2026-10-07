"""栃木県選挙管理委員会「候補者別開票区別得票数調」(衆院小選挙区、Excel .xls)のパーサー。

2026 年 2 月 8 日執行の衆院選について、県選管サイトに PDF は見当たらず、確定値は HTML と Excel(.xls)で
公表されている(https://www.pref.tochigi.lg.jp/senkyo/r08syugi/kekka.html の「開票結果『選挙区』
候補者別開票区別得票数」)。本パーサーは .xls を読む。

シートは選挙区ごとのブロックが縦に並ぶ:
  「区分」行(候補者名) → 党派行 → 「【 第N区 】」 → 開票区行 … → 「第N区計」 → 「（惜敗率）」
惜敗率は整数部と「.433」のような小数部が別セルに分かれている。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import norm, to_number

_KANJI = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_SKIP_ROWS = ("市部計", "郡部計")


@dataclass
class ResultRow:
    district: int
    counting_unit: str
    candidate: str
    party: str | None
    votes: float | None


@dataclass
class DistrictSummary:
    district: int
    totals: dict[str, float]
    sekihai: dict[str, float]
    winner: str | None


def _district_no(s: str) -> int | None:
    m = re.search(r"第(\d+|[一二三四五六七八九十]+)区", norm(s))
    if not m:
        return None
    g = m.group(1)
    return int(g) if g.isdigit() else sum(_KANJI[c] for c in g) if len(g) == 1 else _kanji_num(g)


def _kanji_num(g: str) -> int:
    if g.startswith("十"):
        return 10 + (_KANJI[g[1]] if len(g) > 1 else 0)
    if len(g) == 2 and g[1] == "十":
        return _KANJI[g[0]] * 10
    return _KANJI[g[0]] * 10 + _KANJI[g[2]] if len(g) == 3 else _KANJI[g]


def read_xls(path: str | Path) -> list[list]:
    import io

    import xlrd
    # 県選管の .xls はセクタサイズの警告が出るが内容は読めるため、警告ログは捨てる
    wb = xlrd.open_workbook(str(path), logfile=io.StringIO())
    sh = wb.sheet_by_index(0)
    return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]


def parse_grid(grid: list[list]) -> tuple[list[ResultRow], list[DistrictSummary], dict]:
    rows: list[ResultRow] = []
    summaries: list[DistrictSummary] = []
    meta = {"title": None, "status": None}
    cand_cols: dict[int, str] = {}
    party: dict[int, str] = {}
    district: int | None = None
    totals: dict[str, float] = {}
    for r, row in enumerate(grid):
        cells = [norm(v) for v in row]
        first = cells[1] if len(cells) > 1 else ""
        joined = "".join(cells)
        if meta["title"] is None and "執行" in joined:
            meta["title"] = joined
        if "確定" in joined and "現在" in joined:
            meta["status"] = joined
        if first == "区分":
            end = next((i for i, v in enumerate(cells) if v == "得票総数"), len(cells))
            cand_cols = {i: str(row[i]).replace("　", " ").strip() for i in range(2, end)
                         if cells[i] and cells[i] != "確定表示"}
            nxt = grid[r + 1] if r + 1 < len(grid) else []
            party = {i: norm(nxt[i]) or None for i in cand_cols if i < len(nxt)}
            continue
        if first.startswith("【") and (d := _district_no(first)):
            district, totals = d, {}
            continue
        if district is None or not cand_cols or not first:
            continue
        if re.fullmatch(r"第.+区計", first):
            totals = {name: to_number(row[i]) for i, name in cand_cols.items()}
            continue
        if first == "（惜敗率）" or first == "(惜敗率)":
            sek = {}
            for i, name in cand_cols.items():
                ip, fp = to_number(row[i]), norm(row[i + 1]) if i + 1 < len(row) else ""
                if ip is not None:
                    sek[name] = ip + (float(fp) if re.fullmatch(r"\.\d+", fp) else 0.0)
            winner = max(totals, key=lambda k: totals[k] or -1) if totals else None
            summaries.append(DistrictSummary(district, totals, sek, winner))
            district, cand_cols = None, {}
            continue
        if first.endswith("郡計") or first in _SKIP_ROWS:
            continue
        for i, name in cand_cols.items():
            rows.append(ResultRow(district, first, name, party.get(i), to_number(row[i])))
    return rows, summaries, meta
