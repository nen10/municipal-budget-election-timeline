"""基準層の指標(DESIGN.md 17.2)と全体系列(16.2)を作る。

取得済みの表(estat_values、municipality_fiscal の普通交付税、grant_decisions、特別交付税・国交省の PDF)から
observations(自治体 × 指標 × 観測時点)と totals(全国・都道府県計)を作り直す。
全体の値が取れない指標・年度は totals に行を作らない(未取得)。
"""

from __future__ import annotations

import json
import re
import sqlite3

from . import http
from .municipalities import master
from .prefs import name as pref_name, short as pref_short
from .sources.base import Observation, fy_period

# indicator_id -> (表, 行の指定, 列名, 表示名)。列番号・行番号は年度で変わるため名前で指定する。
# 行の指定: "01"/"02" は 04 表の行番号(行名は「決算額」で同じため番号で区別)、タプルは行名
ESTAT = {
    "e_kokko": ("04", "01", ["国庫支出金"], "国庫支出金 計(決算)"),
    "e_kokko_futsuken": ("04", "01", ["普通建設事業費支出金"], "国庫支出金のうち普通建設事業費支出金(決算)"),
    "e_kokko_saigai": ("04", "01", ["災害復旧事業費支出金"], "国庫支出金のうち災害復旧事業費支出金(決算)"),
    "e_kokko_shasoukou": ("04", "01", ["社会資本整備総合交付金"], "国庫支出金のうち社会資本整備総合交付金(決算)"),
    "e_pref": ("04", "02", ["都道府県支出金"], "都道府県支出金 計(決算)"),
    "e_futsu": ("04", "01", ["普通交付税"], "普通交付税(決算)"),
    "e_tokko": ("04", "01", ["特別交付税"], "特別交付税(決算)"),
    "e_road_kokko": ("10", ("国庫支出金",), ["土木費・道路橋りょう費"], "道路橋りょう費に充当した国庫支出金(決算)"),
    "e_hojo_road_kokko": ("21", ("道路", "橋りょう"), ["1列の財源内訳・国庫支出金"], "補助事業費(道路・橋りょう)の国庫支出金(決算)"),
}
KOFU = {
    "kofu_total": (None, "国交省 交付決定額 計(年度合計)"),
    "kofu_shasoukou": (r"社会資本整備総合交付金|社会資本整備交付金", "国交省 交付決定額 社会資本整備総合交付金(年度合計)"),
    "kofu_bouan": (r"防災・安全", "国交省 交付決定額 防災・安全交付金(年度合計)"),
    "kofu_road": (r"道路", "国交省 交付決定額 道路関係の補助(年度合計)"),
}
ESTAT_LIST = "https://www.e-stat.go.jp/stat-search/files?toukei=00200251&tstat=000001077755"


def _estat_values(conn, ind: str) -> dict[tuple[str, int], float]:
    table, row, items, _ = ESTAT[ind]
    q = ",".join("?" * len(items))
    if isinstance(row, tuple):
        cond, params = f"l.row_name IN ({','.join('?' * len(row))})", list(row)
    else:
        cond, params = "v.row_no=?", [row]
    rows = conn.execute(
        f"""SELECT v.code, v.fiscal_year, SUM(v.value) FROM estat_values v JOIN estat_labels l
            ON l.fiscal_year=v.fiscal_year AND l.table_no=v.table_no AND l.row_no=v.row_no AND l.item_code=v.item_code
            WHERE v.table_no=? AND {cond} AND l.item_name IN ({q}) GROUP BY 1, 2""", (table, *params, *items))
    return {(c, fy): v for c, fy, v in rows}


def _estat_url(conn, ind, fy):
    table, row, items, _ = ESTAT[ind]
    r = conn.execute("SELECT source_url, retrieved_at FROM estat_labels WHERE fiscal_year=? AND table_no=? LIMIT 1",
                     (fy, table)).fetchone()
    return (r[0], r[1]) if r else (ESTAT_LIST, None)


