"""第17節の基盤層: パーサー(フィクスチャ)、法人番号 → 団体コード、指標 6〜10 の生成、17.3 の突き合わせ、
第16節の全体系列のパーサー。

フィクスチャの出典(すべて data/raw に取得した公開ファイルの一部を切り出したもの):
- estat_04_fy2020_698_sample.csv / estat_04_fy2024_638_sample.csv: e-Stat 地方財政状況調査 市町村分 歳入内訳
  (statInfId 000032188698 / 000040375638)。見出し行 + 那須烏山市・那珂川町の行。FY2020 は団体コード等が 0 埋めなし
- estat_10_fy2020_710_sample.csv / estat_10_fy2024_650_sample.csv: 同 歳出内訳及び財源内訳(表 10)
  (statInfId 000032188710 / 000040375650)
- kofu_fy2024_road_sample.xlsx: 国交省 補助金等に関する情報開示 令和6年度上半期 道路局(001889204.xlsx)の見出しと 4 行
- futsu_decision_fy2024_sample.xlsx: 総務省 令和6年度市町村別普通交付税決定額(000958553.xlsx)の見出しと栃木県の行
- futsu_juyo_fy2024_sample.xlsx: 総務省 令和6年度 市町村別(費目別)基準財政需要額<再算定>(000983383.xlsx)の見出しと 2 団体
- mlit_overview_shasoukou_2026_p3.pdf / mlit_overview_road_2026_p3p5.pdf: 国交省 令和8年度 配分概要の該当ページ
- tokko_2025_12_prefs_p2.pdf / tokko_2025_03_towns_p8.pdf: 総務省 特別交付税 報道資料の都道府県別表のページ
"""

import pytest

from findnews import baseline, config
from findnews.fetch import estat_chizai, mlit_kofu
from findnews.fetch.mlit_kofu import code_from_corporate_number, parse_workbook
from findnews.fetch.soumu_futsu import parse_decision, parse_juyo
from findnews.parse.estat_chizai import iter_cells
from findnews.parse.mlit_overview import road_totals, shasoukou_national
from findnews.parse.soumu_tokko import parse_pref_totals_dec, parse_town_totals_march
from findnews.site.views import reconciliation

ESTAT = ["estat_04_fy2020_698_sample.csv", "estat_04_fy2024_638_sample.csv",
         "estat_10_fy2020_710_sample.csv", "estat_10_fy2024_650_sample.csv"]


def test_code_from_corporate_number():
    assert code_from_corporate_number("7000020092151") == "092151"          # 那須烏山市
    assert code_from_corporate_number(7000020092151.0) == "092151"          # Excel が数値で返す場合
    assert code_from_corporate_number("7000020010006") == "010006"          # 北海道(都道府県)
    assert code_from_corporate_number("3010405004914") is None              # 独立行政法人(地方公共団体ではない)
    assert code_from_corporate_number("000020092151") is None               # 12 桁
    assert code_from_corporate_number(None) is None


def test_parse_kofu_workbook_keeps_negative_rows(fixtures):
    rows = parse_workbook(fixtures / "kofu_fy2024_road_sample.xlsx")
    assert len(rows) == 4
    assert all(r["bureau"] == "道路局" for r in rows)
    nasu = next(r for r in rows if r["corporate_number"] == "7000020092151")
    assert nasu["recipient_name"] == "那須烏山市" and nasu["amount_yen"] == 6264000
    assert nasu["decided_date"] == "2024-06-14" and nasu["subsidy_name"] == "道路更新防災等対策事業費補助"
    neg = [r for r in rows if r["amount_yen"] < 0]
    assert neg and code_from_corporate_number(neg[0]["corporate_number"]) == "281000"   # 減額の交付決定も行として残す
    agency = [r for r in rows if code_from_corporate_number(r["corporate_number"]) is None]
    assert len(agency) == 1


@pytest.mark.parametrize("name,fy", [(ESTAT[0], 2020), (ESTAT[1], 2024)])
def test_estat_04_parser_pads_codes_and_switches_headers(fixtures, name, fy):
    cells = list(iter_cells(fixtures / name))
    assert {c.fiscal_year for c in cells} == {fy}
    assert {c.code for c in cells} == {"092151", "094111"}                 # FY2020 の「92151」も 6 桁に
    assert {c.table_no for c in cells} == {"04"} and {c.row_no for c in cells} == {"01", "02"}
    v = {(c.code, c.row_no, c.item_name): c.value for c in cells}
    # 行番号 02 は見出し行が切り替わり、列の意味が変わる(01 の「国庫支出金」の列位置に 02 では別の項目)
    assert ("092151", "02", "都道府県支出金") in v
    expect = {2020: (5691825, 929998), 2024: (1860530, 1017797)}[fy]
    assert v[("092151", "01", "国庫支出金")] == expect[0]
    assert v[("092151", "02", "都道府県支出金")] == expect[1]


def _load_estat(conn, fixtures):
    for n in ESTAT:
        estat_chizai.load_file(conn, fixtures / n, {"url": f"https://www.e-stat.go.jp/test/{n}", "retrieved_at": "2026-10-08"})


