"""第10節「都道府県内全市区町村の時系列記録」(`findnews verify --pref 09`、別名 `findnews verify tochigi`)。

方針(DESIGN.md 10.1):
  - 各自治体を自分自身の過去とだけ比べる。他自治体を基準にした正規化・順位・ピア群・z スコアは行わない。
  - 調整・除外はしない。未取得は「未取得」と書き、推定しない。結論的な文章は書かない。

指標(10.3):
  1  国庫支出金(決算カード)
  2  県支出金(決算カードの「都道府県支出金」)
  3  特別交付税(総務省報道発表): 3a 12月分 / 3b 3月分 / 3c 合計。町は報道発表に個別額がないため未取得
  4  社会資本整備総合交付金・防災・安全交付金のうち当該市町が単独で計画策定主体の計画の国費(当初配分)と件数
     (共同計画は自治体別内訳が公表されていないため含めない。参加件数は別に記録)
  5  国交省道路局 当初配分箇所表のうち事業主体が当該市町の箇所の国費と箇所数
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date

from . import settings as settings_mod
from .municipalities import master, normalize_name
from .prefs import name as pref_name

MISSING = "未取得"
UNDEF = "未定義"


@dataclass
class Indicator:
    key: str
    label: str
    source: str
    has_count: bool = False


INDICATORS = [
    Indicator("1", "国庫支出金(決算)", "総務省 決算カード"),
    Indicator("2", "県支出金(決算)", "総務省 決算カード"),
    Indicator("3a", "特別交付税 12月分", "総務省 報道発表"),
    Indicator("3b", "特別交付税 3月分", "総務省 報道発表"),
    Indicator("3c", "特別交付税 合計", "総務省 報道発表"),
    Indicator("4", "社総交・防安交 単独策定主体の国費", "国交省 配分資料(当初配分)", True),
    Indicator("5", "道路局箇所表 事業主体=当該市町の国費", "国交省 道路局 箇所表(当初配分)", True),
]
IND = {i.key: i for i in INDICATORS}


@dataclass
class Obs:
    value: float | None          # 千円。None = 未取得
    count: int | None = None
    reason: str = ""             # 未取得の理由
    items: dict[str, float] = field(default_factory=dict)   # 指標 4, 5 の事業別(事業名 → 千円)


# ---------------------------------------------------------------- データの組み立て

def _years(conn, sql, params=()):
    return {r[0] for r in conn.execute(sql, params)}


def build(conn: sqlite3.Connection, codes: list[str], period: tuple[int, int]) -> dict[tuple[str, str], dict[int, Obs]]:
    years = list(range(period[0], period[1] + 1))
    out: dict[tuple[str, str], dict[int, Obs]] = {}

    card_years = _years(conn, "SELECT DISTINCT fiscal_year FROM municipality_fiscal WHERE source='soumu_card'")
    for key, item in (("1", "国庫支出金"), ("2", "都道府県支出金")):
        vals = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT code, fiscal_year, value FROM municipality_fiscal WHERE source='soumu_card' AND item=?", (item,))}
        for c in codes:
            out[(c, key)] = {}
            for y in years:
                if y in card_years and vals.get((c, y)) is not None:
                    out[(c, key)][y] = Obs(vals[(c, y)])
                else:
                    out[(c, key)][y] = Obs(None, reason="決算カード未公表" if y not in card_years else "値なし")

    tk = {}
    for r in conn.execute("""SELECT municipality_code, fiscal_year, item_name, amount_thousand_yen FROM subsidy_allocations
                             WHERE program_id='soumu_tokko'"""):
        tk[(r[0], r[1], r[2])] = r[3]
    tk_years = {k[1] for k in tk}
    for key, item in (("3a", "12月交付額"), ("3b", "3月交付額"), ("3c", "交付総額")):
        yrs_item = {k[1] for k in tk if k[2] == item}
        for c in codes:
            out[(c, key)] = {}
            for y in years:
                v = tk.get((c, y, item))
                if v is not None:
                    out[(c, key)][y] = Obs(v)
                elif not _NAMES.get(c, "").endswith("市"):
                    out[(c, key)][y] = Obs(None, reason="報道発表に町の個別額なし")
                elif y not in yrs_item:
                    out[(c, key)][y] = Obs(None, reason="未発表" if y not in tk_years or y >= max(tk_years) else "資料未取得")
                else:
                    out[(c, key)][y] = Obs(None, reason="資料に記載なし")

    for key, progs in (("4", ("mlit_shasoukou", "mlit_bouan")), ("5", ("mlit_road",))):
        q = ",".join("?" * len(progs))
        yrs = _years(conn, f"SELECT DISTINCT fiscal_year FROM subsidy_allocations WHERE program_id IN ({q})", progs)
        items: dict[tuple[str, int], dict[str, float]] = {}
        for r in conn.execute(
                f"""SELECT municipality_code, fiscal_year, item_name, amount_thousand_yen FROM subsidy_allocations
                    WHERE program_id IN ({q}) AND attribution='sole'""", progs):
            d = items.setdefault((r[0], r[1]), {})
            name = r[2]
            while name in d:          # 同名の行が複数ある場合は区別して保持
                name += "＊"
            d[name] = r[3] or 0.0
        for c in codes:
            out[(c, key)] = {}
            for y in years:
                if y in yrs:
                    it = items.get((c, y), {})
                    out[(c, key)][y] = Obs(sum(it.values()), len(it), items=it)
                else:
                    out[(c, key)][y] = Obs(None, reason="配分資料未取得")
    return out


def population(conn, codes, period) -> dict[tuple[str, int], tuple[float, str]]:
    pop = {}
    for r in conn.execute("""SELECT code, fiscal_year, value, source FROM municipality_fiscal
                             WHERE item='住民基本台帳人口' AND value IS NOT NULL"""):
        if r[0] in codes and period[0] <= r[1] <= period[1]:
            pop.setdefault((r[0], r[1]), (r[2], r[3]))
    return pop


# ---------------------------------------------------------------- 計算

def direction(rate: float | None, threshold: float) -> str:
    if rate is None or (isinstance(rate, float) and math.isnan(rate)):
        return MISSING
    if rate >= threshold:
        return "増加"
    if rate <= -threshold:
        return "減少"
    return "横ばい"


def yoy(series: dict[int, Obs], y: int) -> tuple[float | None, float | None]:
    cur, prev = series.get(y), series.get(y - 1)
    if cur is None or prev is None or cur.value is None or prev.value is None:
        return None, None
    d = cur.value - prev.value
    return d, (d / prev.value if prev.value != 0 else None)


def max_decline(series: dict[int, Obs]) -> dict | None:
    vals = {y: o.value for y, o in series.items() if o.value is not None}
    if not vals:
        return None
    ymax = max(vals, key=lambda y: (vals[y], -y))
    ylast = max(vals)
    dec = vals[ymax] - vals[ylast]
    return {"max": vals[ymax], "max_year": ymax, "latest": vals[ylast], "latest_year": ylast,
            "decline": dec, "rate": (dec / vals[ymax]) if vals[ymax] else None}


_PERIOD_RE = re.compile(r"[（(]?(第[0-9０-９一二三四五六七八九十]+期|重点計画|重点)[）)]?|[0-9０-９]{4}$")


def _stem(name: str) -> str:
    return _PERIOD_RE.sub("", normalize_name(name))


def classify_items(prev: dict[str, float], cur: dict[str, float]) -> list[dict]:
    """事業名を前年度と突合して分類する。完全一致を優先し、残りは期・年号を除いた名称で突合(名称変更として注記)。"""
    out = []
    p_left, c_left = dict(prev), dict(cur)
    pairs = []
    for n in list(c_left):
        if n in p_left:
            pairs.append((n, n, ""))
            p_left.pop(n); c_left.pop(n)
    for n in list(c_left):
        cand = [m for m in p_left if _stem(m) == _stem(n)]
        if len(cand) == 1:
            pairs.append((cand[0], n, f"名称変更: {cand[0]} → {n}"))
            p_left.pop(cand[0]); c_left.pop(n)
    for pn, cn, note in pairs:
        a, b = prev[pn], cur[cn]
        cls = "継続(増額)" if b > a else "継続(減額)" if b < a else "継続(同額)"
        out.append({"item": cn, "prev": a, "cur": b, "class": cls, "note": note})
    for n, v in c_left.items():
        out.append({"item": n, "prev": None, "cur": v, "class": "新規", "note": ""})
    for n, v in p_left.items():
        out.append({"item": n, "prev": v, "cur": None, "class": "消滅", "note": ""})
    return out


@dataclass
class Result:
    codes: list[str]
    period: tuple[int, int]
    threshold: float
    obs: dict
    pop: dict
    first_diffs: dict      # (election_group, election_date, code, indicator_id) -> timeline.Row(投票日後最初の観測時点)
    names: dict
    item_rows: list[dict]  # 指標 4, 5 の事業別分類
    settings: dict


def compute(conn: sqlite3.Connection, pref_code: str = "09", cfg: dict | None = None) -> Result:
    global _NAMES
    cfg = cfg or settings_mod.load()
    v = cfg["verification"]
    period = tuple(v["period"])
    th = float(v["direction_threshold"])
    _NAMES = master(pref_code)
    codes = sorted(_NAMES)
    obs = build(conn, codes, period)
    pop = population(conn, codes, period)
    from . import timeline
    trows, _ = timeline.build(conn, pref_code, codes, list(FIRST_DIFF_INDICATORS), th)
    first = {}
    for g, d in conn.execute("""SELECT DISTINCT substr(election_id, 1, instr(election_id, '_smd_') - 1), election_date
                                FROM elections WHERE pref_code=? ORDER BY election_date DESC""", (pref_code,)):
        for c in codes:
            for ind in FIRST_DIFF_INDICATORS:
                first[(g, d, c, ind)] = timeline.first_after(trows, c, ind, d)
    item_rows = []
    for key in ("4", "5"):
        for c in codes:
            s = obs[(c, key)]
            for y in range(period[0] + 1, period[1] + 1):
                if s[y].value is None or s[y - 1].value is None:
                    continue
                for r in classify_items(s[y - 1].items, s[y].items):
                    item_rows.append({"indicator": key, "code": c, "name": _NAMES[c], "fiscal_year": y, **r})
    return Result(codes=codes, period=period, threshold=th, obs=obs, pop=pop, first_diffs=first, names=_NAMES,
                  item_rows=item_rows, settings=cfg)


FIRST_DIFF_INDICATORS = {"tokko_dec": "3a 特別交付税 12月分", "tokko_march": "3b 特別交付税 3月分",
                         "mlit_sole_grants": "4 社総交・防安交 単独策定主体", "mlit_road": "5 道路局箇所表 事業主体=市町"}
_NAMES: dict[str, str] = {}


# ---------------------------------------------------------------- 出力

def _n(v, unit=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return MISSING
    return f"{v:,.0f}{unit}"


def _pct(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return UNDEF
    return f"{v * 100:+.1f}%"


def _t(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def sources_table(conn) -> list[list[str]]:
    rows = []
    q = [
        ("決算カード(指標 1, 2、参考人口 FY2020–2024)", "SELECT DISTINCT fiscal_year, source_url, retrieved_at FROM municipality_fiscal WHERE source='soumu_card' AND source_url IS NOT NULL"),
        ("住民基本台帳人口(参考人口 FY2025)", "SELECT DISTINCT fiscal_year, source_url, retrieved_at FROM municipality_fiscal WHERE source='soumu_jumin'"),
        ("特別交付税 報道発表(指標 3)", "SELECT DISTINCT fiscal_year || ' ' || CASE WHEN item_name='12月交付額' THEN '12月' ELSE '3月' END, source_url, retrieved_at FROM subsidy_allocations WHERE program_id='soumu_tokko'"),
        ("国交省 事業実施箇所 都道府県別 PDF(指標 4, 5)", "SELECT DISTINCT fiscal_year, source_url, retrieved_at FROM subsidy_allocations WHERE program_id IN ('mlit_shasoukou','mlit_bouan','mlit_road')"),
    ]
    for label, sql in q:
        for r in sorted(set(tuple(x) for x in conn.execute(sql).fetchall())):
            rows.append([label, str(r[0]), r[1] or "", (r[2] or "")[:10]])
    return rows


def render(conn: sqlite3.Connection, res: Result, title_pref: str | None = None) -> str:
    TOCHIGI = res.names  # noqa: N806  (表示名の辞書。名前は互換のため)
    title_pref = title_pref or pref_name(res.codes[0][:2])
    th = res.threshold
    years = list(range(res.period[0], res.period[1] + 1))
    v = res.settings["verification"]
    L = [f"# {title_pref} 全市町の補助金・交付金の時系列記録(DESIGN.md 第10節)", "",
         f"- 対象: {len(res.codes)} 市町 / 対象年度: {res.period[0]}–{res.period[1]}(西暦の会計年度)",
         f"- 増減方向の閾値: ±{th * 100:.1f}%(設定ファイル: `{res.settings['_path']}`)",
         "- 選挙との関係は、投票日の直後に来る最初の観測時点の「直前時点比の差分」で示す(第4節。平均との比較はしない)。"
         "観測時点ごとの差分と政局イベントの対応は data/processed/timeline_<都道府県コード>/ を参照。",
         "- 各市町は自分自身の過去の値とだけ比べている。他市町を基準にした正規化・順位・z スコアは算出していない。",
         "- 金額の単位は千円。未取得は推定せず「未取得」、前年値が 0 または未取得の変化率は「未定義」。", ""]

    # 出典
    L += ["## 1. 出典と取得日", "", _t(["データ", "年度", "URL", "取得日"], sources_table(conn)), ""]

    # 年度表
    L += ["## 2. 年度表(全 25 市町 × 指標)", "",
          "各セル: 金額 / 前年差額 / 前年比変化率 / 方向。指標 4・5 は金額の後に件数(件)。参考人口は住民基本台帳人口"
          "(決算カードと同じく年度内の 1 月 1 日現在。FY2025 は総務省の令和8年1月1日現在、FY2026 は未取得)。", ""]
    for c in res.codes:
        L += [f"### {TOCHIGI[c]}({c})", ""]
        rows = []
        for ind in INDICATORS:
            s = res.obs[(c, ind.key)]
            row = [f"{ind.key} {ind.label}"]
            for y in years:
                o = s[y]
                if o.value is None:
                    row.append(f"{MISSING}({o.reason})" if o.reason else MISSING)
                    continue
                d, g = yoy(s, y)
                cnt = f" ({o.count}件)" if ind.has_count else ""
                cell = f"{_n(o.value)}{cnt}"
                if y > res.period[0] or (y - 1) in s:
                    cell += f"<br>{_n(d) if d is not None else UNDEF} / {_pct(g)} / {direction(g, th)}"
                row.append(cell)
            rows.append(row)
        rows.append(["参考: 住民基本台帳人口"] + [_n(res.pop[(c, y)][0], "人") if (c, y) in res.pop else MISSING for y in years])
        L += [_t(["指標"] + [str(y) for y in years], rows), ""]

    # 最大値からの減少
    L += ["## 3. 対象期間内の最大値からの減少", "",
          "最大値とその年度、最新年度(値が取得できた最後の年度)の値、最大値からの減少額・減少率。", ""]
    rows = []
    for c in res.codes:
        for ind in INDICATORS:
            m = max_decline(res.obs[(c, ind.key)])
            if m is None:
                rows.append([TOCHIGI[c], f"{ind.key} {ind.label}", MISSING, "", "", "", "", ""])
            else:
                rows.append([TOCHIGI[c], f"{ind.key} {ind.label}", _n(m["max"]), m["max_year"], _n(m["latest"]),
                             m["latest_year"], _n(m["decline"]), _pct(-m["rate"] if m["rate"] else 0.0) if m["rate"] is not None else UNDEF])
    L += [_t(["市町", "指標", "最大値", "最大の年度", "最新値", "最新年度", "最大値からの減少額", "最大値からの変化率"], rows), ""]

    # 選挙後最初の差分
    L += ["## 4. 選挙後最初の差分(指標 3〜5)", "",
          "投票日の直後に来る最初の観測時点(decided_date が投票日より後)の値と、その直前の観測時点の値の差。"
          "指標 3 は 12 月分・3 月分をそれぞれの系列として扱う(12 月分の直前は前年度の 12 月分)。", ""]
    elections = sorted({(k[0], k[1]) for k in res.first_diffs}, key=lambda x: x[1], reverse=True)
    for g, d in elections:
        L += [f"### {g}(投票日 {d})", ""]
        rows, cnt = [], {}
        for c in res.codes:
            for ind, lab in FIRST_DIFF_INDICATORS.items():
                r = res.first_diffs[(g, d, c, ind)]
                if r is None:
                    rows.append([TOCHIGI[c], lab, "—", "—", "—", "—", "—", "—", f"{MISSING}(投票日後の観測時点なし)"])
                    dirn = MISSING
                else:
                    prev = None if r.delta is None else r.value - r.delta
                    val = _n(r.value) if r.value is not None else f"{MISSING}({r.missing_reason})"
                    rows.append([TOCHIGI[c], lab, f"{r.period_start[:4]}年度分", (r.decided_date or "") + ("(代用)" if r.decided_date_is_proxy else ""),
                                 _n(prev) if prev is not None else MISSING, val, _n(r.delta) if r.delta is not None else UNDEF,
                                 _pct(r.delta_pct), r.direction])
                    dirn = r.direction
                cnt.setdefault(ind, {}).setdefault(dirn, 0)
                cnt[ind][dirn] += 1
        L += [_t(["市町", "指標", "観測時点", "decided_date", "直前の値", "値", "差額", "変化率", "方向"], rows), ""]
        L += ["方向別の市町数(各市町を自分の直前の観測時点と比べた結果を数えたもの):", "",
              _t(["指標", "増加", "横ばい", "減少", MISSING],
                 [[FIRST_DIFF_INDICATORS[i]] + [cnt.get(i, {}).get(k, 0) for k in ("増加", "横ばい", "減少", MISSING)]
                  for i in FIRST_DIFF_INDICATORS]), ""]

    # 事業別
    L += ["## 5. 事業別の分類(指標 4・5、前年度との事業名突合)", "",
          "事業名が前年度と完全一致すれば継続。完全一致しない場合は「第N期」「重点計画」・末尾の西暦を除いた名称が"
          "1 対 1 で一致すれば継続とみなし、備考に名称変更を記す。指標 5 の事業名は「工種|事業名(箇所)」。", ""]
    for key in ("4", "5"):
        rows_k = [r for r in res.item_rows if r["indicator"] == key]
        summ = {}
        for r in rows_k:
            summ.setdefault((r["code"], r["fiscal_year"]), {}).setdefault(r["class"], 0)
            summ[(r["code"], r["fiscal_year"])][r["class"]] += 1
        L += [f"### 指標 {key} {IND[key].label}", "", "#### 分類の件数(市町 × 年度)", ""]
        classes = ["継続(増額)", "継続(減額)", "継続(同額)", "新規", "消滅"]
        rows = [[TOCHIGI[c], y] + [summ[(c, y)].get(k, 0) for k in classes]
                for (c, y) in sorted(summ)]
        L += [_t(["市町", "年度"] + classes, rows), ""]
        L += ["#### 継続(減額)と消滅の個別一覧", ""]
        rows = [[r["name"], r["fiscal_year"], r["item"], _n(r["prev"]), _n(r["cur"]) if r["cur"] is not None else "—",
                 r["class"], r["note"]] for r in rows_k if r["class"] in ("継続(減額)", "消滅")]
        L += [_t(["市町", "年度", "事業名", "前年度額", "当年度額", "分類", "備考"], rows) if rows else "該当なし", ""]
        L += ["#### 全事業の一覧", ""]
        rows = [[r["name"], r["fiscal_year"], r["item"], _n(r["prev"]) if r["prev"] is not None else "—",
                 _n(r["cur"]) if r["cur"] is not None else "—", r["class"], r["note"]] for r in rows_k]
        L += [_t(["市町", "年度", "事業名", "前年度額", "当年度額", "分類", "備考"], rows) if rows else "該当なし", ""]

    # 注記
    L += ["## 6. 注記欄(既知の要因。調整・除外はしていない)", ""]
    L += notes(conn, res)
    return "\n".join(L) + "\n"


def notes(conn, res: Result) -> list[str]:
    TOCHIGI = res.names  # noqa: N806
    L = []
    # 災害復旧
    rows = [[TOCHIGI[r[0]], r[1], _n(r[2])] for r in conn.execute(
        """SELECT code, fiscal_year, value FROM municipality_fiscal WHERE source='soumu_card' AND item='災害復旧事業費'
           AND value IS NOT NULL AND value > 0 AND fiscal_year BETWEEN ? AND ? ORDER BY code, fiscal_year""",
        res.period) if r[0] in res.codes]
    L += ["### 災害復旧", "", "決算カードの性質別歳出「災害復旧事業費」が計上されている市町・年度(千円):", "",
          _t(["市町", "年度", "災害復旧事業費"], rows) if rows else "該当なし", ""]
    names = [r for r in res.item_rows if re.search(r"災害|復旧", r["item"])]
    L += ["指標 4・5 の事業名に「災害」「復旧」を含むもの: " +
          ("、".join(sorted({f"{r['name']} {r['item']}" for r in names})) if names else "該当なし"), ""]
    # 大規模事業
    L += ["### 大規模事業の完了・着工", "",
          "資料上は事業の完了と配分の終了を区別できない。指標 4・5 で「消滅」に分類された事業は上の個別一覧のとおり。"
          "決算カードの普通建設事業費(千円):", ""]
    rows = []
    vals = {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT code, fiscal_year, value FROM municipality_fiscal WHERE source='soumu_card' AND item='普通建設事業費'")}
    yrs = sorted({y for (_, y) in vals if res.period[0] <= y <= res.period[1]})
    for c in res.codes:
        rows.append([TOCHIGI[c]] + [_n(vals.get((c, y))) for y in yrs])
    L += [_t(["市町"] + [str(y) for y in yrs], rows), ""]
    # 事業主体の記載がない道路局の行(指標 5 に含めていない)
    rows = []
    for r in conn.execute("""SELECT fiscal_year, item_name, recipient_codes, amount_thousand_yen, raw_ref FROM subsidy_allocations
                             WHERE program_id='mlit_road' AND attribution='unknown' ORDER BY fiscal_year""").fetchall():
        cs = [c for c in (r[2] or "").split(",") if c in res.codes]
        if cs:
            route = next((x.split("=", 1)[1] for x in (r[4] or "").split(" ") if x.startswith("route=")), "")
            rows.append(["、".join(TOCHIGI[c] for c in cs), r[0], r[1].replace("|", " / "), route, _n(r[3])])
    L += ["### 道路局箇所表のうち事業主体の記載がなく指標 5 に含めていない行(所在地に当該市町を含むもの、千円)", "",
          _t(["所在市町", "年度", "工種 / 事業名(箇所)", "路線名", "事業費"], rows) if rows else "該当なし", ""]
    # 報道された数値(本システムの指標とは集計範囲が異なる)
    L += ["### 報道された国交省の数値(本システムの指標とは集計範囲が異なる)", "",
          "- 時事ドットコム 2026-10-06「対立候補支援の２市町、道路予算が最大５４％減　簗氏「カット」発言、圧力なし―国交省」"
          " https://www.jiji.com/jc/article?k=2026100600835&g=eco : 今年度の道路関連予算が「那須烏山市が前年度比２６．２％減、"
          "那珂川町が同５４．１％減だった」。",
          "- 本記録の指標 5(道路局箇所表のうち事業主体が当該市町の箇所、当初配分)の 2025→2026 年度の前年比は上の年度表のとおりで、"
          "報道の数値とは一致しない。共同計画(社総交・防安交)の市町別内訳など、公表 PDF にない配分が報道の集計に含まれている可能性があるが、"
          "資料からは確認できない。", ""]
    # 合併
    card_codes = {}
    for r in conn.execute("SELECT fiscal_year, COUNT(DISTINCT code) FROM municipality_fiscal WHERE source='soumu_card' GROUP BY 1"):
        card_codes[r[0]] = r[1]
    L += ["### 合併", "",
          "決算カードの団体数(年度別): " + "、".join(f"{y}: {n}" for y, n in sorted(card_codes.items())) +
          "。対象期間内に団体コードの増減は資料上確認されない。", ""]
    # 開票区の分割
    split = conn.execute("""SELECT election_id, counting_unit FROM election_results
                            WHERE counting_unit GLOB '*第[0-9１-９]*' GROUP BY 1, 2""").fetchall()
    if split:
        L += ["### その他(資料の構造)", "",
              "- 特別交付税の報道発表は町の個別額を載せていないため、町(11 町)の指標 3 は全年度未取得。",
              "- 国交省の共同計画(計画策定主体が複数)は自治体別内訳がないため指標 4 に含めていない。",
              "- 道路局箇所表のうち国道・県道・都市計画道路の補助事業は事業主体の記載がなく、指標 5 に含めていない"
              "(路線名が「(市)」「(町)」の行は道路管理者である当該市町を事業主体とした)。",
              "- 令和7年度(FY2025)の栃木県 PDF の道路局セクションには地方創生道整備推進交付金(市町村道分)の表がない(栃木県で確認)。", ""]
    return L


def run(conn: sqlite3.Connection, out_path, pref_code: str = "09") -> Result:
    res = compute(conn, pref_code)
    from pathlib import Path
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(render(conn, res), encoding="utf-8")
    return res