def build(conn: sqlite3.Connection, pref_code: str) -> dict:
    from .pipeline import store_observations
    names = master(pref_code)
    all_codes = {r[0] for r in conn.execute("SELECT code FROM municipalities")}
    counts = {}
    conn.execute("DELETE FROM observations WHERE pref_code=? AND generated_by IN ('estat_chizai','soumu_futsu','mlit_kofu')",
                 (pref_code,))
    # ---- 地方財政状況調査(指標 6・7・8)
    for ind, (_, _, _, label) in ESTAT.items():
        vals = _estat_values(conn, ind)
        years = sorted({fy for (_, fy) in vals})
        obs = []
        for code in names:
            for fy in years:
                ps, pe = fy_period(fy)
                url, ra = _estat_url(conn, ind, fy)
                v = vals.get((code, fy))
                obs.append(Observation(pref_code, code, ind, ps, pe, pe, False, "決算(年度末)", None, v, "千円",
                                       missing_reason=None if v is not None else "地方財政状況調査に当該団体の行なし",
                                       source_url=url, retrieved_at=ra, generated_by="estat_chizai"))
        counts[ind] = store_observations(conn, obs)
        _totals(conn, ind, vals, all_codes, pref_code, "決算", "地方財政状況調査 市町村分(e-Stat)", label)
    # ---- 普通交付税 決定額(指標 9)
    dates = {}
    p = http.raw_dir("soumu_futsu") / "sources.json"
    if p.exists():
        dates = json.loads(p.read_text(encoding="utf-8")).get("dates", {})
    vals = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT code, fiscal_year, value FROM municipality_fiscal WHERE source='soumu_futsu' AND item='普通交付税 当初決定額'")}
    urls = {r[0]: r[1] for r in conn.execute(
        "SELECT fiscal_year, MIN(source_url) FROM municipality_fiscal WHERE source='soumu_futsu' AND item='普通交付税 当初決定額' GROUP BY 1")}
    obs = []
    for code in names:
        for fy in sorted(urls):
            ps, pe = fy_period(fy)
            d = dates.get(f"{fy}_当初")
            v = vals.get((code, fy))
            obs.append(Observation(pref_code, code, "futsu_decided", ps, pe, d, False, "当初決定日(算定結果の報道資料の日付)", d, v,
                                   "千円", missing_reason=None if v is not None else "決定額の表に当該団体の行なし",
                                   source_url=urls[fy], generated_by="soumu_futsu"))
    counts["futsu_decided"] = store_observations(conn, obs)
    _totals(conn, "futsu_decided", vals, all_codes, pref_code, "当初決定", "総務省 市町村別普通交付税決定額",
            "普通交付税 当初決定額", decided=lambda fy: dates.get(f"{fy}_当初"))
    # ---- 国交省 交付決定(指標 10)
    gd = conn.execute("""SELECT municipality_code, fiscal_year, subsidy_name, amount_yen, decided_date, source_url
                         FROM grant_decisions WHERE municipality_code IS NOT NULL""").fetchall()
    years = sorted({r[1] for r in gd})
    for ind, (pat, label) in KOFU.items():
        agg, last, url = {}, {}, {}
        for code, fy, sname, amt, d, u in gd:
            if pat and not re.search(pat, sname or ""):
                continue
            agg[(code, fy)] = agg.get((code, fy), 0.0) + (amt or 0) / 1000
            if d and (code, fy) not in last or (d and d > last.get((code, fy), "")):
                last[(code, fy)] = d
            url.setdefault(fy, u)
        obs = []
        for code in names:
            for fy in years:
                ps, pe = fy_period(fy)
                v = agg.get((code, fy), 0.0)
                d = last.get((code, fy)) or pe
                obs.append(Observation(pref_code, code, ind, ps, pe, d, True,
                                       "年度内で最後の交付決定日(交付決定がない年度は年度末)。値は年度合計(変更減額を含む)",
                                       None, v, "千円", count=None, source_url=url.get(fy),
                                       generated_by="mlit_kofu"))
        counts[ind] = store_observations(conn, obs)
        muni_codes = {c for c in all_codes if c[2:5] != "000"}  # 都道府県(XX000+検査数字)を除く
        _totals(conn, ind, agg, muni_codes, pref_code, "交付決定", "国交省 補助金等に関する情報開示(交付決定)", label,
                note="公表対象の交付決定の合計。都道府県・企業団等への交付は含まない")
    # ---- 特別交付税(報道発表)・国交省 配分(既存指標の全体)
    _tokko_totals(conn, pref_code)
    _mlit_totals(conn, pref_code)
    # 決算カード(指標 1・2)は地方財政状況調査と同じ定義なので同じ全体系列を使う
    for card, est in (("card_kokko", "e_kokko"), ("card_pref", "e_pref")):
        conn.execute("DELETE FROM totals WHERE indicator_id=?", (card,))
        conn.execute(f"""INSERT INTO totals SELECT '{card}', level, pref_code, series, period_start, period_end, decided_date,
                         value, unit, note, source_url, retrieved_at FROM totals WHERE indicator_id='{est}'""")
    conn.commit()
    return counts


