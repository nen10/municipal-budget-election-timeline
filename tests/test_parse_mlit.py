from findnews.parse import mlit_kasho as P


def test_kasho_fixture(fixtures):
    d = P.parse_pdf(fixtures / "kasho_2026_09_sample.pdf")
    g = d["grants"]
    sole = [x for x in g if x.attribution == "sole" and x.municipality_code == "092151"]
    assert [(x.program, x.amount_thousand_yen) for x in sole] == [("社会資本整備総合交付金", 22220)]
    road = [x for x in g if x.plan_name.startswith("拠点間の連携・交流を支えるとちぎの道づくり")][0]
    assert road.attribution == "joint" and road.municipality_code is None
    assert road.amount_thousand_yen == 3034088
    assert "092151" in road.recipient_codes and "094111" not in road.recipient_codes
    bouan = [x for x in g if x.program == "防災・安全交付金"]
    assert bouan and all(x.amount_thousand_yen for x in bouan)
    unresolved = {u for x in g for u in x.unresolved}
    assert unresolved <= {"芳賀中部上水道企業団"}


def test_classify():
    assert P.classify(["栃木県"])[0] == "prefecture"
    assert P.classify(["那須烏山市"])[:2] == ("sole", "092151")
    assert P.classify(["栃木県", "那須烏山市"])[0] == "joint"
    assert P.classify(["那須烏山市", "那珂川町"])[0] == "joint"
