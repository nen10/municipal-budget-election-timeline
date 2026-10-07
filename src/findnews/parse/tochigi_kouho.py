"""栃木県選管「衆議院小選挙区選出議員選挙 候補者届出状況公表票」PDF(選挙区ごと)のパーサー。

列: 届出受理番号 | 政党届出/本人届出/推薦届出 | 届出政党(所属団体) | ふりがな・候補者氏名(戸籍名) | 本籍 | 住所 |
    生年月日(満年齢) | 性別 | 新前元 | 職業 | 重複立候補の有無 | ウェブサイト
- 「政党届出」は届出政党の公認(公職選挙法 86 条 1 項の政党届出)。「本人届出」「推薦届出」は政党の公認ではない。
- 政党の「推薦」(他党推薦など)はこの公表票に載らないため、別の出典がない限り未収集とする。
- 重複立候補「無」の候補は比例代表に名簿登載されていないため、選挙区で落選すれば比例復活はあり得ない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .common import norm


@dataclass
class Candidate:
    district: int | None
    name: str             # 通称(投票用紙・開票結果の表記)
    legal_name: str | None
    kana: str | None
    filing_type: str      # 政党届出 / 本人届出 / 推薦届出
    party: str            # 届出政党(所属団体)。無所属は「無所属」
    incumbency: str | None  # 新 / 前 / 元
    dual: bool | None     # 重複立候補
    occupation: str | None


def _col(hdr: list[str], *keys) -> int | None:
    for i, h in enumerate(hdr):
        if all(k in h for k in keys):
            return i
    return None


def _split_name(cell: str) -> tuple[str | None, str, str | None]:
    lines = [l.strip() for l in (cell or "").split("\n") if l.strip()]
    legal = None
    if lines and re.fullmatch(r"[（(].+[）)]", lines[-1]):
        legal = lines.pop()[1:-1].strip()
    kana = None
    if len(lines) >= 2 and re.fullmatch(r"[ぁ-ゖー\s・]+", lines[0]):
        kana = lines.pop(0)
    name = " ".join(lines)
    return kana, name, legal


def parse_pdf(path: str | Path) -> list[Candidate]:
    out = []
    with pdfplumber.open(path) as pdf:
        text = norm(pdf.pages[0].extract_text() or "")
        m = re.search(r"第(\d+|[１-９])区", text)
        district = int(m.group(1).translate(str.maketrans("１２３４５６７８９", "123456789"))) if m else None
        for p in pdf.pages:
            for tb in p.extract_tables():
                hdr = [norm(c) for c in tb[0]]
                ci_name = _col(hdr, "候補者氏名")
                ci_type = _col(hdr, "本人")
                ci_party = _col(hdr, "所属団体")
                ci_inc = _col(hdr, "新前元")
                ci_dual = _col(hdr, "重複")
                ci_job = _col(hdr, "職業")
                if ci_name is None:
                    continue
                for row in tb[1:]:
                    if not row or not row[ci_name] or not norm(row[0] or "").isdigit():
                        continue
                    kana, name, legal = _split_name(row[ci_name])
                    party = norm(row[ci_party]) if ci_party is not None else ""
                    party = "無所属" if party in ("（無所属）", "(無所属)", "無所属") else party
                    dual = norm(row[ci_dual]) if ci_dual is not None else ""
                    out.append(Candidate(district, name.replace("　", " "), legal, kana,
                                         norm(row[ci_type]) if ci_type is not None else "", party,
                                         norm(row[ci_inc]) if ci_inc is not None else None,
                                         True if dual == "有" else (False if dual in ("無", "") else None),
                                         norm(row[ci_job]) if ci_job is not None else None))
    return out


def nomination_label(c: Candidate) -> str:
    """表示用の公認ラベル(例: 自由民主党公認 / 無所属)。他党の推薦はこの資料からは分からない。"""
    if c.filing_type == "政党届出" and c.party:
        return f"{c.party}公認"
    return c.party or "無所属"
