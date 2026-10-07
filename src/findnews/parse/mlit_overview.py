"""国交省「国土交通省関係予算の配分について」の概要 PDF(全国計)のパーサー。

- 社会資本整備総合交付金の配分概要(「Ⅱ.予算配分総括表」): 注記「国費ベースは、合計 X 億円であり、内訳は…
  社会資本整備総合交付金 Y 億円、防災・安全交付金 Z 億円」から国費(億円)を読む。表本体は事業費。
- 道路関係予算配分概要(道路局): 「配分総括表」の補助事業 計(事業費、百万円)と「都道府県別等配分額【補助事業】」の
  都道府県の計(百万円)。
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from .common import norm, to_number


def _text(path, pages=None) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [p.extract_text() or "" for p in (pdf.pages if pages is None else pdf.pages[:pages])]


def shasoukou_national(path: str | Path) -> dict:
    """戻り値: {"国費 計": 億円, "社会資本整備総合交付金 国費": 億円, "防災・安全交付金 国費": 億円, "事業費 計": 億円}"""
    out = {}
    for t in _text(path, 5):
        n = norm(t)
        if "予算配分総括表" not in n:
            continue
        m = re.search(r"国費ベース.*?合計([\d,]+)億円", n)
        if m:
            out["国費 計"] = to_number(m.group(1))
        m = re.search(r"社会資本整備総合交付金([\d,]+)億円", n)
        if m:
            out["社会資本整備総合交付金 国費"] = to_number(m.group(1))
        m = re.search(r"防災・安全交付金(?:交付金)?([\d,]+)億円", n)
        if m:
            out["防災・安全交付金 国費"] = to_number(m.group(1))
        m = re.search(r"社会資本総合整備事業計([\d,]+)", n)
        if m:
            out["事業費 計"] = to_number(m.group(1))
        if out:
            break
    return out


def road_totals(path: str | Path, pref_short: str) -> dict:
    """戻り値: {"national_hojo": 百万円, "pref_hojo": 百万円}(事業費ベース)"""
    out = {}
    pages = _text(path, 8)
    for t in pages:
        if "配分総括表" in norm(t) and "national_hojo" not in out:
            for line in t.splitlines():
                if norm(line).startswith("補助事業"):
                    # 数字の途中に空白が入ることがある(「3 ,550」「5 55」)。本省・一括・計の並びで計が最大になることを使う
                    nums = [to_number(x) for x in re.findall(r"[\d,]+", norm(line)[4:])]
                    toks = [to_number(x) for x in re.findall(r"[\d,]{4,}", line.split("業", 1)[1])]
                    cand = [x for x in toks[:4] if x is not None]
                    if cand:
                        out["national_hojo"] = max(cand)
                    break
        if "pref_hojo" not in out:
            for line in t.splitlines():
                parts = line.split()
                name = norm("".join(p for p in parts if not re.fullmatch(r"[\d,]+|-", p)))
                if name in (pref_short + "県", pref_short + "都", pref_short + "府", pref_short):
                    nums = [to_number(p) for p in parts if re.fullmatch(r"[\d,]+|-", p)]
                    if len(nums) >= 3 and "整備局" not in line:
                        out["pref_hojo"] = nums[2] if nums[2] is not None else nums[0]
                    break
    return out
