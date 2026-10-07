"""検出レポート(Markdown)。文言は「要検証シグナル」に限定し、意図や因果を断定しない。
政党名・候補者名・役職・金額・日付は省略しない(DESIGN.md 11.6)。"""

from __future__ import annotations

import json
import sqlite3

from .. import verify as V
from .score import WEIGHTS
from .statements import by_politician, excerpt, mentioning

DISCLAIMER = (
    "> この文書は公開データから機械的に算出した **要検証シグナル** の一覧です。各自治体を自分自身の過去とだけ比べており、"
    "他自治体との比較はしていません。増減は事業サイクル・災害復旧・計画の統廃合・申請の有無など多くの理由で生じます。"
    "ここに載ることは、いかなる個人・団体の不正や意図を示すものでもありません。"
)
LABEL = {"tokko_march": "指標 3 特別交付税 3月分", "mlit_sole_grants": "指標 4 社総交・防安交(単独策定主体)",
         "mlit_road": "指標 5 道路局箇所表(事業主体=市町)"}


def _t(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x).replace("|", "/") for x in r) + " |" for r in rows]
    return "\n".join(out)


def render(conn: sqlite3.Connection, run_id, pref_code, group, edate, sig, st, th, names) -> str:
    TOCHIGI = names  # noqa: N806
    L = [f"# 要検証シグナル レポート(都道府県コード {pref_code}、選挙 {group})", "", f"run_id: `{run_id}`", "", DISCLAIMER, ""]
    if edate:
        L += [f"各指標について、投票日({edate})の直後に来る最初の観測時点の、直前の観測時点に対する差分を使う"
              f"(平均との比較はしない)。方向の閾値 ±{th * 100:.1f}%。", ""]
    L += ["## 1. 寄与内訳つきスコア", "",
          "重み: " + "、".join(f"{k} {v}" for k, v in WEIGHTS.items()) +
          "。score は当該議員が当該自治体で最多得票でなかった場合のみ出力(それ以外は 0、ゲート前の値は score_ungated)。"
          "確認の優先順位付けの目安であり、確率や疑惑の強さではない。", ""]
    if len(sig):
        rows = []
        for r in sig.sort_values(["score", "score_ungated"], ascending=False).itertuples():
            ev = json.loads(r.evidence)
            ind = "; ".join(f"{LABEL[k]}: " + (f"{v['period_start'][:4]}年度分 decided {v['decided_date']}: "
                                               f"直前 {V._n(v['prev_value'])}→{V._n(v['value'])} ({V._pct(v['delta_pct'])}, {v['direction']})"
                                               if v and v.get('value') is not None else
                                               f"{V.MISSING}({(v or {}).get('missing_reason') or '投票日後の観測時点なし'})")
                            for k, v in ev["indicators"].items())
            rows.append([TOCHIGI.get(r.municipality_code), f"{r.candidate_name}({r.nomination}、{r.result_label})",
                         f"{r.candidate_share:.1%}({r.candidate_rank}位)", f"{r.top_candidate}({r.top_nomination}) {r.top_share:.1%}",
                         f"{r.score:.3f}", f"{r.score_ungated:.3f}",
                         f"自身の減少 {r.c_own_decline:.2f} / 発言 {r.c_statement_match:.2f}"
                         + (f"(由来事例のため除外 {r.n_statements_not_independent} 件)" if r.n_statements_not_independent else "")
                         + f" / 権限 {r.c_authority:.2f} / 反転 {r.c_reversal:.2f}",
                         ind])
        L += [_t(["自治体", "議員(政党、結果)", "議員の得票率(順位)", "自治体内 1 位", "score", "score_ungated", "寄与(0〜1)",
                  "指標(投票日後最初の観測時点の直前時点比)"], rows), ""]
        L += ["権限の判定メモ:", ""] + sorted({f"- {r.politician_id}: {json.loads(r.evidence)['authority_note']}"
                                            for r in sig.itertuples()}) + [""]
    else:
        L += ["シグナルなし(politicians.csv に登録された候補の選挙結果がない)", ""]

    L += ["## 2. 発言キーワード一致(原文)", ""]
    if len(st):
        from ..keywords import ORIGIN
        kc = st.matched_keywords.str.split("|").explode().value_counts()
        L += [_t(["キーワード", "一致した発言数", "由来", "由来事例", "由来の出典"],
                 [[k, v, ORIGIN.get(k, {}).get("origin", ""), ORIGIN.get(k, {}).get("origin_case_id", "") or "—",
                   ORIGIN.get(k, {}).get("origin_source_url", "") or "—"] for k, v in kc.items()]), "",
              "由来事例のあるキーワードが、その事例の議員・自治体の発言に一致した場合は「由来事例のため独立検証にならない」と注記し、"
              "スコアの発言一致に数えない(DESIGN.md 12 節 8 項)。", ""]
        press = st[st.source == "press"]
        if len(press):
            L += ["### 報道された発言(data/manual/statements.csv、引用文は原文)", ""]
            for r in press.itertuples():
                L.append(f"- 発言者: {r.speaker} / 日付: {r.date} / 場: {r.meeting} / 媒体: {r.speaker_group} / 出典: {r.source_url}")
                L.append(f"  - 引用:「{r.body}」 一致キーワード: {r.matched_keywords}"
                         + (f" ※{r.origin_note}" if r.origin_note else ""))
            L.append("")
        pids = [x[0] for x in conn.execute("SELECT politician_id FROM politicians")]
        L += ["### 登録議員の発言で一致したもの", ""]
        for pid in pids:
            sp = by_politician(st, pid)
            if len(sp):
                L.append(f"**{pid}: {len(sp)} 件**")
                for r in sp.itertuples():
                    L.append(f"- {r.date} {r.meeting}「{excerpt(r.body, r.matched_keywords)}」 {r.source_url}"
                             + (f" ※{r.origin_note}" if r.origin_note else ""))
                L.append("")
        codes = sorted({c for c in (sig.municipality_code if len(sig) else [])})
        L += ["### 選挙区内の自治体名を含み一致したもの", ""]
        for c in codes:
            sm = mentioning(st, c)
            if len(sm):
                L.append(f"**{TOCHIGI[c]}: {len(sm)} 件**")
                for r in sm.itertuples():
                    L.append(f"- {r.date} {r.speaker} {r.meeting}「{excerpt(r.body, r.matched_keywords)}」 {r.source_url}"
                             + (f" ※{r.origin_note}" if r.origin_note else ""))
                L.append("")
        kok = st[st.source == "kokkai"]
        L += [f"### 国会会議録で一致した発言(全 {len(kok)} 件、発言者・会議・日付・URL)", ""]
        for r in kok.sort_values("date").itertuples():
            L.append(f"- {r.date} {r.speaker}({r.speaker_group or ''}) {r.meeting} [{r.matched_keywords}] {r.source_url}")
        L.append("")
    L += ["## 3. 注意", "",
          "- 観測時点ごとの差分と政局イベントの対応は data/processed/timeline_<都道府県コード>/。",
          "- 町の特別交付税(指標 3)は報道発表に個別額がなく未取得。指標 4 は単独策定主体の計画のみ、指標 5 は事業主体が当該市町の箇所のみ。",
          "- キーワードの由来は config/keywords.yaml。由来事例への一致は上記のとおり注記し、スコアに数えていない。", ""]
    return "\n".join(L) + "\n"
