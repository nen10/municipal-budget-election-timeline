"""国土交通省「国土交通省関係予算の配分について」の都道府県別「事業実施箇所」PDF のパーサー。

1 つの PDF(例: 栃木県分)から次を取り出す。
  1. 「社会資本整備総合交付金の配分」表(社会資本整備総合交付金・防災・安全交付金)
     列: 計画名 | 計画策定主体 | 配分国費(千円) | 備考
     計画策定主体が複数の自治体の「共同計画」は自治体別の内訳が公表されていないため、
     attribution='joint' として自治体には按分しない(推定で割り振らない)。
道路局の箇所表(同じ PDF の先頭部分)は findnews.parse.mlit_road で扱う。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from ..municipalities import lookup, normalize_name
from ..prefs import name as pref_name_of
from .common import norm, to_number

_SUBHEAD = re.compile(r"^(社会資本整備総合交付金|防災・安全交付金)\s*（単位")

PROGRAM_IDS = {"社会資本整備総合交付金": "mlit_shasoukou", "防災・安全交付金": "mlit_bouan"}


@dataclass
class GrantRow:
    program: str                    # 社会資本整備総合交付金 / 防災・安全交付金
    plan_name: str
    recipients: list[str]
    recipient_codes: list[str]
    unresolved: list[str]
    amount_thousand_yen: float | None
    page: int
    attribution: str = field(default="joint")   # sole / joint / prefecture
    municipality_code: str | None = None


def classify(recipients: list[str], pref_code: str = "09") -> tuple[str, str | None, list[str], list[str]]:
    pref_name = pref_name_of(pref_code)
    codes, unresolved = [], []
    for r in recipients:
        if r == pref_name:
            continue
        c = lookup(r, pref_code)
        (codes.append(c) if c else unresolved.append(r))
    munis = sorted(set(codes))
    if not munis and not unresolved:
        return "prefecture", None, munis, unresolved
    if len(munis) == 1 and not unresolved and pref_name not in recipients:
        return "sole", munis[0], munis, unresolved
    return "joint", None, munis, unresolved


def grant_totals(page) -> tuple[str | None, float | None]:
    """ページ内の「合 計」行の金額(検算用)。"""
    text = page.extract_text() or ""
    program = None
    for line in text.splitlines()[:6]:
        m = _SUBHEAD.match(line.strip())
        if m:
            program = m.group(1)
    for line in text.splitlines():
        m = re.match(r"^合\s*計\s+([\d,]+)", line.strip())
        if m:
            return program, to_number(m.group(1))
    return program, None


def parse_grant_page(page, page_no: int, pref_code: str = "09") -> list[GrantRow]:
    text = page.extract_text() or ""
    program = None
    for line in text.splitlines()[:6]:
        m = _SUBHEAD.match(line.strip())
        if m:
            program = m.group(1)
            break
    if program is None:
        return []
    out = []
    for tb in page.extract_tables():
        for row in tb:
            if not row or len(row) < 3:
                continue
            name, who, amount = (row[0] or ""), (row[1] or ""), row[2]
            if norm(name) in ("計画名", "合計") or norm(name).startswith("合計"):
                continue
            if not norm(who):
                continue
            recips = [normalize_name(x) for x in norm(who).split(",") if x]
            attribution, code, codes, unresolved = classify(recips, pref_code)
            out.append(GrantRow(program, norm(name), recips, codes, unresolved, to_number(amount), page_no,
                                attribution, code))
    return out


def parse_pdf(path: str | Path, pref_code: str = "09") -> dict:
    grants: list[GrantRow] = []
    totals: dict[str, float] = {}
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            text = p.extract_text() or ""
            if "計画策定主体" in text:
                grants.extend(parse_grant_page(p, i, pref_code))
                prog, tot = grant_totals(p)
                if prog and tot is not None:
                    totals[prog] = tot
    # 検算: 表の「合計」と行の合計の差(0 でなければ取りこぼしの疑い)
    check = {prog: tot - sum(g.amount_thousand_yen or 0 for g in grants if g.program == prog)
             for prog, tot in totals.items()}
    return {"grants": grants, "totals": totals, "total_check": check}
