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
    election_id     TEXT PRIMARY KEY,          -- 例: shugiin_2026_tochigi_3
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
    candidate_name  TEXT NOT NULL,
    politician_id   TEXT REFERENCES politicians(politician_id),
    party           TEXT,
    votes           REAL,
    is_district_winner INTEGER,                -- 選挙区全体での当選(1/0)
    pr_revived      INTEGER,                   -- 比例復活(1/0/NULL=未確認)
    sekihai_rate    REAL,                      -- 惜敗率(%)
    source_url      TEXT,
    retrieved_at    TEXT,
    PRIMARY KEY (election_id, counting_unit, candidate_name)
);

CREATE TABLE IF NOT EXISTS endorsements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endorser_name   TEXT,
    endorser_role   TEXT,                      -- 首長 / 地方議員 / 団体
    municipality_code TEXT,
    candidate_name  TEXT,
    politician_id   TEXT,
    election_id     TEXT,
    stance          TEXT,                      -- support / oppose / neutral / 空欄=未確認
    evidence_date   TEXT,
    source_title    TEXT,
    note            TEXT,
    source_url      TEXT,
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
    municipality_code TEXT,
    fiscal_year     INTEGER,
    project         TEXT,
    result          TEXT,                      -- 採択 / 不採択 / 減額 / 不明
    note            TEXT,
    source_url      TEXT,
    retrieved_at    TEXT
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
