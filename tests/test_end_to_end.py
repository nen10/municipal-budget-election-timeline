"""フィクスチャだけで DB 投入 → 検証 → マトリックス → 検出まで通す(ネットワーク不要)。"""

import csv

from click.testing import CliRunner

from findnews import manual, matrix, verify
from findnews.cli import main
from findnews.detect import run as detect_run
from findnews.fetch import kokkai, mlit_grants, mlit_road, soumu_card, soumu_tokko, tochigi_election

BANNED = ("圧力をかけた", "報復した", "不正があった", "報復が", "圧力があった")


def _load(conn, fixtures):
    manual.load_all(conn)
    soumu_card.load(conn, soumu_card.parse([fixtures / "card_2024_sample.xlsx"]))
    soumu_tokko.load(conn, soumu_tokko.parse([fixtures / "tokko_2025_03_sample.pdf"]))
    xls = fixtures / "shugiin_r08syugi_smd_kaihyo.xls"
    tochigi_election.load(conn, tochigi_election.parse([xls], {xls: [fixtures / "kouho_r08syugi_3.pdf"]}))
    kokkai.load(conn, kokkai.parse([fixtures / "kokkai_sample.json"]))
    manual.load_all(conn)


def test_election_attributes_and_pr(conn, fixtures):
    _load(conn, fixtures)
    rows = {r["candidate_name"]: r for r in conn.execute(
        "SELECT * FROM election_results WHERE election_id='shugiin_20260208_smd_09_3' AND municipality_code='092151'")}
    assert rows["やな 和生"]["politician_id"] == "yana_kazuo"
    assert rows["やな 和生"]["nomination"] == "自由民主党公認"
    assert rows["やな 和生"]["result_label"] == "落選・比例復活"       # election_outcomes.csv(出典つき)
    assert rows["渡辺 しんたろう"]["result_label"] == "選挙区当選"
    assert rows["渡辺 しんたろう"]["rank_in_municipality"] == 1
    assert abs(rows["渡辺 しんたろう"]["vote_share"] - 8642 / 12701) < 1e-9


def test_matrix_and_verify_outputs(conn, fixtures, tmp_path):
    _load(conn, fixtures)
    res = matrix.run(conn, "shugiin_20260208", "09", tmp_path)
    md = (tmp_path / "matrix_tochigi_shugiin_20260208.md").read_text(encoding="utf-8")
    assert "自由民主党公認・落選・比例復活 を支持" in md and "無所属・選挙区当選 を支持" in md
    assert "川俣純子" in md and "渡辺 しんたろう(無所属)" in md
    assert "未収集" in md and res["a1_uncollected"] == 20
    with open(tmp_path / "matrix_tochigi_shugiin_20260208.csv", encoding="utf-8-sig") as f:
        rows = {r["municipality_code"]: r for r in csv.DictReader(f)}
    r = rows["092151"]
    assert r["A1_mayor_name"] == "川俣純子" and r["A1_candidates"] == "渡辺 しんたろう"
    assert r["A2_top1_name"] == "渡辺 しんたろう" and r["A2_top2_name"] == "やな 和生"
    assert r["A3_revived_names"] == "やな 和生" and "農林水産大臣" in r["A3_revived_positions"]
    out = tmp_path / "v.md"
    verify.run(conn, out)
    v = out.read_text(encoding="utf-8")
    assert "那須烏山市(092151)" in v and "未取得" in v
    assert "z=" not in v and "ピア" not in v        # 他自治体との比較はしない
    for text in (md, v):
        for b in BANNED:
            assert b not in text


def test_detect_offline(conn, fixtures, tmp_path):
    _load(conn, fixtures)
    mlit_grants.load(conn, mlit_grants.parse([fixtures / "kasho_2026_09_sample.pdf"]))
    res = detect_run.run(conn, "09", tmp_path / "out", "shugiin_20260208")
    md = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "要検証シグナル" in md and res["n_signals"] > 0
    assert "大幅にカット" in md        # 報道された発言は原文のまま
    for b in BANNED:
        assert b not in md


def test_cli_db_init(tmp_path):
    r = CliRunner().invoke(main, ["--db", str(tmp_path / "x.sqlite"), "db", "init"])
    assert r.exit_code == 0, r.output
    assert "subsidy_allocations" in r.output
