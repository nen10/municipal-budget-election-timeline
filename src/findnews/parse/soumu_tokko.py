"""総務省 報道発表「特別交付税交付額の決定」(12 月分・3 月分)PDF のパーサー。

PDF の構造(令和2〜7年度で確認):
  - 都市分(大都市を含む)は 1 ページに最大 3 ブロック。各ブロックは
    [都道府県名(縦書き 1 文字ずつ) | 都市名(「市」を省略) | 金額列] で、都道府県の区切りに「計」行がある。
  - 3 月分は金額列が 2 つ(3 月交付額・交付総額)、12 月分は 1 つ(12 月交付額)。
  - 町村分は都道府県ごとの合計しか載っていない。したがって **町(那珂川町など)の個別交付額は
    この資料からは得られない**(決算カードの「特別交付税」決算額で代替する)。

方法: pdfplumber の単語座標を使い、縦書きラベル列(ヘッダの「府」の x 座標)でブロックを分け、
同じ y 座標の単語を行にまとめ、「計」行で都道府県セグメントを区切る。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .common import era_fiscal_year_to_ad, norm, to_number

_NUM = re.compile(r"^[▲△-]?[\d,]+$")


@dataclass
class TokkoRecord:
    pref: str           # 縦書きラベルから復元した都道府県名(例: 栃木)
    city_stem: str      # 「市」を除いた都市名(例: 那須烏山)
    amounts: list[float]
    page: int


def _rows(words, tol=1.5):
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _header_limit(words) -> float:
    """最初の金額(カンマ区切りの数値)の y 座標。これより上をヘッダとみなす(ページ寸法が年度で異なるため相対判定)。"""
    tops = [w["top"] for w in words if re.fullmatch(r"[\d]{1,3}(,\d{3})+", w["text"])]
    return min(tops) - 1 if tops else 0.0


def _label_columns(words) -> list[tuple[float, float, float]]:
    """ヘッダの縦書き「府」(都道府県名ラベル列)の (x0, x1, top) を返す。"""
    limit = _header_limit(words)
    cands = [w for w in words if w["text"] == "府" and w["top"] < limit]
    return sorted({(round(w["x0"], 1), round(w["x1"], 1), w["top"]) for w in cands})


def parse_page(page, page_no: int, block_width: float | None = None) -> list[TokkoRecord]:
    words = page.extract_words()
    labels = _label_columns(words)
    if not labels:
        return []
    xs = [l[0] for l in labels]
    if block_width is None:
        block_width = min((b - a for a, b in zip(xs, xs[1:])), default=180.0)
    header_bottom = _header_limit(words) - 1
    out: list[TokkoRecord] = []
    for i, (lx0, lx1, _) in enumerate(labels):
        right = min(xs[i + 1] if i + 1 < len(xs) else 1e9, lx0 + block_width)
        bw = [w for w in words if lx0 - 1 <= w["x0"] < right - 1 and w["top"] > header_bottom + 1]
        seg_label: list[str] = []
        seg_rows: list[tuple[str, list[float]]] = []

        def flush():
            pref = "".join(seg_label)
            for name, nums in seg_rows:
                out.append(TokkoRecord(pref, name, nums, page_no))

        for row in _rows(bw):
            label = [w["text"] for w in row if w["x0"] <= lx1 + 1]
            rest = [w for w in row if w["x0"] > lx1 + 1]
            nums = [to_number(w["text"]) for w in rest if _NUM.match(w["text"])]
            name = norm("".join(w["text"] for w in rest if not _NUM.match(w["text"])))
            seg_label.extend(label)
            if name == "計" or name.startswith("全国計"):
                flush()
                seg_label, seg_rows = [], []
                continue
            if name and nums:
                seg_rows.append((name, nums))
        flush()
    return out


def parse_pdf(path: str | Path) -> dict:
    """戻り値: {fiscal_year, kind('12月'|'3月'), decision_date, records}"""
    with pdfplumber.open(path) as pdf:
        first = pdf.pages[0].extract_text() or ""
        fy = era_fiscal_year_to_ad(first)
        kind = "12月" if re.search(r"12\s*月交付額", norm(first)) else "3月"
        decision_date = _parse_date(first)
        # ブロック幅は 3 ブロックあるページの間隔の中央値
        spacings = []
        for p in pdf.pages:
            xs = [l[0] for l in _label_columns(p.extract_words())]
            spacings += [b - a for a, b in zip(xs, xs[1:])]
        bw = statistics.median(spacings) if spacings else 180.0
        recs: list[TokkoRecord] = []
        for i, p in enumerate(pdf.pages, 1):
            t = norm(p.extract_text() or "")
            if "都市分" not in t:
                continue
            recs.extend(parse_page(p, i, bw))
    return {"fiscal_year": fy, "kind": kind, "decision_date": decision_date, "records": recs}


def _parse_date(text: str) -> str | None:
    m = re.search(r"(令和|平成)(元|\d+)年\s*(\d+)月\s*(\d+)日", norm(text))
    if not m:
        return None
    base = 2018 if m.group(1) == "令和" else 1988
    y = base + (1 if m.group(2) == "元" else int(m.group(2)))
    return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"


def select_pref(records: list[TokkoRecord], pref_label: str, stems: dict[str, str]) -> tuple[dict[str, TokkoRecord], list[str]]:
    """pref_label(例: 栃木)のセグメントから stems(語幹→コード)に一致する都市を取り出す。

    戻り値: ({code: record}, 警告メッセージ)
    """
    found: dict[str, TokkoRecord] = {}
    warnings: list[str] = []
    for r in records:
        if r.pref != pref_label:
            continue
        code = stems.get(r.city_stem)
        if code is None:
            warnings.append(f"未知の都市名 {r.city_stem}(p.{r.page})")
            continue
        if code in found:
            warnings.append(f"重複 {r.city_stem}")
        found[code] = r
    return found, warnings
