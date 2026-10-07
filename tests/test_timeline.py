"""差分計算・対応期間・イベントの対応付け(DESIGN.md 13 節)。"""

from findnews import events, timeline
from findnews.pipeline import store_observations
from findnews.sources.base import Observation


def _obs(code, ind, fy, decided, value, proxy=False):
    return Observation("09", code, ind, f"{fy}-04-01", f"{fy + 1}-03-31", decided, proxy, "test", decided, value, "千円",
                       source_url="u", generated_by="test")


def test_diff_series_and_windows():
    rows = timeline.diff_series([
        {"decided_date": "2025-04-01", "period_start": "2025-04-01", "value": 59000.0},
        {"decided_date": "2024-04-01", "period_start": "2024-04-01", "value": 11000.0},
        {"decided_date": "2026-04-07", "period_start": "2026-04-01", "value": 20000.0},
        {"decided_date": "2027-04-01", "period_start": "2027-04-01", "value": None},
    ], 0.05)
    assert [r["period_start"][:4] for r in rows] == ["2024", "2025", "2026", "2027"]   # decided_date 順
    assert rows[0]["window_start"] is None and rows[0]["direction"] == "未取得"
    assert rows[1]["delta"] == 48000 and rows[1]["direction"] == "増加"
    assert rows[2]["delta"] == -39000 and abs(rows[2]["delta_pct"] + 39000 / 59000) < 1e-12
    assert (rows[2]["window_start"], rows[2]["window_end"]) == ("2025-04-02", "2026-04-07")
    assert rows[3]["delta"] is None and rows[3]["direction"] == "未取得"


def test_zero_previous_is_undefined():
    rows = timeline.diff_series([{"decided_date": "2025-01-01", "period_start": "a", "value": 0.0},
                                 {"decided_date": "2026-01-01", "period_start": "b", "value": 5.0}], 0.05)
    assert rows[1]["delta"] == 5.0 and rows[1]["delta_pct"] is None and rows[1]["direction"] == "未取得"


def test_overlaps_month_and_period_events():
    ev = {"date": "2026-02-01", "end_date": "2026-02-28"}
    assert timeline.overlaps(ev, "2025-04-02", "2026-04-07")
    assert not timeline.overlaps(ev, "2026-04-08", "2027-04-01")
    assert timeline.overlaps({"date": "2026-04-07", "end_date": None}, "2025-04-02", "2026-04-07")   # 端点を含む
    assert not timeline.overlaps({"date": "2025-04-01", "end_date": None}, "2025-04-02", "2026-04-07")


def test_event_association_by_scope(conn, tmp_path):
    store_observations(conn, [_obs("092151", "mlit_road", 2025, "2025-04-01", 59000.0, True),
                              _obs("092151", "mlit_road", 2026, "2026-04-07", 20000.0, True),
                              _obs("092011", "mlit_road", 2025, "2025-04-01", 100.0, True),
                              _obs("092011", "mlit_road", 2026, "2026-04-07", 100.0, True)])
    conn.execute("INSERT INTO elections VALUES ('shugiin_20260208_smd_09_3','shugiin_smd','2026-02-08','栃木県第3区','09',NULL,NULL)")
    conn.execute("""INSERT INTO election_results(election_id, municipality_code, counting_unit, candidate_name, votes)
                    VALUES ('shugiin_20260208_smd_09_3','092151','那須烏山市','A',1)""")
    for ev in [
        {"event_id": "m1", "date": "2026-02-01", "end_date": "2026-02-28", "event_type": "省庁への問い合わせ(報道)",
         "scope": "municipality", "pref_code": "09", "municipality_code": "092151", "source_url": "u"},
        {"event_id": "d1", "date": "2026-02-08", "event_type": "衆院選投票", "scope": "district", "pref_code": "09",
         "district": "栃木県第3区", "source_url": "u"},
        {"event_id": "n1", "date": "2026-03-17", "event_type": "予算配分公表", "scope": "national", "source_url": "u"},
        {"event_id": "late", "date": "2026-05-24", "event_type": "議員発言(報道)", "scope": "municipality",
         "pref_code": "09", "municipality_code": "092151", "source_url": "u"},
    ]:
        events._put(conn, ev)
    rows, _ = timeline.build(conn, "09", ["092151", "092011"], ["mlit_road"], 0.05)
    r = {(x.municipality_code, x.period_start[:4]): x for x in rows}
    assert r[("092151", "2026")].event_ids == ["m1", "d1", "n1"]       # 対応期間 2025-04-02〜2026-04-07
    assert r[("092011", "2026")].event_ids == ["n1"]                   # 3 区外・別自治体のイベントは付かない
    assert r[("092151", "2025")].event_ids == []                       # 最初の観測時点は対応期間なし
    first = timeline.first_after(rows, "092151", "mlit_road", "2026-02-08")
    assert first.decided_date == "2026-04-07" and first.direction == "減少"
    out = timeline.write_outputs(conn, "09", rows, timeline.load_events(conn, "09"), tmp_path)
    md = (tmp_path / "timeline_09" / "092151.md").read_text(encoding="utf-8")
    assert "省庁への問い合わせ(報道)" in md and "2026-05-24" in md      # 対応期間外のイベントも末尾の一覧に出る
    assert (tmp_path / "events_09.md").exists() and out["rows"] == 4


def test_events_import_requires_source(conn, tmp_path):
    p = tmp_path / "ev.csv"
    p.write_text("event_id,date,event_type,scope,source_url\na,2026-01-01,その他,national,https://example.org\n"
                 "b,2026-01-02,その他,national,\n", encoding="utf-8")
    res = events.import_csv(conn, p)
    assert res["imported"] == 1 and res["skipped_without_source"] == ["b"]
