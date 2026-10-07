"""総務省「市町村決算カード」(都道府県別 Excel、1 シート = 1 団体)のパーサー。

レイアウトは固定のセル番地ではなく、ラベル文字列を探してその右側の最初の値を読む方式にしている。
ラベルの右に数値ではない文字列が先に現れた場合はそのラベル出現を見出しとみなして次の出現を探す
(例: 目的別歳出の見出し「普通建設事業費」は読み飛ばし、性質別歳出の行を読む)。
「-」は NULL として保存する(0 で埋めない)。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from ..municipalities import check_digit
from .common import era_year_to_ad, is_placeholder, norm, to_number

# item 名 -> (ラベル, 単位)
ITEMS: dict[str, tuple[str, str]] = {
    "歳入合計": ("歳入合計", "千円"),
    "地方税": ("地方税", "千円"),
    "地方交付税": ("地方交付税", "千円"),
    "普通交付税": ("普通交付税", "千円"),
    "特別交付税": ("特別交付税", "千円"),
    "国庫支出金": ("国庫支出金", "千円"),
    "都道府県支出金": ("都道府県支出金", "千円"),
    "地方債": ("地方債", "千円"),
    "歳出合計": ("歳出合計", "千円"),
    "普通建設事業費": ("普通建設事業費", "千円"),
    "普通建設事業費_うち補助": ("うち補助", "千円"),
    "普通建設事業費_うち単独": ("うち単独", "千円"),
    "災害復旧事業費": ("災害復旧事業費", "千円"),
    "土木費": ("土木費", "千円"),
    "農林水産業費": ("農林水産業費", "千円"),
    "財政力指数": ("財政力指数", "指数"),
    "標準財政規模": ("標準財政規模", "千円"),
}


@dataclass
class CardRecord:
    code: str
    name: str
    fiscal_year: int
    item: str
    value: float | None
    unit: str
    sheet: str


def _grid(ws) -> list[list]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _value_right(row: list, col: int):
    """col より右で最初に現れる非空セル。数値か「-」なら値、文字列なら見出しとみなし _HEADER を返す。"""
    for v in row[col + 1:]:
        if v is None or norm(v) == "":
            continue
        if is_placeholder(v):
            return None
        num = to_number(v)
        if num is not None:
            return num
        return _HEADER
    return _HEADER


_HEADER = object()


def find_labeled_value(grid: list[list], label: str):
    """ラベルに一致するセルを行優先で探し、右側の値を返す。見つからなければ KeyError。"""
    target = norm(label)
    for row in grid:
        for ci, v in enumerate(row):
            if norm(v) == target:
                val = _value_right(row, ci)
                if val is not _HEADER:
                    return val
    raise KeyError(label)


def find_code(grid: list[list], pref_code: str) -> str | None:
    """「09」「2151」のように 2 桁 + 4 桁で書かれた団体コードを探して 6 桁にする。"""
    for row in grid:
        cells = [(i, norm(v)) for i, v in enumerate(row) if v is not None and norm(v) != ""]
        for k, (_, v) in enumerate(cells[:-1]):
            if v == pref_code and re.fullmatch(r"\d{4}", cells[k + 1][1]):
                code = pref_code + cells[k + 1][1]
                if check_digit(code[:5]) == code[5]:
                    return code
    return None


def find_fiscal_year(grid: list[list]) -> int | None:
    for row in grid[:6]:
        for v in row:
            if v is not None and re.fullmatch(r"(令和|平成)(元|\d+)年度", norm(v)):
                return era_year_to_ad(norm(v))
    return None


def find_population(grid: list[list]) -> tuple[float | None, str | None]:
    """住民基本台帳人口(調査対象年度の 1 月 1 日現在、表の最上段)。"""
    for row in grid[:12]:
        for ci, v in enumerate(row):
            m = re.fullmatch(r"(令|平)(\d+)\.1\.1", norm(v))
            if m:
                val = _value_right(row, ci)
                base = 2018 if m.group(1) == "令" else 1988
                return (None if val is _HEADER else val), f"{base + int(m.group(2))}-01-01"
    return None, None


def parse_sheet(ws, pref_code: str = "09") -> list[CardRecord]:
    grid = _grid(ws)
    code = find_code(grid, pref_code)
    fy = find_fiscal_year(grid)
    if code is None or fy is None:
        return []
    m = re.fullmatch(r"\d+(.+)", ws.title)
    name = m.group(1) if m else ws.title
    out: list[CardRecord] = []
    for item, (label, unit) in ITEMS.items():
        try:
            val = find_labeled_value(grid, label)
        except KeyError:
            val = None  # ラベル自体がない年度は NULL
        out.append(CardRecord(code, name, fy, item, val, unit, ws.title))
    pop, pop_date = find_population(grid)
    out.append(CardRecord(code, name, fy, "住民基本台帳人口", pop, "人", ws.title))
    return out


def parse_workbook(path: str | Path, pref_code: str = "09") -> list[CardRecord]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[CardRecord] = []
    for ws in wb.worksheets:
        out.extend(parse_sheet(ws, pref_code))
    wb.close()
    return out
