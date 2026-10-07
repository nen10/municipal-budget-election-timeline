from findnews.municipalities import MasterNotAvailable, has_master, lookup, master
from findnews.prefs import PREFS, short
from findnews.sources import (ELECTION_SOURCES, INDICATOR_SOURCES, INDICATORS, ElectionSource, IndicatorSource,
                              status_for_pref)


def test_adapters_implement_protocols():
    assert set(INDICATOR_SOURCES) == {"soumu_card", "soumu_tokko", "mlit_grants", "mlit_road"}
    for a in INDICATOR_SOURCES.values():
        assert isinstance(a, IndicatorSource)
        assert set(a.indicator_ids) <= set(INDICATORS)
    assert isinstance(ELECTION_SOURCES["09"], ElectionSource)


def test_unsupported_pref_is_reported_not_raised():
    rows = {r["source_id"]: r for r in status_for_pref("47")}
    assert rows["election_47"]["status"].startswith("未対応")
    assert rows["soumu_card"]["status"].startswith("未対応") or has_master("47")


def test_prefs_and_master():
    assert len(PREFS) == 47 and PREFS["09"] == "栃木県" and short("09") == "栃木" and short("01") == "北海道"
    assert len(master("09")) == 25 and lookup("芳賀郡益子町", "09") == "093424"
    try:
        master("99")
        assert False
    except MasterNotAvailable:
        pass


def test_tochigi_adapter_parse_observations(fixtures):
    obs = INDICATOR_SOURCES["soumu_card"].parse(fixtures / "card_2024_sample.xlsx", "09")
    o = {(x.municipality_code, x.indicator_id): x for x in obs}
    x = o[("092151", "card_kokko")]
    assert x.value == 1860530 and (x.period_start, x.period_end, x.decided_date) == ("2024-04-01", "2025-03-31", "2025-03-31")
    t = INDICATOR_SOURCES["soumu_tokko"].parse(fixtures / "tokko_2025_03_sample.pdf", "09")
    tt = {x.municipality_code: x for x in t}
    assert tt["092151"].value == 532034 and tt["092151"].decided_date == "2026-03-17"
    assert tt["094111"].value is None and "町村" in tt["094111"].missing_reason
    r = INDICATOR_SOURCES["mlit_road"].parse(fixtures / "kasho_2026_09_road_sample.pdf", "09")
    rr = {x.municipality_code: x for x in r}
    assert rr["094111"].value == 78000 and rr["094111"].count == 2 and rr["094111"].decided_date_is_proxy
