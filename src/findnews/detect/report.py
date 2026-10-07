"""Markdown レポートの生成。文言は「要検証シグナル」に限定し、意図や因果を断定しない。"""

from __future__ import annotations

import json
import math
import sqlite3

import pandas as pd

from ..municipalities import TOCHIGI
from .panel import decision_date
from .score import WEIGHTS
from .statements import by_politician, mentioning, snippet

DISCLAIMER = (
    "> この文書は公開データから機械的に算出した **要検証シグナル** の一覧です。"
    "数値の偏差は事業サイクル・災害復旧・計画の統廃合・申請の有無など多くの理由で生じます。"
    "ここに載ることは、いかなる個人・団体の不正や意図を示すものでもありません。"
    "結論を出す前に、必ず一次資料と当事者への取材で確認してください。"
)

METRIC_LABEL = {
    "card:国庫支出金": "決算 国庫支出金",
    "card:都道府県支出金": "決算 都道府県支出金",
    "card:特別交付税": "決算 特別交付税",
    "card:普通建設事業費_うち補助": "決算 普通建設事業費(補助)",
    "card:土木費": "決算 土木費",
    "tokko:交付総額": "特別交付税 交付総額(報道発表)",
    "tokko:3月交付額": "特別交付税 3月交付額(報道発表)",
    "mlit:road_maint": "道路メンテナンス事業 当初配分(単独)",
    "mlit:sole_grants": "社総交+防安交 単独計画の当初配分",
    "mlit:joint_plans": "社総交+防安交 共同計画への参加件数",
}


def _fmt(v, nd=0):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if nd == 0:
        return f"{v:,.0f}"
    return f"{v:+.{nd}f}" if nd and v is not None else str(v)


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def joint_plan_changes(conn: sqlite3.Connection, code: str) -> list[str]:
    rows = conn.execute(
        """SELECT fiscal_year, program_id, item_name, amount_thousand_yen FROM subsidy_allocations
           WHERE program_id IN ('mlit_shasoukou','mlit_bouan') AND attribution='joint'
             AND (',' || recipient_codes || ',') LIKE ? ORDER BY fiscal_year, item_name""", (f"%,{code},%",)).fetchall()
    by_year: dict[int, dict[str, float]] = {}
    for r in rows:
        by_year.setdefault(r[0], {})[r[2]] = r[3]
    out = []
    prev = None
    for fy in sorted(by_year):
        cur = by_year[fy]
        line = f"- FY{fy}: {len(cur)} 件"
        if prev is not None:
            added = sorted(set(cur) - set(prev))
            removed = sorted(set(prev) - set(cur))
            if added:
                line += " / 新たに含まれた: " + "、".join(added)
            if removed:
                line += " / 含まれなくなった: " + "、".join(removed)
        out.append(line)
        prev = cur
    out.append("  (計画名の期替わり(第三期→第四期など)も「含まれなくなった/新たに含まれた」として表示される)")
    return out


