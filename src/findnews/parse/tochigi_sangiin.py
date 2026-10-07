"""栃木県選管 参院選(栃木県選挙区)の開票結果のパーサー。

- 令和4年(2022-07-10): 「候補者別開票区別得票数調」Excel(file/BS_KAIHYO_K_30.xls)
- 令和7年(2025-07-20): 確定値は HTML のみ(file/AS_KAIHYO.html、「開票速報(確定)」)
栃木県選挙区は改選定数 1。当選者は県計の最多得票者とする(資料に当落欄はない)。
党派は資料の「党派名」欄。公認・推薦の別はこの資料からは分からない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup

from .common import norm, to_number

_TOTAL_ROWS = ("市部計", "郡部計", "県計")


@dataclass
class SangiinResult:
    date: str | None
    title: str
    candidates: list[tuple[str, str]]          # (氏名, 党派)
    rows: list[tuple[str, str, float]] = field(default_factory=list)   # (開票区, 氏名, 得票)
    totals: dict[str, float] = field(default_factory=dict)

    @property
    def winner(self) -> str | None:
        return max(self.totals, key=self.totals.get) if self.totals else None


def _date(text: str) -> str | None:
    m = re.search(r"令和(\d+)年(\d+)月(\d+)日", norm(text))
    return f"{2018 + int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def parse_xls(path: str | Path) -> SangiinResult:
    from .tochigi_election import read_xls
    g = read_xls(path)
    title = norm(g[0][1]) if g and len(g[0]) > 1 else ""
    hdr = next(i for i, r in enumerate(g) if norm(r[1]) == "区分")
    cols = {c: str(g[hdr][c]).replace("　", " ").strip() for c in range(2, len(g[hdr]))
            if norm(g[hdr][c]) and "票" not in norm(g[hdr][c])}
    party = {c: norm(g[hdr + 1][c]) for c in cols}
    cols = {c: v for c, v in cols.items() if party[c]}    # 党派欄のない列(確定時刻など)は候補ではない
    res = SangiinResult(_date(title), title, [(cols[c], party[c]) for c in sorted(cols)])
    for r in g[hdr + 2:]:
        name = norm(r[1])
        if not name or name in ("開票区名",):
            continue
        vals = {cols[c]: to_number(r[c]) for c in cols}
        if name == "県計":
            res.totals = {k: v for k, v in vals.items() if v is not None}
        elif name in _TOTAL_ROWS or name.endswith("郡計"):
            continue
        elif any(v is not None for v in vals.values()):
            res.rows += [(name, k, v) for k, v in vals.items()]
    return res


def parse_html(path: str | Path) -> SangiinResult:
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "cp932"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    toks = [t.strip() for t in BeautifulSoup(text, "html.parser").get_text("\n").split("\n") if t.strip()]
    title_date = next((t for t in toks if re.search(r"【令和\d+年\d+月\d+日】", t)), "")
    i = toks.index("候補者氏名")
    j = toks.index("党派名")
    names = [t.replace("　", " ") for t in toks[i + 1:j]]
    parties = [norm(t) for t in toks[j + 1:j + 1 + len(names)]]
    res = SangiinResult(_date(title_date), "参議院栃木県選出議員選挙 開票結果(確定) " + title_date, list(zip(names, parties)))
    k = toks.index("市町名") + 1 + len(names)          # 「得票数」× N の後
    n = len(names)
    while k < len(toks):
        name = norm(toks[k])
        nxt = k + 1
        if nxt < len(toks) and toks[nxt] == "*":
            nxt += 1
        vals = [to_number(t) for t in toks[nxt:nxt + n]]
        if len(vals) == n and all(v is not None for v in vals) and not re.fullmatch(r"[\d.,]+", name):
            if name == "県計":
                res.totals = dict(zip(names, vals))
            elif name not in _TOTAL_ROWS and not name.endswith("郡計"):
                res.rows += [(name, c, v) for c, v in zip(names, vals)]
            k = nxt + n
        else:
            k += 1
    if not res.totals and res.rows:   # 県計の行がない場合は市町の合計(資料に県計があればそちらを使う)
        for _, c, v in res.rows:
            res.totals[c] = res.totals.get(c, 0) + v
    return res