def _load_kofu(conn, fixtures):
    for r in parse_workbook(fixtures / "kofu_fy2024_road_sample.xlsx"):
        conn.execute("""INSERT INTO grant_decisions(fiscal_year, half, bureau, sheet, project_name, recipient_name,
                        corporate_number, municipality_code, amount_yen, account, subsidy_name, decided_date, source_url)
                        VALUES (2024,'上半期',?,?,?,?,?,?,?,?,?,?,'https://www.mlit.go.jp/page/content/001889204.xlsx')""",
                     (r["bureau"], r["sheet"], r["project_name"], r["recipient_name"], r["corporate_number"],
                      code_from_corporate_number(r["corporate_number"]), r["amount_yen"], r["account"],
                      r["subsidy_name"], r["decided_date"]))
    conn.commit()


def test_baseline_build_indicators_6_to_10(conn, fixtures, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path / "raw")      # 実データの PDF を読まない
    _load_estat(conn, fixtures)
    _load_kofu(conn, fixtures)
    counts = baseline.build(conn, "09")
    assert counts["e_road_kokko"] == 25 * 2                        # 25 市町 × 2 年度(行のない団体は未取得として残す)

    def obs(ind, code, fy):
        return conn.execute("SELECT value, missing_reason, decided_date FROM observations WHERE indicator_id=? AND "
                            "municipality_code=? AND period_start=?", (ind, code, f"{fy}-04-01")).fetchone()
    # 指標 7: 道路橋りょう費に充当した国庫支出金(表 10、行「国庫支出金」、列「土木費・道路橋りょう費」)
    assert obs("e_road_kokko", "092151", 2020)[0] == 64485
    assert obs("e_road_kokko", "092151", 2024)[0] == 117483
    assert obs("e_road_kokko", "094111", 2020)[0] == 125867
    assert obs("e_road_kokko", "094111", 2024)[0] == 77600
    assert obs("e_kokko", "092151", 2024)[0] == 1860530 and obs("e_pref", "092151", 2024)[0] == 1017797
    miss = obs("e_kokko", "092011", 2024)                          # フィクスチャにない団体は推定せず未取得
    assert miss[0] is None and "行なし" in miss[1]
    # 指標 10: 交付決定(千円、年度合計)。decided_date は年度内最後の交付決定日
    v, _, d = obs("kofu_road", "092151", 2024)
    assert v == 6264 and d == "2024-06-14"
    assert obs("kofu_total", "092011", 2024)[0] == 0                # 公表一覧に行がなければ 0(この年度の一覧は取得済み)
    # 全体: 都道府県計 = マスタの栃木県内の合計
    t = conn.execute("SELECT value FROM totals WHERE indicator_id='e_road_kokko' AND level='prefecture' "
                     "AND period_start='2020-04-01'").fetchone()
    assert t[0] == 64485 + 125867


def test_reconciliation_does_not_adjust(conn):
    rows = [("mlit_sole_grants", 2021, 24000), ("mlit_sole_grants", 2022, 25000),
            ("kofu_shasoukou", 2021, 100000), ("kofu_bouan", 2021, 110760), ("kofu_bouan", 2022, 99946),
            ("e_kokko_shasoukou", 2021, 86402), ("e_kokko_shasoukou", 2020, 60153)]
    for ind, fy, v in rows:
        conn.execute("""INSERT INTO observations(pref_code, municipality_code, indicator_id, period_start, period_end,
                        decided_date, value, unit) VALUES ('09','092151',?,?,?,?,?,'千円')""",
                     (ind, f"{fy}-04-01", f"{fy + 1}-03-31", f"{fy + 1}-03-31", v))
    r = {x["y"]: x for x in reconciliation(conn, "092151")}
    assert r[2021]["k"] == 210760 and r[2021]["k_a"] == 186760 and r[2021]["c_k"] == 86402 - 210760
    assert r[2022]["k"] == 99946 and r[2022]["c"] is None and r[2022]["c_k"] is None   # 決算がない年度は差を出さない
    assert r[2020]["k"] is None and r[2020]["a"] is None and r[2020]["c"] == 60153


def test_parse_futsu_decision_and_juyo(fixtures):
    fy, rows, _ = parse_decision(fixtures / "futsu_decision_fy2024_sample.xlsx")
    assert fy == 2024
    toch = [r for r in rows if r[0] == "栃木県"]
    assert len(toch) == 25
    assert ("栃木県", "那須烏山市", 4140630.0) in toch
    assert any(r[0] == "群馬県" for r in rows)                    # 都道府県名の列が変わると切り替わる
    j = parse_juyo(fixtures / "futsu_juyo_fy2024_sample.xlsx")
    assert j["fiscal_year"] == 2024 and j["municipal"]
    assert j["rows"]["092151"]["道路橋りょう費"] == 165801


def test_parse_totals_overview_and_tokko(fixtures):
    s = shasoukou_national(fixtures / "mlit_overview_shasoukou_2026_p3.pdf")
    assert s["国費 計"] == 12969 and s["社会資本整備総合交付金 国費"] == 4584 and s["防災・安全交付金 国費"] == 8384
    r = road_totals(fixtures / "mlit_overview_road_2026_p3p5.pdf", "栃木")
    assert r == {"national_hojo": 860753, "pref_hojo": 11295}       # 百万円、事業費ベース
    d = parse_pref_totals_dec(fixtures / "tokko_2025_12_prefs_p2.pdf")
    assert d["全国"] == 243889076 and d["栃木"] == 1905499
    t = parse_town_totals_march(fixtures / "tokko_2025_03_towns_p8.pdf")
    assert t["全国"] == [204164994, 274902700] and t["栃木"] == [2056016, 2500781]
