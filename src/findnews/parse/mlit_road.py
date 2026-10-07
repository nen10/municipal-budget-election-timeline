"""国交省「事業実施箇所」都道府県別 PDF のうち **道路局** の箇所表のパーサー。

PDF 先頭の「栃 木 県 / 道 路 局」表紙から次の局の表紙(「水管理・国土保全局」など)までが道路局の箇所表。
表の種類(ヘッダで判別、単位はいずれも百万円):
  A. 工種 | 路線名 | 市町村名 | 事業名(箇所) | 事業費 | 備考   … 直轄事業・補助事業(改築、土砂災害対策など)
     事業主体の列がない。直轄は国。補助のうち路線名が「(市)」「(町)」「(村)」の市町村道は道路管理者である
     当該市町を事業主体とみなし(basis='route_type')、それ以外(国道・県道・都市計画道路など)は事業主体不明
     (attribution='unknown')として自治体に帰属させない。
  B. 工種 | 事業主体 | 事業名(箇所) | 事業費 | 備考      … 無電柱化、通学路緊急対策、道路メンテナンス事業など
  C. 工種 | 市町村名 | 事業名(箇所) | 事業主体 | 事業費 | 備考 … 踏切道改良計画事業
  D. 地域再生計画の名称 | 事業箇所(市町村名) | 国費 | 備考   … 地域未来交付金/地方創生道整備推進交付金(市町村道分)
     事業箇所の市町が 1 つなら当該市町、複数なら共同(按分不能)。
  E. 事業主体名 | 市町村名 | 路線名 | 構造物名 …           … 道路メンテナンス事業の別紙(構造物一覧)。金額がないため読み飛ばす。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from ..municipalities import lookup, normalize_name
from .common import norm, to_number

_MUNI_ROUTE = re.compile(r"[（(](市|町|村)[）)]")


@dataclass
class RoadRow:
    table: str                 # A_direct / A_subsidy / B / C / D
    work_type: str             # 工種(D は「地域再生計画」)
    item_name: str             # 事業名(箇所)または計画名
    route: str | None
    location: str | None       # 市町村名(原文)
    entity: str | None         # 事業主体(原文または判定結果)
    municipality_code: str | None
    attribution: str           # sole / joint / prefecture / national / unknown
    basis: str                 # entity_column / route_type / location_single / location_multiple / direct / none
    amount_million_yen: float | None
    page: int
    location_codes: list[str] = field(default_factory=list)


def _is_cover(text: str) -> bool:
    t = norm(text)
    return len(t) <= 20 and t.endswith(("局", "部", "庁"))


def road_pages(pdf) -> list[int]:
    """道路局セクションのページ番号(0 始まり)。"""
    start = None
    for i, p in enumerate(pdf.pages):
        t = p.extract_text() or ""
        if _is_cover(t):
            if "道路局" in norm(t):
                start = i
            elif start is not None:
                return list(range(start + 1, i))
    return list(range(start + 1, len(pdf.pages))) if start is not None else []


def _codes(text: str) -> list[str]:
    parts = re.split(r"[、,～~]", norm(text))
    return sorted({c for c in (lookup(x) for x in parts if x) if c})


def _entity_row(table, work, item, entity, location, amount, page, route=None) -> RoadRow:
    ent = normalize_name(entity)
    code = lookup(ent)
    if code:
        attr = "sole"
    elif ent.endswith(("県", "都", "道", "府")):
        attr = "prefecture"
    elif ent in ("国", "国土交通省"):
        attr = "national"
    else:
        attr = "unknown"
    return RoadRow(table, norm(work), norm(item), route, location, ent, code, attr, "entity_column",
                   to_number(amount), page)


def parse_page(page, page_no: int) -> list[RoadRow]:
    text = norm(page.extract_text() or "")
    direct = "種別:直轄事業" in text
    out: list[RoadRow] = []
    for tb in page.extract_tables():
        hdr_idx = next((i for i, r in enumerate(tb) if r and norm(r[0]) in ("工種", "地域再生計画の名称", "事業主体名")), None)
        if hdr_idx is None:
            continue
        hdr = [norm(c) for c in tb[hdr_idx]]
        for row in tb[hdr_idx + 1:]:
            cells = [c if c is not None else "" for c in row]
            if not norm(cells[0]) or norm(cells[0]) in ("直轄事業", "補助事業") or norm(cells[0]).startswith(("小計", "合計")):
                continue
            if hdr[0] == "事業主体名":
                break  # E: 構造物一覧
            if hdr[0] == "地域再生計画の名称":
                codes = _codes(cells[1])
                attr = "sole" if len(codes) == 1 else ("joint" if codes else "unknown")
                out.append(RoadRow("D", "地域再生計画", norm(cells[0]), None, norm(cells[1]), None,
                                   codes[0] if len(codes) == 1 else None, attr,
                                   "location_single" if len(codes) == 1 else "location_multiple",
                                   to_number(cells[2]), page_no, codes))
            elif hdr[1] == "路線名":
                route, loc = norm(cells[1]), norm(cells[2])
                codes = _codes(loc)
                if direct:
                    out.append(RoadRow("A_direct", norm(cells[0]), norm(cells[3]), route, loc, "国", None, "national",
                                       "direct", to_number(cells[4]), page_no, codes))
                elif _MUNI_ROUTE.search(route) and len(codes) == 1:
                    out.append(RoadRow("A_subsidy", norm(cells[0]), norm(cells[3]), route, loc, loc, codes[0], "sole",
                                       "route_type", to_number(cells[4]), page_no, codes))
                else:
                    out.append(RoadRow("A_subsidy", norm(cells[0]), norm(cells[3]), route, loc, None, None, "unknown",
                                       "none", to_number(cells[4]), page_no, codes))
            elif hdr[1] == "事業主体":
                out.append(_entity_row("B", cells[0], cells[2], cells[1], None, cells[3], page_no))
            elif hdr[1] == "市町村名" and len(hdr) > 3 and hdr[3] == "事業主体":
                r = _entity_row("C", cells[0], cells[2], cells[3], norm(cells[1]), cells[4], page_no)
                r.location_codes = _codes(cells[1])
                out.append(r)
    return out


def parse_pdf(path: str | Path) -> list[RoadRow]:
    rows: list[RoadRow] = []
    with pdfplumber.open(path) as pdf:
        for i in road_pages(pdf):
            rows.extend(parse_page(pdf.pages[i], i + 1))
    return rows
