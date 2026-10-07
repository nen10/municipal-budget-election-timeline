"""パスと取得設定。"""

from __future__ import annotations

import os
from pathlib import Path

# リポジトリルート(src/findnews/config.py から 2 階層上)
ROOT = Path(os.environ.get("FINDNEWS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
MANUAL_DIR = DATA_DIR / "manual"
PROCESSED_DIR = DATA_DIR / "processed"
DEFAULT_DB = Path(os.environ.get("FINDNEWS_DB", PROCESSED_DIR / "findnews.sqlite"))

# 外部サイトへのアクセス設定。User-Agent を明示し、同一ホストへは最低 1 秒の間隔を空ける。
USER_AGENT = os.environ.get(
    "FINDNEWS_USER_AGENT",
    "findnews-research/0.1 (public-data research prototype; +https://github.com/ local)",
)
MIN_INTERVAL_SEC = float(os.environ.get("FINDNEWS_MIN_INTERVAL", "1.0"))
TIMEOUT_SEC = 90
