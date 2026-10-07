from findnews.parse import mlit_road as P


def test_road_section_tables(fixtures):
    rows = P.parse_pdf(fixtures / "kasho_2026_09_road_sample.pdf")
    by = {(r.table, r.item_name, r.entity or r.location): r for r in rows}
    # 直轄は国
    assert all(r.attribution == "national" for r in rows if r.table == "A_direct")
    # 補助・路線名が(町)の市町村道 → 当該町を事業主体とする
    r = next(r for r in rows if r.table == "A_subsidy" and "壬生" in r.item_name)
    assert (r.municipality_code, r.attribution, r.basis) == ("093611", "sole", "route_type")
    # 都市計画道路(都)は事業主体の記載がないため帰属させない
    r = next(r for r in rows if r.item_name == "駒生町工区")
    assert r.attribution == "unknown" and r.municipality_code is None
    # 踏切: 事業主体列
    assert by[("C", "日光線第248号踏切道", "栃木市")].municipality_code == "092037"
    assert by[("C", "第一大田原街道踏切道", "栃木県")].attribution == "prefecture"
    # 道路メンテナンス
    nk = [r for r in rows if r.municipality_code == "094111"]
    assert sorted(r.amount_million_yen for r in nk) == [37, 41]
    assert [r.amount_million_yen for r in rows if r.municipality_code == "092151"] == [20]
    # 地域再生計画(旧地方創生道整備交付金): 1 市町なら sole、複数なら joint
    assert any(r.table == "D" and r.attribution == "joint" for r in rows)
    assert any(r.table == "D" and r.municipality_code == "092053" for r in rows)
    # 次の局の表紙で止まる(水管理・国土保全局の表は含まない)
    assert not any("河川" in r.work_type for r in rows)
