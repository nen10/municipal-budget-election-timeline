"""申請・要望の記録(DESIGN.md 第4節 requests、13.5、13.6)と参院選のパーサー。"""

import csv

from findnews import requests as R
from findnews.parse import mlit_plans, plan_doc, tochigi_sangiin


def test_plan_doc(fixtures):
    d = plan_doc.parse_pdf(fixtures / "plan_092151_nigiwai.pdf")
    assert d.program == "社会資本整備総合交付金" and d.doc_date == "2024-01-15"
    assert d.plan_name == "快適で安全な那須烏山市中心部におけるにぎわいのまちづくり計画"
    assert (d.start_fy, d.end_fy) == (2024, 2028) and d.grantees == ["那須烏山市"] and d.total_cost_million_yen == 1400


def test_mlit_jigo_hyoka_list(fixtures):
    rows = [r for r in mlit_plans.parse_pdf(fixtures / "jigohyoka_shasoukou_p3.pdf") if r.pref_name == "栃木県"]
    assert len(rows) == 17 and all(r.link for r in rows)
    r = next(r for r in rows if r.plan_name.startswith("亀山北地区"))
    assert (r.start_fy, r.end_fy, r.planners) == (2017, 2021, ["栃木県"])
    assert mlit_plans.era_code_to_fy("H31") == 2019 and mlit_plans.era_code_to_fy("R2") == 2020


def test_sangiin_parsers(fixtures):
    a = tochigi_sangiin.parse_xls(fixtures / "sangiin_r04sangi_kaihyo.xls")
    assert a.date == "2022-07-10" and a.winner == "上野 みちこ" and dict(a.candidates)["上野 みちこ"] == "自由民主党"
    assert len({u for u, _, _ in a.rows}) == 25
    b = tochigi_sangiin.parse_html(fixtures / "sangiin_r07sangi_kaihyo.html")
    assert b.date == "2025-07-20" and b.winner == "高橋 かつのり"
    assert dict(b.candidates)["高橋 かつのり"] == "自由民主党"
    assert ("那須烏山市", "高橋 かつのり") in {(u, c) for u, c, _ in b.rows}


def test_manual_requests_require_source_and_quote(conn, tmp_path):
    cols = ["municipality_code", "record_type", "request_date", "recipient", "program", "project_name", "result",
            "result_date", "source_url", "quote", "indicator_link"]
    with (tmp_path / "requests.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow({"municipality_code": "094072", "record_type": "要望", "request_date": "2025-08-04", "recipient": "国",
                    "program": "要望", "project_name": "国道4号", "result": "不明", "source_url": "https://example.org/a.pdf",
                    "quote": "６車線化を要望します", "indicator_link": "mlit_road"})
        w.writerow({"municipality_code": "094072", "record_type": "要望", "request_date": "2025-09-01",
                    "project_name": "出典なし", "quote": "x"})          # 出典 URL なし → 登録しない
    (tmp_path / "requests_status.csv").write_text(
        "municipality_code,collection_source,status,note\n094072,municipal_page,未収集,古いメモ\n094111,municipal_page,未収集,見つからない\n",
        encoding="utf-8")
    assert R.load_manual(conn, tmp_path) == 1
    st = {r["municipality_code"]: r["status"] for r in conn.execute(
        "SELECT * FROM request_status WHERE collection_source='municipal_page'")}
    assert st == {"094072": "収集済", "094111": "未収集"}      # 記録があれば収集済を優先


def test_chart_date_and_plan_key():
    assert R.plan_key('快適で安全な那須烏山市中心部における"にぎわいのまちづくり"計画') == \
        R.plan_key("快適で安全な那須烏山市中心部におけるにぎわいのまちづくり計画")
    q = {"request_date": None, "date_precision": "fiscal_year", "fiscal_year": 2021, "result_date": "2022-03-25"}
    assert R.chart_date(q) == ("2021-04-01", "2022-03-25")
