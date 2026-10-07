"""フィクスチャだけで DB 投入 → 検出 → レポート出力まで通す(ネットワーク不要)。"""

from click.testing import CliRunner

from findnews import manual
from findnews.cli import main
from findnews.detect import run as detect_run
from findnews.fetch import kokkai, mlit_grants, soumu_card, soumu_tokko, tochigi_election


def test_pipeline_offline(conn, fixtures, tmp_path):
    manual.load_all(conn)
    soumu_card.load(conn, soumu_card.parse([fixtures / "card_2024_sample.xlsx"]))
    soumu_tokko.load(conn, soumu_tokko.parse([fixtures / "tokko_2025_03_sample.pdf"]))
    tochigi_election.load(conn, tochigi_election.parse([fixtures / "shugiin_2026_smd_kaihyo.xls"]))
    kokkai.load(conn, kokkai.parse([fixtures / "kokkai_sample.json"]))
    manual.load_all(conn)
    assert conn.execute("SELECT COUNT(*) FROM election_results WHERE politician_id='yana_kazuo'").fetchone()[0] == 6
    # 比例復活は出典未記入なので NULL のまま
    assert conn.execute("SELECT COUNT(*) FROM election_results WHERE pr_revived IS NOT NULL").fetchone()[0] == 0
    res = detect_run.run(conn, "09", 5, tmp_path / "out")
    md = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "要検証シグナル" in md
    for banned in ("圧力をかけた", "報復した", "不正があった"):
        assert banned not in md
    assert res["focus"] == ["092151", "094111"]


def test_cli_db_init(tmp_path):
    r = CliRunner().invoke(main, ["--db", str(tmp_path / "x.sqlite"), "db", "init"])
    assert r.exit_code == 0, r.output
    assert "subsidy_allocations" in r.output
