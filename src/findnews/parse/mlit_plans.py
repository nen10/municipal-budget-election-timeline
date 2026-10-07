"""国土交通省「社会資本整備総合交付金の事後評価一覧」「防災・安全交付金の事後評価一覧」PDF のパーサー(全国)。

https://www.mlit.go.jp/page/kanbo05_hy_000213.html に掲載。列: 計画名(事後評価へのリンク) | 都道府県 |
計画策定主体 | 開始年度 | 終了年度。計画期間が終わり事後評価を公表した計画の一覧で、実施中の計画は載らない。
計画名のハイパーリンク(事後評価の掲載先。多くは計画主体の公式サイト)も取り出す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .common import norm

_ERA = {"H": 1988, "R": 2018}


def era_code_to_fy(s: str) -> int | None:
    m = re.fullmatch(r"([HR])(\d+|元)", norm(s))
    if not m:
        return None
    return _ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))


@dataclass
class PlanRow:
    program: str                 # 社会資本整備総合交付金 / 防災・安全交付金
    plan_name: str
    pref_name: str
    planners: list[str]
    start_fy: int | None
    end_fy: int | None
    link: str | None
    page: int


def parse_pdf(path: str | Path) -> list[PlanRow]:
    out: list[PlanRow] = []
    with pdfplumber.open(path) as pdf:
        first = norm(pdf.pages[0].extract_text() or "")
        program = "防災・安全交付金" if "防災・安全交付金" in first[:40] else "社会資本整備総合交付金"
        for pno, page in enumerate(pdf.pages, 1):
            links = [(h["top"], h["bottom"], h["uri"]) for h in page.hyperlinks if h.get("uri")]
            for t in page.find_tables():
                data = t.extract()
                for row, cells in zip(t.rows, data):
                    if not cells or not cells[0] or norm(cells[0]).startswith("＜"):
                        continue
                    name = norm(cells[0])
                    top, bottom = row.bbox[1], row.bbox[3]
                    link = next((u for (lt, lb, u) in links if lt >= top - 1 and lb <= bottom + 1), None)
                    out.append(PlanRow(program, name, norm(cells[1]), [norm(x) for x in norm(cells[2]).split(",") if x],
                                       era_code_to_fy(cells[3] or ""), era_code_to_fy(cells[4] or ""), link, pno))
    return out
