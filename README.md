# find-news: 補助金圧力検出システム(第1フェーズ: 栃木県パイロット)

公開データから、国会議員が補助金・交付金の配分に影響した疑いを人が検証するための **要検証シグナル** を作る。
出力は疑惑の断定ではない。設計は [DESIGN.md](DESIGN.md)。

- 各自治体を **自分自身の過去とだけ** 比べる(DESIGN.md 5.1・第10節)。他自治体を基準にした正規化・順位・ピア・z スコアは使わない。
- 取得できなかった値は「未取得」のまま。推定値で埋めない。
- 政党名・候補者名・首長名・役職・金額・日付は省略しない(DESIGN.md 11.6)。発言は原文を引用として保存する。

## セットアップ

```sh
python3.13 -m venv .venv            # Python 3.11 以上
.venv/bin/pip install -e '.[dev]'
.venv/bin/findnews db init          # data/processed/findnews.sqlite を作成(第4節の全テーブル + observations, events, fetch_log)
.venv/bin/findnews manual load      # data/manual/ の手作業データを投入
```

スキーマを変更した場合は `data/processed/findnews.sqlite` を削除して作り直す(`fetch --offline` で data/raw から再構築できる)。

## コマンド(DESIGN.md 14.3)

| コマンド | 内容 |
|---|---|
| `findnews fetch --pref 09 --years 2020-2026 [--source <id> ...] [--offline] [--force]` | 取得 → data/raw 保存 → パース → DB と `observations` に投入。`--source` 省略時は全部 |
| `findnews sources list [--pref <2桁>]` | 登録済みアダプタと、その都道府県での対応状況(未対応はその旨を表示)、指標 ID の一覧 |
| `findnews events generate --pref 09` | 衆院選投票・役職就任・内閣発足・予算配分公表・特別交付税交付決定のイベントを自動登録 |
| `findnews events import [data/manual/events.csv]` | 報道由来などの手作業イベントを登録(出典 URL のない行は登録しない) |
| `findnews timeline --pref 09 [--muni <6桁>] [--indicator <id>]` | 第13節: 差分時系列とイベントの対応 → `timeline_09.csv`、`timeline_09/<自治体>.md`、`events_09.md` |
| `findnews verify --pref 09`(別名 `verify tochigi`) | 第10節: 全市町の時系列記録 → `verification_tochigi.md` |
| `findnews matrix --pref 09 --election shugiin_20260208` | 第11節: 分離マトリックス → `matrix_tochigi_<選挙ID>.md/.csv` |
| `findnews detect run --pref 09 [--election ID]` | 自治体自身の差分・発言一致・寄与内訳つきスコア → `reports/<run_id>/` |
| `findnews status` | テーブル件数と fetch_log の最新状態 |

ソース ID: `soumu_jumin`(全国の市区町村マスタと人口。他県では最初に実行)、`soumu_card`、`soumu_tokko`、`mlit_grants`、
`mlit_road`、`election`(都道府県の選管アダプタ)、`kokkai`。

外部アクセスは同一ホストに 1 秒以上の間隔(国会会議録 API は約 3 秒)、User-Agent は `findnews-research/0.1 (...)`
(`FINDNEWS_USER_AGENT` で変更可)。取得物は data/raw/<source>/ に保存し、manifest.jsonl に URL・取得日時・SHA-256 を残す。

### 差分時系列(DESIGN.md 第13節)

平均との比較はしない。各自治体 × 指標について、観測時点ごとの値と直前の観測時点との差分(Δ、変化率、方向)を並べる。

| 指標 ID | 指標 | 観測時点 | decided_date |
|---|---|---|---|
| `card_kokko` / `card_pref` | 1 国庫支出金 / 2 県支出金(決算) | 年度 | 年度末(3/31)。公表日は未収集 |
| `tokko_dec` / `tokko_march` | 3 特別交付税 12 月分 / 3 月分 | 年度ごとの 12 月分・3 月分(別系列) | 交付決定日(報道発表日) |
| `mlit_sole_grants` | 4 社総交・防安交(単独策定主体) | 当初配分の回 | 当初配分の報道発表日を代用(`decided_date_is_proxy=1`) |
| `mlit_road` | 5 道路局箇所表(事業主体=市町) | 当初配分の回 | 同上 |

差分の対応期間は「直前の観測時点の decided_date の翌日 〜 当該観測時点の decided_date」で、日付が重なるイベントを対応付ける
(月単位・期間のイベントは重なれば対応)。対応は日付の機械的な重なりで、関係を示すものではない。
マトリックスの軸 B と detect のスコアは「投票日の直後に来る最初の観測時点の直前時点比」を使う。