def _totals(conn, ind, vals, codes, pref_code, kind, src_label, label, decided=None, note=None):
    conn.execute("DELETE FROM totals WHERE indicator_id=?", (ind,))
    years = sorted({fy for (_, fy) in vals})
    for fy in years:
        ps, pe = fy_period(fy)
        nat = sum(v for (c, y), v in vals.items() if y == fy and c in codes and v is not None)
        pre = sum(v for (c, y), v in vals.items() if y == fy and c in codes and c.startswith(pref_code) and v is not None)
        d = decided(fy) if decided else pe
        for level, pc, v, who in (("national", "00", nat, "全国 市区町村計"), ("prefecture", pref_code, pre,
                                                                          f"{pref_name(pref_code)} 県内市町村計")):
            conn.execute("INSERT OR REPLACE INTO totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ind, level, pc, f"{who} {label}({kind}、{src_label})", ps, pe, d, v, "千円",
                          note or "市区町村マスタ(住民基本台帳人口の団体コード)にある団体の合計", None, None))


def _tokko_totals(conn, pref_code):
    from .parse import soumu_tokko as P
    sp = pref_short(pref_code)
    for ind in ("tokko_dec", "tokko_march"):
        conn.execute("DELETE FROM totals WHERE indicator_id=?", (ind,))
    for p in sorted(http.raw_dir("soumu_tokko").glob("tokko_*.pdf")):
        m = re.match(r"tokko_(\d{4})_(12|03)", p.name)
        fy = int(m.group(1))
        ps, pe = fy_period(fy)
        meta = http.manifest_meta(p) or {}
        d = P.parse_pdf(p)
        if m.group(2) == "12":
            t = P.parse_pref_totals_dec(p)
            rows = [("national", "00", t.get("全国"), "全国 市町村分"), ("prefecture", pref_code, t.get(sp), f"{pref_name(pref_code)} 市町村分")]
            ind, kind = "tokko_dec", "12月交付額"
        else:
            towns = P.parse_town_totals_march(p)
            cities = {r.pref: r.amounts for r in d["records"] if r.city_stem in ("計", "全国計")}
            nat = (cities.get("全国", [None])[0] or 0) + (towns.get("全国", [None])[0] or 0) if cities.get("全国") and towns.get("全国") else None
            pre = (cities[sp][0] + towns[sp][0]) if cities.get(sp) and towns.get(sp) else None
            rows = [("national", "00", nat, "全国 市町村分(都市計+町村計)"), ("prefecture", pref_code, pre, f"{pref_name(pref_code)} 市町村分(都市計+町村計)")]
            ind, kind = "tokko_march", "3月交付額"
        for level, pc, v, who in rows:
            if v is None:
                continue
            conn.execute("INSERT OR REPLACE INTO totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ind, level, pc, f"{who} 特別交付税 {kind}(交付決定、総務省 報道発表)", ps, pe, d["decision_date"], v,
                          "千円", None, meta.get("url"), meta.get("retrieved_at")))


