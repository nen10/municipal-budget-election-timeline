from findnews.parse import soumu_card as P


def test_card_fixture_values(fixtures):
    recs = P.parse_workbook(fixtures / "card_2024_sample.xlsx")
    v = {(r.code, r.item): r.value for r in recs}
    assert {r.fiscal_year for r in recs} == {2024}
    # 那須烏山市 令和6年度
    assert v[("092151", "国庫支出金")] == 1860530
    assert v[("092151", "特別交付税")] == 611104
    assert v[("092151", "普通建設事業費_うち補助")] == 314858
    assert v[("092151", "土木費")] == 1172371
    assert v[("092151", "財政力指数")] == 0.45
    assert v[("092151", "住民基本台帳人口")] == 23482
    # 那珂川町 令和6年度
    assert v[("094111", "国庫支出金")] == 1135992
    assert v[("094111", "都道府県支出金")] == 612580
    assert v[("094111", "財政力指数")] == 0.37


def test_card_code_matches_master(fixtures):
    from findnews.municipalities import TOCHIGI
    recs = P.parse_workbook(fixtures / "card_2024_sample.xlsx")
    assert {(r.code, r.name) for r in recs} == {("092151", "那須烏山市"), ("094111", "那珂川町")}
    assert all(TOCHIGI[r.code] == r.name for r in recs)


def test_dash_is_null_and_header_occurrence_is_skipped():
    grid = [
        [None, "普通建設事業費", None, "充当一般財源等", None, 999],   # 見出し(右に文字列)→ 読み飛ばす
        [None, "災害復旧事業費", None, "-", None, None],
        [None, "普通建設事業費", None, None, 1234, None],
    ]
    assert P.find_labeled_value(grid, "普通建設事業費") == 1234
    assert P.find_labeled_value(grid, "災害復旧事業費") is None
