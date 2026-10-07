from findnews.parse import tochigi_election as P


def test_district3(fixtures):
    rows, summ, meta = P.parse_grid(P.read_xls(fixtures / "shugiin_2026_smd_kaihyo.xls"))
    assert "確定" in meta["status"]
    s3 = [s for s in summ if s.district == 3][0]
    assert s3.winner == "渡辺 しんたろう"
    assert s3.totals["やな 和生"] == 53975
    assert abs(s3.sekihai["やな 和生"] - 92.291) < 1e-9
    v = {(r.counting_unit, r.candidate): r.votes for r in rows if r.district == 3}
    assert v[("那須烏山市", "渡辺 しんたろう")] == 8642
    assert v[("那須烏山市", "やな 和生")] == 2760
    assert v[("那珂川町", "やな 和生")] == 2481
    # 郡計・市部計は含めない
    assert not any(r.counting_unit.endswith("計") for r in rows)
    assert {s.district for s in summ} == {1, 2, 3, 4, 5}
