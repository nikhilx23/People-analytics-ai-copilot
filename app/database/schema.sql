-- People Analytics AI Copilot — database schema
-- Written as portable SQL (works on SQLite now; swapping to PostgreSQL later
-- is mostly a connection-string change — no vendor-specific syntax used here
-- except AUTOINCREMENT, which has a direct Postgres equivalent, SERIAL).

DROP TABLE IF EXISTS employees;
CREATE TABLE employees (
    employee_id         TEXT PRIMARY KEY,
    full_name           TEXT NOT NULL,          -- PII: never returned by governed tools
    department          TEXT NOT NULL,
    job_title           TEXT NOT NULL,
    job_level           TEXT NOT NULL,
    location            TEXT NOT NULL,
    hire_date           TEXT NOT NULL,           -- ISO date
    termination_date    TEXT,                    -- ISO date or NULL
    termination_type    TEXT,                    -- 'Voluntary' | 'Involuntary' | NULL
    termination_reason  TEXT,
    employee_status     TEXT NOT NULL,           -- 'Active' | 'Terminated'
    salary              INTEGER NOT NULL,        -- sensitive: aggregate-only access
    performance_rating  TEXT NOT NULL,           -- sensitive: aggregate-only access
    manager             TEXT,
    last_promotion_date TEXT                     -- ISO date or NULL
);

-- Audit trail: every question / tool call / governance decision is logged.
-- This is what lets you answer "did the AI follow the data-access rules?"
DROP TABLE IF EXISTS query_log;
CREATE TABLE query_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT NOT NULL,
    user_role        TEXT NOT NULL,
    question         TEXT,
    tool_used        TEXT,
    sql_executed     TEXT,
    governance_decision TEXT NOT NULL,           -- 'ALLOWED' | 'BLOCKED'
    block_reason     TEXT,
    result_summary   TEXT,
    routing_method   TEXT                        -- 'llm' | 'rule_based'
);

-- Analyst feedback loop
DROP TABLE IF EXISTS feedback;
CREATE TABLE feedback (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    question        TEXT NOT NULL,
    tool_used       TEXT,
    answer          TEXT,
    feedback        TEXT NOT NULL,               -- 'up' | 'down' | 'report'
    error_type      TEXT,                        -- free text, only set on 'down'/'report'
    response_time_ms INTEGER
);

-- Per-question latency breakdown, for the performance dashboard.
DROP TABLE IF EXISTS performance_log;
CREATE TABLE performance_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp               TEXT NOT NULL,
    question                TEXT,
    tool_used               TEXT,
    routing_method          TEXT,                 -- 'llm' | 'rule_based'
    routing_time_ms         REAL,
    tool_execution_time_ms  REAL,
    db_query_time_ms        REAL,
    validation_time_ms      REAL,
    total_time_ms           REAL
);
