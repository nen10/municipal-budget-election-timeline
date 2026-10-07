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
.venv/bin/findnews db init          # data/processed/findnews.sqlite を作成(第4節の全テーブル + fetch_log)
.venv/bin/findnews manual load      # data/manual/ の手作業データを投入
```

スキーマを変更した場合は `data/processed/findnews.sqlite` を削除して作り直す(`fetch ... --offline` で data/raw から再構築できる)。

## コマンド

| コマンド | 内容 |
|---|---|
| `findnews fetch soumu-card` | 総務省 市町村決算カード(栃木県、直近 5 年度) |
| `findnews fetch soumu-tokko` | 総務省 特別交付税 報道発表(12 月分・3 月分、2020–2025 年度) |
| `findnews fetch mlit-grants` | 国交省 社会資本整備総合交付金・防災・安全交付金 当初配分(2021–2026 年度) |
| `findnews fetch mlit-road` | 国交省 道路局 当初配分箇所表(同じ PDF の道路局セクション) |
| `findnews fetch soumu-jumin` | 総務省 住民基本台帳人口(最新年、参考人口列用) |
| `findnews fetch tochigi-election` | 栃木県選管 衆院選 2026-02-08 / 2024-10-27 / 2021-10-31 の開票区別得票と候補者届出状況公表票 |
| `findnews fetch kokkai` | 国会会議録検索システム API(DESIGN.md 5.3 のキーワード) |
| `findnews fetch all` | 上記をすべて |
| `findnews verify tochigi` | 第10節: 全 25 市町の時系列記録 → `data/processed/verification_tochigi.md` |
| `findnews matrix --pref 09 --election shugiin_20260208` | 第11節: 分離マトリックス → `data/processed/matrix_tochigi_<選挙ID>.md/.csv` |
| `findnews detect run --pref 09 [--election ID]` | 自治体自身の減少・発言一致・寄与内訳つきスコア → `data/processed/reports/<run_id>/` |
| `findnews status` | テーブル件数と fetch_log の最新状態 |

すべての `fetch` は「URL 一覧 → data/raw/<source>/ に保存(manifest.jsonl に URL・取得日時・SHA-256)→ パース → DB 投入」の順で、
`--offline` を付けると保存済みファイルだけをパースする。外部アクセスは同一ホストに 1 秒以上の間隔(国会会議録 API は約 3 秒)、
User-Agent は `findnews-research/0.1 (...)`(`FINDNEWS_USER_AGENT` で変更可)。

増減方向の閾値(既定 ±5%)、対象年度、選挙前後の年度は `config/settings.yaml` で変更できる。

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
| `cases.yaml` | 第6節の既知事例 5 件 | 簗氏の件のみ一部出典確認済。他 4 件は設計書の記述の転記で出典未確認 |
| `requests.csv` | 要望と採択 | 雛形のみ |

報道サイトについて: 下野新聞(shimotsuke.co.jp)の robots.txt は AI エージェント(Claude-User 等)を拒否しているため、
本システムは同サイトをクロールしない。`endorsements.csv` の同紙由来の行は手作業の引用で、公開前に人が原文を再確認すること。

## 出力

- `data/processed/verification_tochigi.md`: 全 25 市町 × 指標 1〜5 の年度表(金額・前年差額・変化率・方向・参考人口)、最大値からの減少、選挙前後、事業別の継続/新規/消滅、出典と取得日、注記
- `data/processed/matrix_tochigi_<選挙ID>.md` / `.csv`: A1(首長の支持)・A2(自治体内得票)・A3(議員の役職)× 指標 3〜5 の方向
- `data/processed/reports/<run_id>/report.md`, `signals.csv`: 寄与内訳つきスコア(自身の減少 0.6・発言 0.3・権限 0.1・反転 0.0)

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
  fetch/     ソースごとの取得(soumu_card, soumu_tokko, mlit_grants, mlit_road, soumu_jumin, tochigi_election, kokkai)
  parse/     PDF/Excel のパーサー(ネットワーク非依存)
  detect/    パネル、スコア、レポート
  verify.py  第10節、matrix.py 第11節、manual.py 手作業データ、cli.py
config/settings.yaml
data/raw/ (取得物、版管理外)  data/manual/  data/processed/
tests/
```
