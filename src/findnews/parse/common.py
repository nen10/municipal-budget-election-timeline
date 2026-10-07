"""パーサー共通の小道具。"""

from __future__ import annotations

import re
import unicodedata

_JP_ERA = {"令和": 2018, "平成": 1988}


def norm(v) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(v)))


def to_number(v):
    """数値または数値文字列を float に。「-」「―」「＊」・空欄は None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = norm(v).replace(",", "")
    if s in ("", "-", "―", "－", "‐", "*", "＊", "…", "x", "X"):
        return None
    s = s.replace("▲", "-").replace("△", "-")
    try:
        return float(s)
    except ValueError:
        return None


def is_placeholder(v) -> bool:
    return norm(v) in ("-", "―", "－", "‐", "*", "＊")


def era_year_to_ad(text: str) -> int | None:
    """「令和6年度」「令和元年度」「平成30年度」→ 西暦年度。"""
    t = norm(text)
    m = re.search(r"(令和|平成)(元|\d+)年", t)
    if not m:
        return None
    n = 1 if m.group(2) == "元" else int(m.group(2))
    return _JP_ERA[m.group(1)] + n


def era_fiscal_year_to_ad(text: str) -> int | None:
    """「…年度」の形のみを対象にした和暦→西暦年度(日付の「令和8年3月」を誤認しない)。"""
    t = norm(text)
    m = re.search(r"(令和|平成)(元|\d+)年度", t)
    if not m:
        return None
    n = 1 if m.group(2) == "元" else int(m.group(2))
    return _JP_ERA[m.group(1)] + n
