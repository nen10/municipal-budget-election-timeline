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
