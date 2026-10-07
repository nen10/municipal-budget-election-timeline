"""礼儀正しい HTTP 取得と生データの保存。

- 同一ホストへのリクエスト間隔を MIN_INTERVAL_SEC 以上に保つ(既定 1 秒)。
- User-Agent を明示する。
- 取得したファイルは data/raw/<source>/ に保存し、manifest.jsonl に
  URL・取得日時・SHA-256 を追記する(根拠への遡及用)。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import config

_last_access: dict[str, float] = {}
_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = config.USER_AGENT
    return _session


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    last = _last_access.get(host)
    if last is not None:
        wait = config.MIN_INTERVAL_SEC - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_access[host] = time.monotonic()


def get(url: str, **kwargs) -> requests.Response:
    """レート制限つき GET。HTTP エラーは例外にする。"""
    _throttle(url)
    kwargs.setdefault("timeout", config.TIMEOUT_SEC)
    resp = session().get(url, **kwargs)
    resp.raise_for_status()
    return resp


def get_html(url: str) -> str:
    resp = get(url)
    # 官公庁サイトは Shift_JIS と UTF-8 が混在するため推定に任せる
    if resp.encoding is None or resp.encoding.lower() in ("iso-8859-1", "ascii"):
        resp.encoding = resp.apparent_encoding
    return resp.text


@dataclass
class RawFile:
    url: str
    path: Path
    retrieved_at: str
    sha256: str


def raw_dir(source: str) -> Path:
    d = config.RAW_DIR / source
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(url: str, source: str, filename: str, force: bool = False) -> RawFile:
    """URL を data/raw/<source>/<filename> に保存する。既存ファイルは force=False なら再取得しない。"""
    d = raw_dir(source)
    path = d / filename
    manifest = d / "manifest.jsonl"
    if path.exists() and not force:
        meta = _lookup_manifest(manifest, filename)
        if meta:
            return RawFile(url=meta["url"], path=path, retrieved_at=meta["retrieved_at"], sha256=meta["sha256"])
    resp = get(url)
    content = resp.content
    path.write_bytes(content)
    rf = RawFile(url=url, path=path, retrieved_at=now_iso(), sha256=hashlib.sha256(content).hexdigest())
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"file": filename, "url": url, "retrieved_at": rf.retrieved_at, "sha256": rf.sha256},
                           ensure_ascii=False) + "\n")
    return rf


def _lookup_manifest(manifest: Path, filename: str) -> dict | None:
    if not manifest.exists():
        return None
    found = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("file") == filename:
            found = rec
    return found


def manifest_meta(path: Path) -> dict | None:
    """保存済みファイルの取得メタデータ(url, retrieved_at)を返す。"""
    return _lookup_manifest(path.parent / "manifest.jsonl", path.name)
