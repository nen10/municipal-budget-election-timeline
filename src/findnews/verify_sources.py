"""手作業で見つけた出典候補の検証(取得して原文に引用が含まれるかを確かめる)。

- robots.txt を urllib.robotparser で確認し、拒否されているパスは取得しない(「robots.txt で拒否」と記録)。
- 引用(quote)が、取得したページ本文(HTML のテキスト、PDF は pdfplumber の抽出テキスト)に、空白を除いた形で
  含まれる場合だけ「検証済み」とする。含まれない候補は登録しない(推測で埋めない)。
- 日付は、ページ本文に「M月D日」または「M/D」の形で現れるかを併せて記録する。
"""

from __future__ import annotations

import re
import urllib.robotparser
from functools import lru_cache
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import config, http
from .municipalities import normalize_name


@lru_cache(maxsize=None)
def _robots(host_url: str):
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = http.get(host_url + "/robots.txt")
        rp.parse(resp.text.splitlines() if "html" not in resp.headers.get("content-type", "") else [])
    except Exception:  # noqa: BLE001  (404 等は制限なし)
        rp.parse([])
    return rp


def allowed(url: str) -> bool:
    u = urlparse(url)
    return _robots(f"{u.scheme}://{u.netloc}").can_fetch(config.USER_AGENT, url)


def page_text(url: str, source: str = "municipal_pages") -> tuple[str, str]:
    """(本文テキスト, 保存したファイル名)。"""
    import hashlib
    ext = ".pdf" if url.lower().split("?")[0].endswith(".pdf") else ".html"
    fn = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
    rf = http.download(url, source, fn)
    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(rf.path) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages), fn
    raw = rf.path.read_bytes()
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return BeautifulSoup(raw.decode(enc), "html.parser").get_text("\n"), fn
        except UnicodeDecodeError:
            continue
    return "", fn


def check(url: str, quote: str | None, date: str | None = None) -> dict:
    if not allowed(url):
        return {"status": "robots.txt で拒否", "verified": False}
    try:
        text, fn = page_text(url)
    except Exception as e:  # noqa: BLE001
        return {"status": f"取得失敗 {e!r}"[:200], "verified": False}
    t = normalize_name(text)
    ok = bool(quote) and normalize_name(quote) in t
    date_ok = None
    if date:
        y, m, d = (int(x) for x in date.split("-"))
        date_ok = bool(re.search(rf"(?<!\d){m}月{d}日|(?<!\d){m}/{d}(?!\d)", t))
    return {"status": "検証済み" if ok else "引用が本文に見つからない", "verified": ok, "date_found": date_ok,
            "file": fn, "text": text}
