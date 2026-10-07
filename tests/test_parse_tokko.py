from findnews.municipalities import TOCHIGI, stem
from findnews.parse import soumu_tokko as P

STEMS = {stem(n): c for c, n in TOCHIGI.items() if n.endswith("市")}


def test_march_release(fixtures):
    d = P.parse_pdf(fixtures / "tokko_2025_03_sample.pdf")
    assert d["fiscal_year"] == 2025          # 「令和8年3月17日」ではなく「令和7年度」
    assert d["kind"] == "3月"
    assert d["decision_date"] == "2026-03-17"
    found, warnings = P.select_pref(d["records"], "栃木", STEMS)
    assert not warnings
    assert len(found) == 14                  # 栃木県の 14 市(町は個別額なし)
    assert found["092151"].amounts == [532034, 630595]
    assert found["092011"].amounts == [678525, 862614]
    assert "094111" not in found


def test_december_release(fixtures):
    d = P.parse_pdf(fixtures / "tokko_2025_12_sample.pdf")
    assert (d["fiscal_year"], d["kind"], d["decision_date"]) == (2025, "12月", "2025-12-19")
    found, _ = P.select_pref(d["records"], "栃木", STEMS)
    assert found["092151"].amounts == [98561]


def test_older_layout_with_different_page_size(fixtures):
    d = P.parse_pdf(fixtures / "tokko_2021_03_sample.pdf")
    assert d["fiscal_year"] == 2021
    found, _ = P.select_pref(d["records"], "栃木", STEMS)
    assert len(found) == 14
    assert found["092151"].amounts[1] == 555705   # 決算カードの令和3年度 特別交付税と一致
