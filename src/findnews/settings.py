"""config/settings.yaml の読み込み(無ければ既定値)。"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import config

DEFAULTS = {
    "verification": {
        "period": [2021, 2026],
        "direction_threshold": 0.05,
    }
}


def load(path: str | Path | None = None) -> dict:
    p = Path(path or config.ROOT / "config" / "settings.yaml")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for k, v in (data or {}).items():
        out.setdefault(k, {}).update(v or {})
    out["_path"] = str(p) if p.exists() else "(既定値)"
    return out
