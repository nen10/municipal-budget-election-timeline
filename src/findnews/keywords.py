"""発言検出のキーワード(設計書 5.3)とその由来。定義は config/keywords.yaml。

由来事例(origin_case_id)のあるキーワードは、その事例に関わる議員・自治体の発言への一致を
「由来事例のため独立検証にならない」として扱い、スコアの発言一致には数えない(DESIGN.md 12 節 8 項)。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config


def load(path: str | Path | None = None) -> list[dict]:
    p = Path(path or config.ROOT / "config" / "keywords.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = []
    for k in data.get("keywords", []):
        out.append({"keyword": str(k["keyword"]), "origin": k.get("origin") or "",
                    "origin_case_id": k.get("origin_case_id") or "", "origin_source_url": k.get("origin_source_url") or ""})
    return out


KEYWORDS = load()
STATEMENT_KEYWORDS = [k["keyword"] for k in KEYWORDS]
ORIGIN = {k["keyword"]: k for k in KEYWORDS}
