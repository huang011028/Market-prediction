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
    
    predicted_at    TEXT NOT NULL,
    valid_until     TEXT NOT NULL,
    
    actual_direction    TEXT,
    actual_change_pct   REAL,
    direction_correct   INTEGER,
    magnitude_hit       INTEGER,
    verified_at         TEXT,
    
    agents_used     TEXT NOT NULL,
    agents_failed   TEXT,
    elapsed_seconds REAL,
    llm_model       TEXT,
    
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
    last_updated    TEXT,
    PRIMARY KEY (agent_name, timeframe)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions(target);
CREATE INDEX IF NOT EXISTS idx_predictions_time ON predictions(predicted_at);
CREATE INDEX IF NOT EXISTS idx_predictions_verified ON predictions(verified_at);
CREATE INDEX IF NOT EXISTS idx_agent_results_prediction ON agent_results(prediction_id);
