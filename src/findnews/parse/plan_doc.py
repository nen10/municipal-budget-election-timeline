"""社会資本総合整備計画の計画書 PDF(国交省のシステムが出力する全国共通の様式)の 1 ページ目を読む。

読み取る項目: 交付金の種類と計画書の日付(1 行目「社会資本総合整備計画 社会資本整備総合交付金 令和06年01月15日」)、
計画の名称、計画の期間、交付対象、全体事業費(百万円)、案件番号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .common import norm, to_number


@dataclass
class PlanDoc:
    program: str | None
    doc_date: str | None
    plan_name: str | None
    period: str | None
    start_fy: int | None
    end_fy: int | None
    grantees: list[str]
    total_cost_million_yen: float | None
    case_no: str | None
    first_line: str


def _fy(s: str) -> int | None:
    m = re.search(r"(令和|平成)(\d+|元)年度", s)
    if not m:
        return None
    return (2018 if m.group(1) == "令和" else 1988) + (1 if m.group(2) == "元" else int(m.group(2)))


def parse_pdf(path: str | Path) -> PlanDoc:
    with pdfplumber.open(path) as pdf:
        text = pdf.pages[0].extract_text() or ""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    t = "\n".join(norm(l) for l in lines)
    first = lines[0] if lines else ""
    m = re.match(r"社会資本総合整備計画\s*(\S+交付金)\s*(令和|平成)(\d+)年(\d+)月(\d+)日", norm(first))
    program, doc_date = (None, None)
    if m:
        program = m.group(1)
        y = (2018 if m.group(2) == "令和" else 1988) + int(m.group(3))
        doc_date = f"{y:04d}-{int(m.group(4)):02d}-{int(m.group(5)):02d}"
    name = re.search(r"計画の名称(.+)", t)
    period = re.search(r"計画の期間(.+?年度)〜(.+?年度)", t)
    grantees = re.search(r"交付対象(.+)", t)
    cost = re.search(r"全体事業費（百万円）合計（Ａ＋Ｂ＋Ｃ＋Ｄ）([\d,]+)", t) or re.search(r"全体事業費.*?合計.*?([\d,]+)", t)
    case = re.search(r"案件番号[:：](\d+)", t)
    return PlanDoc(program, doc_date, name.group(1) if name else None,
                   f"{period.group(1)}〜{period.group(2)}" if period else None,
                   _fy(period.group(1)) if period else None, _fy(period.group(2)) if period else None,
                   [g for g in re.split(r"[、,]", grantees.group(1)) if g] if grantees else [],
                   to_number(cost.group(1)) if cost else None, case.group(1) if case else None, first)