増減方向の閾値(既定 ±5%)と対象年度は `config/settings.yaml`。発言キーワードと由来は `config/keywords.yaml`
(由来事例のあるキーワードがその事例の議員・自治体の発言に一致した場合は「由来事例のため独立検証にならない」と注記し、スコアに数えない)。

## 新しい都道府県を追加する手順(DESIGN.md 14.4)

1. `findnews fetch --pref <code> --source soumu_jumin` で全国の市区町村マスタを入れる(他県の自治体コード・名称はここから取る)。
2. `findnews fetch --pref <code> --years 2021-2026` で全国対応ソース(決算カード、特別交付税、国交省配分・道路局箇所表)を取得する。
   `findnews sources list --pref <code>` で対応状況を確認する。
3. その県の選管アダプタを `src/findnews/fetch/elections/pref<code>_<name>.py` に実装し(`ElectionSource` プロトコル:
   `pref_code`、`list_elections()`、`fetch()`、`parse()`、`run()`)、`src/findnews/sources/registry.py` の `_election_sources()` に登録する。
   選管の公表形式は県ごとに違うため、既存の栃木県アダプタ(Excel + 候補者届出状況公表票 PDF)はそのまま使えない。
4. `data/manual/endorsements.csv`、`events.csv`、`positions.csv`、`election_outcomes.csv` にその県の行を出典つきで追加する。
5. `findnews events generate --pref <code>`、`findnews events import`、`findnews timeline --pref <code>`、
   `findnews matrix --pref <code> --election <ID>`、`findnews verify --pref <code>` を実行する。
6. 出力の冒頭の未対応・未収集・未取得の件数を確認する。未対応のソースはエラーにならず「未対応」と出る。

出力の CSV(`timeline_<pref>.csv`、`matrix_*.csv`)はすべて `pref_code` と `municipality_code` を持つ長形式で、県をまたいで縦に結合できる。

## 取得・パースの状況(2026-10-08 時点)

