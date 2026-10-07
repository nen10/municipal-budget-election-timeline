"""静的サイト: 全ページが生成され、サイト内リンクが切れていないこと(DESIGN.md 第15節)。"""

import re
from html.parser import HTMLParser
from pathlib import Path

from click.testing import CliRunner

from findnews.cli import main

from .test_end_to_end import BANNED, _load


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        for k in ("href", "src"):
            if a.get(k):
                self.links.append(a[k])


def _parse(path: Path) -> _Links:
    p = _Links()
    p.feed(path.read_text(encoding="utf-8"))
    return p


def test_site_build_all_pages_and_no_broken_links(tmp_path, fixtures):
    from findnews import db
    dbp = tmp_path / "s.sqlite"
    c = db.connect(dbp)
    db.init_db(c)
    db.ensure_municipalities(c)
    _load(c, fixtures)
    c.close()
    out = tmp_path / "site"
    r = CliRunner().invoke(main, ["--db", str(dbp), "site", "build", "--pref", "09", "--out", str(out)])
    assert r.exit_code == 0, r.output
    for f in ["index.html", "events.html", "sources.html", "about.html", "static/style.css", "static/site.js",
              "matrix/shugiin_20260208.html"]:
        assert (out / f).exists(), f
    munis = sorted((out / "municipalities").glob("*.html"))
    assert len(munis) == 25
    pages = sorted(out.rglob("*.html"))
    parsed = {p: _parse(p) for p in pages}
    broken = []
    for page, lp in parsed.items():
        for link in lp.links:
            if re.match(r"^(https?:|mailto:)", link):
                continue                                    # 出典の外部 URL は検査しない(表示のみ)
            target, _, frag = link.partition("#")
            dest = (page.parent / target).resolve() if target else page
            if not dest.exists():
                broken.append((page.relative_to(out), link))
            elif frag and dest.suffix == ".html" and frag not in parsed.get(dest, _parse(dest)).ids:
                broken.append((page.relative_to(out), link))
    assert not broken, broken[:10]
    # 外部 CDN を読み込まない
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert not re.search(r'<(script|link)[^>]+(src|href)="https?://', text), page
        for b in BANNED:
            assert b not in text, (page, b)
    m = (out / "municipalities" / "092151.html").read_text(encoding="utf-8")
    assert "<svg" in m and "減少" in m and "川俣純子" in m and "大幅にカット" in m
    assert 'scope="col"' in m and "<caption>" in m and 'id="requests"' in m
    # グラフの縦線は国政選挙の投票日だけ(DESIGN.md 13.5)。支持表明や報道は SVG に描かない
    for svg in re.findall(r"<svg.*?</svg>", m, re.S):
        assert "首長の支持表明" not in svg and "議員発言" not in svg and "役職就任" not in svg
