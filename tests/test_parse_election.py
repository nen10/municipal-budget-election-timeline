from findnews.parse import tochigi_election as P


def test_district3(fixtures):
    rows, summ, meta = P.parse_grid(P.read_xls(fixtures / "shugiin_r08syugi_smd_kaihyo.xls"))
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


def test_kouho_filing_table(fixtures):
    from findnews.parse import tochigi_kouho as K
    cs = {c.name: c for c in K.parse_pdf(fixtures / "kouho_r08syugi_3.pdf")}
    assert set(cs) == {"渡辺 しんたろう", "やな 和生", "いが 央"}
    assert cs["やな 和生"].legal_name == "簗 和生"
    assert K.nomination_label(cs["やな 和生"]) == "自由民主党公認"
    assert cs["やな 和生"].incumbency == "前" and cs["やな 和生"].dual is True
    assert K.nomination_label(cs["渡辺 しんたろう"]) == "無所属"
    assert cs["渡辺 しんたろう"].filing_type == "本人届出" and cs["渡辺 しんたろう"].dual is False
