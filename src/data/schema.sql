-- ============================================================
-- Market Prediction — 预测追踪数据库 Schema
-- ============================================================

-- 预测主表
CREATE TABLE IF NOT EXISTS predictions (
    id              TEXT PRIMARY KEY,
    target          TEXT NOT NULL,
    target_name     TEXT,
    timeframe       TEXT NOT NULL,
    
    direction       TEXT NOT NULL,
    min_pct         REAL,
    max_pct         REAL,
    confidence      REAL NOT NULL,

    -- Prediction Target V3.1: Beta 残差收益分布与可操作边际
    target_version  TEXT,
    target_type     TEXT,
    residualization_mode TEXT,
    market_beta     REAL,
    horizon         TEXT,
    horizon_trading_days INTEGER,
    horizon_calendar_days INTEGER,
    benchmark_symbol TEXT,
    up_threshold_pct REAL,
    down_threshold_pct REAL,
    neutral_band_pct REAL,
    expected_excess_return_pct REAL,
    expected_return_p10 REAL,
    expected_return_p50 REAL,
    expected_return_p90 REAL,
    prob_up         REAL,
    prob_down       REAL,
    prob_no_edge    REAL,
    edge_score      REAL,
    decision        TEXT,
    no_trade_reason TEXT,
    neutral_reason  TEXT,
    
    predicted_at    TEXT NOT NULL,
    valid_until     TEXT NOT NULL,
    
    actual_direction    TEXT,
    actual_change_pct   REAL,
    actual_effective_return_pct REAL,
    actual_absolute_return_pct REAL,
    actual_benchmark_return_pct REAL,
    window_max_effective_return_pct REAL,
    window_min_effective_return_pct REAL,
    target_type_used TEXT,
    brier_score       REAL,
    edge_hit          INTEGER,
    direction_correct   INTEGER,
    magnitude_hit       INTEGER,
    verified_at         TEXT,
    
    agents_used     TEXT NOT NULL,
    agents_failed   TEXT,
    elapsed_seconds REAL,
    llm_model       TEXT,

    -- Forward cohort lineage and persistent verification retries
    cohort_id       TEXT,
    lineage_json    TEXT,
    code_revision   TEXT,
    prompt_bundle_hash TEXT,
    skill_registry_hash TEXT,
    verification_status TEXT DEFAULT 'scheduled',
    verification_attempts INTEGER DEFAULT 0,
    last_verification_attempt_at TEXT,
    next_verification_at TEXT,
    verification_last_error TEXT,
    
    summary         TEXT,
    report_json     TEXT,
    report_md       TEXT
);

-- Agent 单独结果
CREATE TABLE IF NOT EXISTS agent_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    min_pct         REAL,
    max_pct         REAL,
    confidence      REAL NOT NULL,
    reasoning       TEXT,
    key_factors     TEXT,
    risks           TEXT,
    data_summary    TEXT,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);

-- 准确率统计
CREATE TABLE IF NOT EXISTS accuracy_stats (
    agent_name      TEXT,
    timeframe       TEXT,
    total_predictions   INTEGER DEFAULT 0,
    direction_accuracy  REAL DEFAULT 0,
    magnitude_accuracy  REAL DEFAULT 0,
    avg_confidence      REAL DEFAULT 0,
    avg_error_pct       REAL DEFAULT 0,
    brier_score         REAL DEFAULT 0,
    edge_hit_rate       REAL DEFAULT 0,
    avg_edge_score      REAL DEFAULT 0,
    actionable_coverage REAL DEFAULT 0,
    avg_actual_effective_return_pct REAL DEFAULT 0,
    avg_expected_excess_return_pct REAL DEFAULT 0,
    last_updated    TEXT,
    PRIMARY KEY (agent_name, timeframe)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target);
CREATE INDEX IF NOT EXISTS idx_predictions_time ON predictions(predicted_at);
CREATE INDEX IF NOT EXISTS idx_predictions_verified ON predictions(verified_at);
-- V3.1 cohort/retry indexes are created by PredictionStore._migrate_schema.
-- Keeping them out of this base script lets existing databases add columns first.
CREATE INDEX IF NOT EXISTS idx_agent_results_prediction ON agent_results(prediction_id);
