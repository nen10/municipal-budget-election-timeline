from findnews.fetch import kokkai


def test_match_keywords():
    assert kokkai.match_keywords("あの町には冷や飯を食わせる") == ["冷や飯"]
    assert kokkai.match_keywords("予算を カット した") == ["予算をカット"]  # 空白は無視
    assert kokkai.match_keywords("無関係な発言") == []


def test_target_municipalities_disambiguation():
    assert kokkai.target_municipalities("那須塩原市と那須町の道路") == ["092134", "094072"]
    assert kokkai.target_municipalities("那須烏山市の要望") == ["092151"]


def test_parse_fixture(fixtures):
    parsed = kokkai.parse([fixtures / "kokkai_sample.json"])
    assert len(parsed) == 2
    assert all(p["matched"] == ["予算を取ってきた"] for p in parsed)
    assert all(p["url"].startswith("https://kokkai.ndl.go.jp/") for p in parsed)
