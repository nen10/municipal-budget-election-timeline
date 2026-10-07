"""アダプタのレジストリ。`findnews sources list` で一覧する。"""

from __future__ import annotations

from ..municipalities import has_master
from .indicators import MlitRoadSource, MlitSoleGrantsSource, SoumuCardSource, SoumuTokkoSource

INDICATOR_SOURCES = {a.source_id: a for a in (SoumuCardSource(), SoumuTokkoSource(), MlitSoleGrantsSource(),
                                              MlitRoadSource())}

# 指標 ID → (DESIGN.md の指標番号、表示名、所管省庁)
INDICATORS = {
    "card_kokko": ("1", "国庫支出金(決算)", None),
    "card_pref": ("2", "県支出金(決算)", None),
    "tokko_dec": ("3a", "特別交付税 12月分", "総務省"),
    "tokko_march": ("3b", "特別交付税 3月分", "総務省"),
    "mlit_sole_grants": ("4", "社総交・防安交 単独策定主体の国費", "国土交通省"),
    "mlit_road": ("5", "道路局箇所表 事業主体=当該市町の国費", "国土交通省"),
    # 基準層(DESIGN.md 17.2)
    "e_kokko": ("6", "国庫支出金 計(決算、地方財政状況調査)", None),
    "e_kokko_futsuken": ("6", "国庫支出金のうち普通建設事業費支出金(決算)", None),
    "e_kokko_saigai": ("6", "国庫支出金のうち災害復旧事業費支出金(決算)", None),
    "e_kokko_shasoukou": ("6", "国庫支出金のうち社会資本整備総合交付金(決算)", "国土交通省"),
    "e_pref": ("6", "都道府県支出金 計(決算)", None),
    "e_futsu": ("6", "普通交付税(決算)", "総務省"),
    "e_tokko": ("6", "特別交付税(決算)", "総務省"),
    "e_road_kokko": ("7", "道路橋りょう費に充当した国庫支出金(決算)", "国土交通省"),
    "e_hojo_road_kokko": ("8", "補助事業費(道路・橋りょう)の国庫支出金(決算)", "国土交通省"),
    "futsu_decided": ("9", "普通交付税 当初決定額", "総務省"),
    "kofu_total": ("10", "国交省 交付決定額 計(年度合計)", "国土交通省"),
    "kofu_shasoukou": ("10", "国交省 交付決定額 社会資本整備総合交付金", "国土交通省"),
    "kofu_bouan": ("10", "国交省 交付決定額 防災・安全交付金", "国土交通省"),
    "kofu_road": ("10", "国交省 交付決定額 道路関係の補助", "国土交通省"),
}


def _election_sources():
    from ..fetch.elections.pref09_tochigi import TochigiElectionSource
    return {s.pref_code: s for s in (TochigiElectionSource(),)}


ELECTION_SOURCES = _election_sources()

# 指標でも選挙でもない補助ソース
AUX_SOURCES = {
    "soumu_jumin": ("national", "総務省 住民基本台帳人口(市区町村別)。全国の市区町村マスタと参考人口"),
    "kokkai": ("national", "国会会議録検索システム API(発言キーワード)"),
    "requests": ("national", "申請・要望の記録: 国交省 事後評価一覧(全国)・当初配分資料から機械生成、市町サイトの計画書・要望は手作業で登録"),
}


def election_source(pref_code: str):
    return ELECTION_SOURCES.get(pref_code)


def status_for_pref(pref_code: str) -> list[dict]:
    """各ソースの、その都道府県での対応状況。未対応はエラーにせず理由を返す。"""
    rows = []
    master_ok = has_master(pref_code)
    for sid, a in INDICATOR_SOURCES.items():
        st = "対応" if master_ok else "未対応(市区町村マスタ未取得: --source soumu_jumin を先に実行)"
        rows.append({"source_id": sid, "kind": "indicator", "coverage": a.coverage, "indicators": ",".join(a.indicator_ids),
                     "label": a.label, "status": st, "note": getattr(a, "note", "")})
    es = election_source(pref_code)
    rows.append({"source_id": es.source_id if es else f"election_{pref_code}", "kind": "election", "coverage": "prefecture",
                 "indicators": "", "label": es.label if es else "(県選管アダプタなし)",
                 "status": "対応" if es else "未対応(この都道府県の選管アダプタが未実装)", "note": ""})
    for sid, (cov, label) in AUX_SOURCES.items():
        rows.append({"source_id": sid, "kind": "auxiliary", "coverage": cov, "indicators": "", "label": label,
                     "status": "対応", "note": ""})
    return rows