| ソース | 状態 | 取得範囲 | 備考 |
|---|---|---|---|
| 総務省 決算カード | 成功 | FY2020–2024 × 25 市町 | [card.html](https://www.soumu.go.jp/iken/zaisei/card.html) → 年度ページ → 栃木県 xlsx。ラベル探索で読む。FY2025(選挙を含む年度)は未公表 |
| 総務省 特別交付税 報道発表 | 一部 | FY2020–2025 × **14 市のみ** | 月別報道資料一覧から「令和N年度特別交付税…交付額の決定」を探す。**町村は都道府県計しか載っておらず、11 町は個別額を取得できない**。市の交付総額は決算カードの特別交付税と 70/70 一致(検算)。総務省「地方交付税」ページの「特別交付税3月算定分」リンクは算定方法に関する意見処理の資料で交付額ではない |
| 国交省 社総交・防安交 当初配分 | 成功(帰属に制約) | FY2021–2026 | 「国土交通省関係予算の配分について」→「事業実施箇所」→ 栃木県 PDF 末尾の配分表。行の合計は表の「合計」と全年度で一致。**共同計画(策定主体が複数)は自治体別の内訳がなく帰属させない**。依頼文の https://www.mlit.go.jp/sogoseisaku/region/ には一覧がなかった。補正予算分は未取得 |
| 国交省 道路局 箇所表 | 成功(帰属に制約) | FY2021–2026 | 同じ PDF の道路局セクション。事業主体の列がある表(道路メンテナンス、通学路、無電柱化、踏切)と、路線名が(市)(町)の市町村道は当該市町に帰属。国道・県道・都市計画道路の補助事業は事業主体の記載がなく帰属させない。FY2025 の PDF には地方創生道整備推進交付金の表がない |
| 総務省 住民基本台帳人口 | 成功 | 令和8年1月1日現在(=FY2025) | FY2021–2024 は決算カードの値。FY2026 は未存在 |
| 栃木県選管 衆院選 | 成功 | 2026-02-08、2024-10-27、2021-10-31 | 確定値は Excel(.xls)と HTML で公表され、**PDF は見当たらなかった**ため `AS_KAIHYO_K_10_1.xls` を読む。投票日は表題「令和N年M月D日執行」から登録。候補者の党派・政党届出/本人届出・新前元・重複立候補は「候補者届出状況公表票」PDF から(全行突合済) |
| 国会会議録検索 API | 成功 | 2015-01-01 以降のキーワード検索 + 登録議員の発言 | 本文に完全一致したものだけ保存。登録議員(簗和生氏ほか)のキーワード一致はなし |
| 比例復活 | 手作業(出典つき) | 2026 年のみ | 時事ドットコム開票結果(栃木選挙区)の「比」表示。総務省・県選管の比例当選人一覧は見つからなかった |

`findnews status` と DB の `fetch_log` に、各段階の成否と件数が記録される。

## 手作業データ(data/manual/)

| ファイル | 内容 | 現状 |
|---|---|---|
| `endorsements.csv` | 首長の支持表明(DESIGN.md 11.1 A1 の列) | 2026 年衆院選の 25 市町分の行。**収集済 6(3 区の大田原・矢板・那須塩原・那須烏山・那須町・那珂川町、下野新聞 2026-10-04 無料公開記事)、未収集 19**。首長の党派は全件未収集 |
| `election_outcomes.csv` | 比例復活(出典 URL つきの行だけ DB に反映) | 2026 年の重複立候補した落選者 8 人 |
| `politicians.csv` | 議員と表記揺れ | 2026 年の栃木の当選者・比例復活者 7 人 |
| `positions.csv` | 政府・党の役職 | 官邸名簿で確認した 3 件(簗和生 農林水産大臣 2026-09-17、茂木敏充 外務大臣 2026-02-18・2026-09-17)。党役職は未収集 |
| `statements.csv` | 報道された発言(原文、発言者・場・日付・出典) | 1 件(新潮QUE 2026-09-30) |
| `subsidy_programs.csv` | 補助事業マスタ | 裁量性の評価は未実施(空欄) |
| `events.csv` | 政局イベント(DESIGN.md 13.3) | 10 件: 簗氏から国交副大臣・官房長への問い合わせ(時事通信 2026-10-06、月まで、那須烏山市・那珂川町)、簗氏の発言(新潮QUE 2026-09-30、2026-05-24。場は出典どおり「自民党大田原支部総会後の懇親会」で、設計書第1節の「県連会合」とは記載が異なる)、首長の支持表明 6 件(日付は出典になく選挙期間として記録) |
| `cases.yaml` | 第6節の既知事例 5 件 | 簗氏の件のみ一部出典確認済。他 4 件は設計書の記述の転記で出典未確認 |
| `requests.csv` | 要望と採択 | 雛形のみ |

報道サイトについて: 下野新聞(shimotsuke.co.jp)の robots.txt は AI エージェント(Claude-User 等)を拒否しているため、
本システムは同サイトをクロールしない。`endorsements.csv` の同紙由来の行は手作業の引用で、公開前に人が原文を再確認すること。

## 出力

- `data/processed/timeline_09.csv` / `timeline_09/<自治体コード>.md` / `events_09.md`: 差分時系列と政局イベント(第13節)

- `data/processed/verification_tochigi.md`: 全 25 市町 × 指標 1〜5 の年度表(金額・前年差額・変化率・方向・参考人口)、最大値からの減少、選挙後最初の差分(3 選挙)、事業別の継続/新規/消滅、出典と取得日、注記
- `data/processed/matrix_tochigi_<選挙ID>.md` / `.csv`: A1(首長の支持)・A2(自治体内得票)・A3(議員の役職)× 指標 3〜5 の投票日後最初の差分の方向
- `data/processed/reports/<run_id>/report.md`, `signals.csv`: 寄与内訳つきスコア(投票日後最初の差分の減少 0.6・発言 0.3・権限 0.1・反転 0.0)

## テスト

```sh
.venv/bin/pytest -q
```

`tests/fixtures/` には実データから切り出した小さなファイル(決算カード 2 シート、特別交付税 PDF 2 ページ、国交省 PDF 数ページ、
県選管 xls、候補者届出状況公表票、国会会議録 API 応答 2 件)を置き、ネットワークなしでパーサーと全体の流れを検査する。
出典はいずれも官公庁の公開資料(政府標準利用規約等)と国会会議録。

## ディレクトリ

```
src/findnews/
  db/        schema.sql、接続・初期化
  sources/   アダプタのプロトコル(IndicatorSource / ElectionSource)、指標アダプタ、レジストリ
  fetch/     ソースごとの取得(soumu_card, soumu_tokko, mlit_grants, mlit_road, soumu_jumin, kokkai, tochigi_election)
  fetch/elections/  都道府県選管アダプタ(pref09_tochigi.py)
  parse/     PDF/Excel のパーサー(ネットワーク非依存)
  detect/    パネル、スコア、レポート
  pipeline.py fetch 本体、timeline.py 第13節、events.py イベント、verify.py 第10節、matrix.py 第11節、
  prefs.py 都道府県表、municipalities.py 市区町村マスタ、manual.py 手作業データ、cli.py
config/settings.yaml
data/raw/ (取得物、版管理外)  data/manual/  data/processed/
tests/
```