def _mlit_totals(conn, pref_code):
    from .fetch.mlit_grants import RELEASES
    from .parse.mlit_overview import road_totals, shasoukou_national
    sp = pref_short(pref_code)
    for ind in ("mlit_sole_grants", "mlit_road"):
        conn.execute("DELETE FROM totals WHERE indicator_id=?", (ind,))
    for fy, (press, d, _) in RELEASES.items():
        ps, pe = fy_period(fy)
        a = http.raw_dir("mlit_overview") / f"shasoukou_{fy}.pdf"
        if a.exists():
            n = shasoukou_national(a)
            meta = http.manifest_meta(a) or {}
            if n.get("国費 計") is not None:
                conn.execute("INSERT OR REPLACE INTO totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             ("mlit_sole_grants", "national", "00", "全国 社会資本整備総合交付金+防災・安全交付金 当初配分(国費、配分概要)",
                              ps, pe, d, n["国費 計"] * 100000, "千円",
                              f"配分概要の注記の国費(億円を千円に換算): 社総交 {n.get('社会資本整備総合交付金 国費')} 億円、防安交 {n.get('防災・安全交付金 国費')} 億円。"
                              "自治体の値は単独策定主体の計画のみで、全体は共同計画・都道府県事業を含む",
                              meta.get("url"), meta.get("retrieved_at")))
        pt = conn.execute("""SELECT SUM(amount_thousand_yen), MIN(source_url) FROM subsidy_allocations
                             WHERE program_id IN ('mlit_shasoukou','mlit_bouan') AND fiscal_year=? AND source_url LIKE ?""",
                          (fy, "%")).fetchone()
        # 県計: その都道府県の PDF の配分表の全行(共同計画・県の計画を含む)
        rows = conn.execute("""SELECT SUM(amount_thousand_yen), MIN(source_url) FROM subsidy_allocations a
                               WHERE program_id IN ('mlit_shasoukou','mlit_bouan') AND fiscal_year=?
                               AND (recipient_codes LIKE ? OR recipients LIKE ?)""",
                            (fy, f"{pref_code}%", f"%{pref_name(pref_code)}%")).fetchone()
        if rows and rows[0]:
            conn.execute("INSERT OR REPLACE INTO totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         ("mlit_sole_grants", "prefecture", pref_code,
                          f"{pref_name(pref_code)} 社会資本整備総合交付金+防災・安全交付金 当初配分 県計(国費、県別 PDF の全計画)",
                          ps, pe, d, rows[0], "千円", "共同計画・県の計画を含む県内の全計画の配分国費", rows[1], None))
        b = http.raw_dir("mlit_overview") / f"road_{fy}.pdf"
        if b.exists():
            r = road_totals(b, sp)
            meta = http.manifest_meta(b) or {}
            for level, pc, key, who in (("national", "00", "national_hojo", "全国"), ("prefecture", pref_code, "pref_hojo", pref_name(pref_code))):
                if r.get(key) is not None:
                    conn.execute("INSERT OR REPLACE INTO totals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 ("mlit_road", level, pc, f"{who} 道路関係予算 補助事業 当初配分(事業費ベース、道路局 配分概要。自治体の値は国費ベースで基準が違う)",
                                  ps, pe, d, r[key] * 1000, "千円",
                                  "道路局の補助事業全体(都道府県・政令市等の事業を含む)。自治体の値は事業主体が当該市町の箇所の国費のみ。"
                                  "配分概要に補助事業の国費の全国・県計がないため事業費を全体に使っている。割合(share)は国費÷事業費で、全体に占める国費の割合ではない",
                                  meta.get("url"), meta.get("retrieved_at")))