def latest_errors(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(source, step, 対象) ごとに最新の記録がエラーのものだけを返す(後で成功したものは除く)。"""
    latest: dict[tuple, tuple] = {}
    for r in conn.execute("SELECT source, step, status, detail, source_url FROM fetch_log ORDER BY id"):
        target = (r["detail"] or "").split(":")[0] if r["step"] == "parse" else (r["source_url"] or r["detail"])
        latest[(r["source"], r["step"], target)] = (r["status"], r["detail"] or "")
    return [(k[0], k[1], v[1]) for k, v in latest.items() if v[0] == "error"]


def render(conn: sqlite3.Connection, run_id, pref_code, k, series, dev, groups, feats, pnl, sig, st, focus) -> str:
    L = [f"# 要検証シグナル レポート(都道府県コード {pref_code})", "", f"run_id: `{run_id}`", "", DISCLAIMER, ""]

    # 1. データの範囲
    L += ["## 1. 使用データの範囲", ""]
    if len(series):
        cov = series.groupby("metric").agg(years=("fiscal_year", lambda s: f"{int(s.min())}–{int(s.max())}"),
                                           municipalities=("code", "nunique"),
                                           non_null=("value", lambda s: int(s.notna().sum())))
        L.append(_table(["指標", "内容", "年度", "自治体数", "値のある行"],
                        [[m, METRIC_LABEL.get(m, m), r.years, r.municipalities, r.non_null] for m, r in cov.iterrows()]))
    L += ["", "金額の単位は千円。年度は西暦の会計年度(令和6年度 = 2024)。",
          "選挙(2026-02-08)より後に決定された値を「選挙後」とみなす: 国交省の当初配分は FY2026(2026 年 4 月公表)、"
          "特別交付税は FY2025 の 3 月分(2026-03-17 決定)。決算カードは FY2024 が最新で、選挙後の決算値はまだ存在しない。", ""]
    # 決算カードと報道発表の特別交付税の突合(同じ値になるはず)
    chk = conn.execute(
        """SELECT COUNT(*), SUM(ABS(f.value - a.amount_thousand_yen) < 0.5) FROM municipality_fiscal f
           JOIN subsidy_allocations a ON a.municipality_code=f.code AND a.fiscal_year=f.fiscal_year
           WHERE f.item='特別交付税' AND f.source='soumu_card' AND a.program_id='soumu_tokko' AND a.item_name='交付総額'""").fetchone()
    if chk and chk[0]:
        L += [f"検算: 決算カードの特別交付税と報道発表の交付総額が一致した市×年度 {chk[1]} / {chk[0]}。", ""]
    errs = latest_errors(conn)
    if errs:
        L += ["最新の実行でエラーのままの取得・パース(fetch_log):", ""]
        L += [f"- {s_} / {st_}: {d_[:200]}" for s_, st_, d_ in errs]
        L.append("")

    # 2. ピア
    L += ["## 2. ピア(比較対象)の定義", "",
          f"同県内で、決算カード(FY{int(feats['base_year'].iloc[0]) if len(feats) else '—'})の財政力指数と住民基本台帳人口(対数)を"
          f"県内で標準化し、距離の近い {k} 団体をピアとした。", ""]
    for c in focus:
        if c in groups:
            rows = [[p, TOCHIGI.get(p, p), f"{feats.loc[p, 'fci']:.2f}", f"{math.exp(feats.loc[p, 'logpop']):,.0f}"]
                    for p in [c] + groups[c]]
            L += [f"**{TOCHIGI.get(c, c)}({c})のピア**", "", _table(["コード", "名称", "財政力指数", "人口"], rows), ""]

    # 3. 対象自治体の推移
    L += ["## 3. 対象自治体の年度推移とピア比偏差", "",
          "各セル: 値(千円、件数指標は件) / 対称変化率 g / ピア中央値 / z。z = (g − ピア中央値) / max(ピア標準偏差, 0.05)。"
          "「*」は選挙後に決定された値。", ""]
    edate = pd.Timestamp("2026-02-08").date()
    for c in focus:
        d = dev[dev.code == c]
        if d.empty:
            L += [f"### {TOCHIGI.get(c, c)}({c})", "", "データなし", ""]
            continue
        L += [f"### {TOCHIGI.get(c, c)}({c})", ""]
        years = sorted(d.fiscal_year.unique())
        rows = []
        for m in sorted(d.metric.unique(), key=lambda x: list(METRIC_LABEL).index(x) if x in METRIC_LABEL else 99):
            row = [METRIC_LABEL.get(m, m)]
            for y in years:
                r = d[(d.metric == m) & (d.fiscal_year == y)]
                if r.empty:
                    row.append("")
                    continue
                r = r.iloc[0]
                mark = "*" if decision_date(m, int(y)) > edate else ""
                val = _fmt(r.value)
                if pd.isna(r.growth):
                    row.append(f"{val}{mark}")
                else:
                    med = "—" if pd.isna(r.peer_median_growth) else f"{r.peer_median_growth:+.2f}"
                    row.append(f"{val}{mark}<br>g={r.growth:+.2f} / 中央値={med}<br>z={_fmt(r.z, 2)}")
            rows.append(row)
        L += [_table(["指標"] + [str(int(y)) for y in years], rows), ""]
        alt = {m: p_ for m, p_ in d.groupby("metric")["peers"].first().items() if p_ != ",".join(groups.get(c, []))}
        for m, p_ in alt.items():
            L.append(f"- {METRIC_LABEL.get(m, m)} のピア(値のある自治体から選択): "
                     + "、".join(TOCHIGI.get(x, x) for x in p_.split(",") if x))
        L += ["", f"**{TOCHIGI.get(c, c)} が策定主体に含まれる共同計画(社総交・防安交、金額は計画全体で按分不能)**", ""]
        L += joint_plan_changes(conn, c)
        L.append("")

    # 4. シグナル
    L += ["## 4. 寄与内訳つきスコア", "",
          "重み: " + "、".join(f"{k} {v}" for k, v in WEIGHTS.items()) +
          "。score は当該議員が当該自治体で最多得票でなかった場合のみ出力(それ以外は 0、ゲート前の値は score_ungated)。"
          "スコアは確認の優先順位付けのための目安であり、確率や疑惑の強さではない。", ""]
    if len(sig):
        rows = []
        for r in sig.sort_values(["score", "score_ungated"], ascending=False).itertuples():
            ev = json.loads(r.evidence)
            rows.append([f"{TOCHIGI.get(r.municipality_code, r.municipality_code)}", r.politician_id,
                         "" if pd.isna(r.fiscal_year) else int(r.fiscal_year),
                         "はい" if r.opposed_locally else "いいえ", f"{r.candidate_share:.1%}",
                         f"{r.score:.3f}", f"{r.score_ungated:.3f}",
                         f"財政 {r.c_fiscal_deviation:.2f} / 発言 {r.c_statement_match:.2f} / 権限 {r.c_authority:.2f} / 反転 {r.c_reversal:.2f}",
                         f"{METRIC_LABEL.get(ev['worst_post_metric'], ev['worst_post_metric'])} z={ev['worst_post_z']}"])
        L += [_table(["自治体", "議員", "年度", "当該自治体で非最多", "得票率", "score", "score_ungated",
                      "寄与(0〜1)", "最も負の選挙後指標"], rows), ""]
        L += ["権限の判定メモ: " + json.loads(sig.iloc[0].evidence)["authority_note"], ""]
    else:
        L += ["シグナルなし(選挙結果または指標が未投入)", ""]

    # 5. 発言
    L += ["## 5. 発言キーワード一致", ""]
    if len(st):
        kc = st.matched_keywords.str.split("|").explode().value_counts()
        L += [_table(["キーワード", "一致した発言数"], [[k_, v] for k_, v in kc.items()]), ""]
        pols = [r[0] for r in conn.execute("SELECT politician_id FROM politicians")]
        for pid in pols:
            sp = by_politician(st, pid)
            L += [f"**{pid} の発言で一致したもの: {len(sp)} 件**", ""]
            for r in sp.head(10).itertuples():
                L.append(f"- {r.date} {r.meeting}「{snippet(r.body, r.matched_keywords)}」 {r.source_url}")
            L.append("")
        for c in focus:
            sm = mentioning(st, c)
            L += [f"**{TOCHIGI.get(c, c)} の名称を含み、キーワードに一致した発言: {len(sm)} 件**", ""]
            for r in sm.head(10).itertuples():
                L.append(f"- {r.date} {r.speaker} {r.meeting}「{snippet(r.body, r.matched_keywords)}」 {r.source_url}")
            L.append("")
    else:
        L += ["一致した発言はない(または未取得)", ""]

    # 6. 注意
    L += ["## 6. 解釈上の注意", "",
          "- 町(那珂川町など)の特別交付税は報道発表に個別額がなく、決算カード(FY2024 まで)でしか追えない。",
          "- 国交省の社総交・防安交は共同計画の自治体別内訳が公表されていない。単独計画と道路メンテナンス事業のみ自治体に帰属させ、"
          "共同計画は参加件数だけを数えている。県事業(計画策定主体が県のみ)は市町に帰属させていない。",
          "- 当初配分のみで、補正予算・年度途中の追加配分・交付決定額(実績)は含まない。",
          "- 道路メンテナンス事業は橋梁点検・修繕計画の進捗で年ごとに大きく変動する。小規模町の値は数千万円単位で、"
          "わずかな額の差でも変化率が大きくなる。",
          "- ピアは 5 団体と少なく、z は不安定になりやすい。選挙前年度の z(参考列)と比べて、選挙後の値が例外的かどうかを見ること。",
          "- 支持表明(endorsements)は出典未確認のため空欄で、ゲートは選挙結果の得票(当該自治体で最多得票でない)だけで判定している。",
          "- 役職データ(positions.csv)は出典未確認(unverified)。農林水産省の役職は、本フェーズで取得した総務省・国交省の指標とは所管が一致しない。",
          ""]
    return "\n".join(L)
