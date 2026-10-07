"""指標アダプタ。既存の取得・パースモジュール(fetch/*, parse/*)を包み、Observation を返す。

指標 ID:
  card_kokko       決算カード 国庫支出金(指標 1)          観測時点 = 年度、decided_date = 年度末(3/31)
  card_pref        決算カード 都道府県支出金(指標 2)      同上
  tokko_dec        特別交付税 12 月分(指標 3a)            decided_date = 交付決定日(報道発表日)
  tokko_march      特別交付税 3 月分(指標 3b)             同上
  mlit_sole_grants 社総交・防安交 単独策定主体の国費(指標 4) decided_date = 当初配分の報道発表日(代用)
  mlit_road        道路局箇所表 事業主体=市町の国費(指標 5)  同上
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import http
from ..municipalities import master
from .base import Document, Observation, fy_period

TOKKO_TOWN_REASON = "報道発表に町村の個別額なし(都道府県計のみ)"


def _meta(path: Path) -> dict:
    return http.manifest_meta(path) or {"url": None, "retrieved_at": None}


def _download(doc: Document, dest: Path, source: str) -> Path:
    return http.download(doc.url, source, doc.filename).path


class SoumuCardSource:
    source_id = "soumu_card"
    indicator_ids = ("card_kokko", "card_pref")
    coverage = "national"
    label = "総務省 市町村決算カード"
    ITEMS = {"card_kokko": "国庫支出金", "card_pref": "都道府県支出金"}

    def list_documents(self, pref_code, years):
        from ..fetch import soumu_card
        return [Document(s["url"], s["filename"], s["fiscal_year"], extra={"page": s["page"]})
                for s in soumu_card.list_sources(5, pref_code, years or None)]

    def fetch(self, doc, dest=None):
        return _download(doc, dest, self.source_id)

    def local_files(self, pref_code):
        from ..fetch import soumu_card
        return soumu_card.local_files(pref_code)

    def parse(self, path, pref_code):
        from ..parse import soumu_card as P
        meta = _meta(path)
        out = []
        for r in P.parse_workbook(path, pref_code):
            for ind, item in self.ITEMS.items():
                if r.item == item:
                    ps, pe = fy_period(r.fiscal_year)
                    out.append(Observation(pref_code, r.code, ind, ps, pe, pe, False, "決算(年度末)", None, r.value, "千円",
                                           missing_reason=None if r.value is not None else "決算カードに値なし",
                                           source_url=meta["url"], retrieved_at=meta["retrieved_at"],
                                           generated_by=self.source_id))
        return out

    def load_legacy(self, conn, paths, pref_code):
        from ..fetch import soumu_card
        return soumu_card.load(conn, soumu_card.parse(paths, pref_code))


class SoumuTokkoSource:
    source_id = "soumu_tokko"
    indicator_ids = ("tokko_dec", "tokko_march")
    coverage = "national"
    label = "総務省 特別交付税 報道発表(12 月分・3 月分)"
    note = "市のみ市別。町村は都道府県計のみのため未取得"

    def list_documents(self, pref_code, years):
        from ..fetch import soumu_tokko
        return [Document(s["url"], s["filename"], s["fiscal_year"], extra={"kind": s["kind"]})
                for s in soumu_tokko.list_sources(years or list(range(2020, 2026))) if s.get("url")]

    def fetch(self, doc, dest=None):
        return _download(doc, dest, self.source_id)

    def local_files(self, pref_code):
        from ..fetch import soumu_tokko
        return soumu_tokko.local_files()

    def parse(self, path, pref_code):
        from ..fetch import soumu_tokko
        d = soumu_tokko.parse([path], pref_code)[0]
        ind = "tokko_dec" if d["kind"] == "12月" else "tokko_march"
        ps, pe = fy_period(d["fiscal_year"])
        out = []
        for code, name in master(pref_code).items():
            r = d["found"].get(code)
            if r is not None:
                val, reason = r.amounts[0], None
            else:
                val = None
                reason = "資料に記載なし" if name.endswith("市") else TOKKO_TOWN_REASON
            out.append(Observation(pref_code, code, ind, ps, pe, d["decision_date"], False, "交付決定日(報道発表)",
                                   d["decision_date"], val, "千円", missing_reason=reason,
                                   source_url=d["meta"]["url"], retrieved_at=d["meta"]["retrieved_at"],
                                   generated_by=self.source_id))
        return out

    def load_legacy(self, conn, paths, pref_code):
        from ..fetch import soumu_tokko
        return soumu_tokko.load(conn, soumu_tokko.parse(paths, pref_code), pref_code)


class _MlitBase:
    coverage = "national"

    def list_documents(self, pref_code, years):
        from ..fetch import mlit_grants
        fys = [y for y in (years or sorted(mlit_grants.RELEASES))]
        return [Document(s["url"], s["filename"], s["fiscal_year"], published_date=s.get("date"))
                for s in mlit_grants.list_sources(fys, pref_code) if s.get("url")]

    def fetch(self, doc, dest=None):
        return _download(doc, dest, "mlit_grants")

    def local_files(self, pref_code):
        from ..fetch import mlit_grants
        return mlit_grants.local_files(pref_code)

    def _obs(self, path, pref_code, sums: dict[str, tuple[float, int]]):
        from ..fetch import mlit_grants
        fy = int(re.search(r"kasho_(\d{4})_", path.name).group(1))
        date = mlit_grants.RELEASES.get(fy, (None, None, None))[1]
        meta = _meta(path)
        ps, pe = fy_period(fy)
        return [Observation(pref_code, code, self.indicator_ids[0], ps, pe, date, True,
                            "当初配分の報道発表日を代用(交付決定日は未公表)", date,
                            sums.get(code, (0.0, 0))[0], "千円", sums.get(code, (0.0, 0))[1],
                            source_url=meta["url"], retrieved_at=meta["retrieved_at"], generated_by=self.source_id)
                for code in master(pref_code)]


class MlitSoleGrantsSource(_MlitBase):
    source_id = "mlit_grants"
    indicator_ids = ("mlit_sole_grants",)
    label = "国交省 社会資本整備総合交付金・防災・安全交付金 当初配分(単独策定主体のみ)"
    note = "共同計画は自治体別内訳がなく含めない。補正配分は未取得"

    def parse(self, path, pref_code):
        from ..parse import mlit_kasho as P
        sums: dict[str, list] = {}
        for g in P.parse_pdf(path, pref_code)["grants"]:
            if g.attribution == "sole":
                s = sums.setdefault(g.municipality_code, [0.0, 0])
                s[0] += g.amount_thousand_yen or 0
                s[1] += 1
        return self._obs(path, pref_code, {k: tuple(v) for k, v in sums.items()})

    def load_legacy(self, conn, paths, pref_code):
        from ..fetch import mlit_grants
        return mlit_grants.load(conn, mlit_grants.parse(paths, pref_code))


class MlitRoadSource(_MlitBase):
    source_id = "mlit_road"
    indicator_ids = ("mlit_road",)
    label = "国交省 道路局 当初配分箇所表(事業主体=市町)"
    note = "国道・県道等の補助事業は事業主体の記載がなく含めない"

    def parse(self, path, pref_code):
        from ..parse import mlit_road as P
        sums: dict[str, list] = {}
        for r in P.parse_pdf(path, pref_code):
            if r.attribution == "sole":
                s = sums.setdefault(r.municipality_code, [0.0, 0])
                s[0] += (r.amount_million_yen or 0) * 1000
                s[1] += 1
        return self._obs(path, pref_code, {k: tuple(v) for k, v in sums.items()})

    def load_legacy(self, conn, paths, pref_code):
        from ..fetch import mlit_road
        return mlit_road.load(conn, mlit_road.parse(paths, pref_code))
