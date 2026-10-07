-- 補助金圧力検出システム スキーマ(設計書 第4節)
-- 全テーブルに source_url と retrieved_at を持たせ、根拠に遡れるようにする。
-- 金額の単位は原則「千円」(amount_thousand_yen)。原資料が百万円単位のものは取込時に千円へ換算し、
-- 元の単位を unit_original に残す。
-- 取得できなかった値は NULL のまま残す(推定値で埋めない)。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS municipalities (
    code            TEXT PRIMARY KEY,          -- 全国地方公共団体コード 6 桁(検査数字込み)
    name            TEXT NOT NULL,
    pref_code       TEXT NOT NULL,             -- 2 桁
    pref_name       TEXT,
    kind            TEXT,                      -- 市 / 町 / 村
    population      INTEGER,                   -- 最新の住民基本台帳人口(年度別は municipality_fiscal)
    population_date TEXT,
    fiscal_capability_index REAL,              -- 最新の財政力指数(年度別は municipality_fiscal)
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS municipality_fiscal (
    code            TEXT NOT NULL REFERENCES municipalities(code),
    fiscal_year     INTEGER NOT NULL,          -- 西暦の年度(令和6年度 = 2024)
    item            TEXT NOT NULL,             -- 例: 国庫支出金, 財政力指数, 住民基本台帳人口
    value           REAL,                      -- 欠損・「-」は NULL
    unit            TEXT,                      -- 千円 / 人 / 指数 など
    source          TEXT NOT NULL,             -- 例: soumu_card
    source_url      TEXT,
    retrieved_at    TEXT,
    PRIMARY KEY (code, fiscal_year, item, source)
);

CREATE TABLE IF NOT EXISTS politicians (
    politician_id   TEXT PRIMARY KEY,          -- 例: yana_kazuo
    name            TEXT NOT NULL,
    name_variants   TEXT,                      -- 「|」区切り(会議録・選挙資料の表記揺れ)
    party           TEXT,
    note            TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    politician_id   TEXT NOT NULL REFERENCES politicians(politician_id),
    title           TEXT NOT NULL,             -- 役職名
    organization    TEXT,                      -- 内閣 / 党 / 国会 など
    ministry        TEXT,                      -- 所管省庁
    start_date      TEXT,                      -- ISO 形式(日が不明なら YYYY-MM)
    end_date        TEXT,
    verification    TEXT,                      -- verified / unverified
    note            TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS elections (
    election_id     TEXT PRIMARY KEY,          -- 例: shugiin_20260208_smd_09_3(選挙単位のまとめは shugiin_20260208)
    kind            TEXT NOT NULL,             -- shugiin_smd / shugiin_pr など
    election_date   TEXT NOT NULL,
    district        TEXT,                      -- 例: 栃木県第3区
    pref_code       TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS election_results (
    election_id     TEXT NOT NULL REFERENCES elections(election_id),
    municipality_code TEXT,                    -- 開票区から解決したコード(解決不能なら NULL)
    counting_unit   TEXT NOT NULL,             -- 原資料の開票区名(例: 宇都宮市第１)
    candidate_name  TEXT NOT NULL,             -- 投票用紙・開票結果の表記(通称)
    candidate_legal_name TEXT,                 -- 戸籍名(候補者届出状況公表票)
    politician_id   TEXT REFERENCES politicians(politician_id),
    party           TEXT,                      -- 所属政党(届出政党・所属団体。無所属は「無所属」)
    filing_type     TEXT,                      -- 政党届出 / 本人届出 / 推薦届出
    nomination      TEXT,                      -- 公認・推薦の表示(例: 自由民主党公認 / 無所属)。他党推薦は出典がある場合のみ追記
    recommending_parties TEXT,                 -- 他党の推薦(出典のあるもののみ。未確認は NULL)
    incumbency      TEXT,                      -- 新 / 前 / 元
    dual_candidacy  INTEGER,                   -- 比例重複立候補(1/0)
    votes           REAL,
    vote_share      REAL,                      -- 自治体(選挙区内の部分)内の得票率 = 得票 ÷ 候補者得票の合計(有効投票)
    rank_in_municipality INTEGER,              -- 自治体(選挙区内の部分)内の順位
    is_district_winner INTEGER,                -- 選挙区全体での当選(1/0)
    pr_revived      INTEGER,                   -- 比例復活(1/0/NULL=未確認)。重複立候補なしの落選者は 0
    pr_revived_source_url TEXT,
    sekihai_rate    REAL,                      -- 惜敗率(%)
    result_label    TEXT,                      -- 選挙区当選 / 落選・比例復活 / 落選 / 落選(比例復活未確認)
    source_url      TEXT,
    retrieved_at    TEXT,
    PRIMARY KEY (election_id, counting_unit, candidate_name)
);

CREATE TABLE IF NOT EXISTS endorsements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    municipality_code TEXT,
    election_id     TEXT,                      -- 選挙 ID(shugiin_20260208 など、選挙単位)
    endorser_role   TEXT,                      -- 首長 / 地方議員 / 団体
    mayor_name      TEXT,                      -- 当該選挙時点の首長(出典の記載どおり)
    mayor_affiliation TEXT,                    -- 首長の党派(所属政党。無所属なら首長選での推薦政党)
    candidate_name  TEXT,                      -- 支持した候補(複数なら複数行)
    candidate_party_nomination TEXT,           -- 支持候補の政党と公認・推薦の別(例: 自民公認)
    candidate_result TEXT,                     -- 選挙区当選 / 落選・比例復活 / 落選
    endorsement_form TEXT,                     -- 出陣式出席 / 推薦状 / 応援演説 / 報道での明言 / 後援会役員 / その他
    source_url      TEXT,
    outlet          TEXT,                      -- 媒体名
    evidence_date   TEXT,                      -- 出典の日付
    quote           TEXT,                      -- 引用文(原文)
    collection_status TEXT,                    -- 収集済 / 中立・非表明(出典あり) / 未収集
    politician_id   TEXT,
    note            TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS subsidy_programs (
    program_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    ministry        TEXT,
    discretion_level TEXT,                     -- 裁量性の高さ(high/medium/low/空欄=未評価)
    results_public  TEXT,                      -- 採択・配分結果の公開有無
    granularity     TEXT,
    note            TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS subsidy_allocations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id      TEXT NOT NULL REFERENCES subsidy_programs(program_id),
    municipality_code TEXT,                    -- 単独計画・単独事業主体のときのみ。共同計画は NULL
    fiscal_year     INTEGER NOT NULL,
    item_name       TEXT,                      -- 計画名・箇所名など
    recipients      TEXT,                      -- 計画策定主体の原文(「,」区切り)
    recipient_codes TEXT,                      -- 解決できた自治体コード(「,」区切り)
    attribution     TEXT NOT NULL,             -- sole(単独) / joint(共同、按分不能) / aggregate(県計など)
    amount_thousand_yen REAL,
    unit_original   TEXT,
    n_locations     INTEGER,
    decision_date   TEXT,                      -- 配分・交付決定の日付(分かる場合)
    raw_ref         TEXT,                      -- 原資料内の位置(ページ等)
    source_url      TEXT,
    retrieved_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_alloc_muni ON subsidy_allocations(municipality_code, fiscal_year);

CREATE TABLE IF NOT EXISTS requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pref_code       TEXT,
    municipality_code TEXT,
    record_type     TEXT NOT NULL,             -- 申請(計画提出・交付申請)/ 要望 / 結果
    request_date    TEXT,                      -- 申請日・要望日(不明なら NULL)
    date_precision  TEXT,                      -- day / month / fiscal_year(年度のみ判明)
    fiscal_year     INTEGER,
    recipient       TEXT,                      -- 申請先・要望先(国土交通大臣、栃木県、議員名など)
    program         TEXT,                      -- 制度名(社会資本整備総合交付金、防災・安全交付金、国への要望書など)
    project_name    TEXT,                      -- 事業名・計画名
    project_key     TEXT,                      -- 同じ事業を結ぶキー(計画名を正規化)
    planners        TEXT,                      -- 計画策定主体(共同計画は複数)
    attribution     TEXT,                      -- sole / joint
    plan_period     TEXT,
    requested_amount_thousand_yen REAL,        -- 要望額(計画書の全体事業費など。性質は amount_note)
    amount_note     TEXT,
    result          TEXT,                      -- 採択 / 一部採択 / 不採択 / 不明
    result_date     TEXT,                      -- 結果判明日
    result_amount_thousand_yen REAL,
    indicator_link  TEXT,                      -- グラフに載せる指標(mlit_sole_grants / mlit_road / all / NULL)
    collection_source TEXT,                    -- mlit_jigo_hyoka / mlit_haibun / municipal_plan_pdf / municipal_page / manual
    quote           TEXT,                      -- 出典の原文(表の行など)
    note            TEXT,
    source_url      TEXT,
    retrieved_at    TEXT,
    generated_by    TEXT
);

-- 申請・要望の収集状況(市町 × 収集元)。記録がないことと、探していないことを区別する
CREATE TABLE IF NOT EXISTS request_status (
    municipality_code TEXT NOT NULL,
    collection_source TEXT NOT NULL,
    status          TEXT NOT NULL,             -- 収集済 / 未収集 / 未取得(非公開) / 該当なし
    note            TEXT,
    checked_urls    TEXT,
    source_url      TEXT,
    retrieved_at    TEXT,
    PRIMARY KEY (municipality_code, collection_source)
);

CREATE TABLE IF NOT EXISTS statements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,             -- kokkai / local_assembly / sns / press
    external_id     TEXT,                      -- 例: 国会会議録の speechID
    speaker         TEXT,
    speaker_group   TEXT,
    speaker_position TEXT,
    politician_id   TEXT,                      -- politicians.name_variants と一致した場合
    date            TEXT,
    meeting         TEXT,
    body            TEXT,
    matched_keywords TEXT,                     -- 「|」区切り
    target_municipalities TEXT,                -- 本文中の自治体名から推定したコード(「,」区切り)
    source_url      TEXT,                      -- 発言の URL
    retrieved_at    TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS cases (
    case_id         TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    period          TEXT,
    pattern         TEXT,
    label           TEXT,                      -- positive / negative / reference / under_review
    summary         TEXT,
    politicians     TEXT,
    municipalities  TEXT,
    verification    TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    municipality_code TEXT NOT NULL,
    fiscal_year     INTEGER,
    politician_id   TEXT,
    score           REAL,
    contributions   TEXT,                      -- JSON(寄与内訳)
    evidence        TEXT,                      -- JSON(根拠 URL と数値)
    created_at      TEXT,
    source_url      TEXT,                      -- 主たる根拠 URL
    retrieved_at    TEXT
);

-- 取得の成否記録(README の「取得状況」の根拠)
CREATE TABLE IF NOT EXISTS fetch_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    step            TEXT NOT NULL,             -- list / download / parse / load
    status          TEXT NOT NULL,             -- ok / error / skipped
    detail          TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
);

-- 観測値(DESIGN.md 13.2)。指標ごとの最も細かい観測時点(年度、12 月分・3 月分、配分回)を 1 行とする。
CREATE TABLE IF NOT EXISTS observations (
    pref_code       TEXT NOT NULL,
    municipality_code TEXT NOT NULL,
    indicator_id    TEXT NOT NULL,             -- card_kokko / card_pref / tokko_dec / tokko_march / mlit_sole_grants / mlit_road
    period_start    TEXT NOT NULL,             -- 値が対象とする期間
    period_end      TEXT NOT NULL,
    decided_date    TEXT,                      -- 値が決まった日(配分決定日・交付決定日・決算は年度末)
    decided_date_is_proxy INTEGER,             -- 1 = 公表日などで代用
    decided_date_basis TEXT,                   -- decided_date の根拠
    published_date  TEXT,                      -- 公表日(不明なら NULL)
    value           REAL,                      -- NULL = 未取得
    unit            TEXT,
    count           INTEGER,                   -- 事業数・箇所数(指標 4・5)
    missing_reason  TEXT,
    source_url      TEXT,
    retrieved_at    TEXT,
    generated_by    TEXT,                      -- アダプタ ID
    PRIMARY KEY (municipality_code, indicator_id, period_start, decided_date)
);

-- 政局イベント(DESIGN.md 13.3)。出典のないイベントは入れない。
CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    date            TEXT NOT NULL,             -- 開始日(月までしか分からない場合は月初、date_precision に記録)
    end_date        TEXT,                      -- 期間イベントの終了日(単日なら NULL)
    date_precision  TEXT,                      -- day / month / period
    event_type      TEXT NOT NULL,
    scope           TEXT NOT NULL,             -- national / prefecture / district / municipality
    pref_code       TEXT,
    district        TEXT,                      -- 例: 栃木県第3区
    municipality_code TEXT,
    actor_name      TEXT,
    actor_party     TEXT,
    actor_role      TEXT,
    counterpart_name TEXT,
    counterpart_role TEXT,
    summary         TEXT,
    quote           TEXT,
    source_url      TEXT,
    outlet          TEXT,
    source_date     TEXT,
    note            TEXT,
    generated_by    TEXT,                      -- 自動生成したモジュール名(手作業は NULL)
    retrieved_at    TEXT
);
