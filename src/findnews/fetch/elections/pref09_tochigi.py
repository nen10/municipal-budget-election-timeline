"""栃木県選挙管理委員会(衆院選)の ElectionSource アダプタ。実体は fetch/tochigi_election.py。"""

from __future__ import annotations

from pathlib import Path

from ... import http
from ...sources.base import ElectionDoc
from .. import tochigi_election as T


class TochigiElectionSource:
    pref_code = "09"
    source_id = "election_09"
    label = "栃木県選挙管理委員会 衆院選(開票区別得票・候補者届出状況公表票)"
    keys = T.ELECTIONS

    def list_elections(self) -> list[ElectionDoc]:
        out = []
        for s in T.list_sources(self.keys):
            if s["url"]:
                out.append(ElectionDoc(s["key"], s["url"], s["filename"],
                                       "results" if s["filename"].endswith(".xls") else "candidates"))
        return out

    def fetch(self, doc: ElectionDoc, dest: Path | None = None) -> Path:
        return http.download(doc.url, T.SOURCE, doc.filename).path

    def parse(self, path: Path):
        """戻り値: (elections, candidates, results) の辞書リスト。"""
        d = T.parse([path])[0]
        date = T.parse_date(d["meta"]["title"])
        elections = [{"election_id": T.election_id(date, s.district), "election_date": date, "district": s.district,
                      "winner": s.winner} for s in d["summaries"]]
        cands = [{"district": k[0], "name": c.name, "legal_name": c.legal_name, "party": c.party,
                  "filing_type": c.filing_type, "incumbency": c.incumbency, "dual": c.dual}
                 for k, (c, _) in d["candidates"].items()]
        results = [{"district": r.district, "counting_unit": r.counting_unit, "candidate": r.candidate, "votes": r.votes}
                   for r in d["rows"]]
        return elections, cands, results

    def run(self, conn, offline: bool = False, force: bool = False):
        return T.run(conn, offline, force, self.keys)
