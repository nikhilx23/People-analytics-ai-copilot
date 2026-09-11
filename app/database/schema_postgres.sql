-- People Analytics AI Copilot — PostgreSQL schema.
-- Same shape as schema.sql (SQLite); only the autoincrement-PK syntax differs
-- (SERIAL/GENERATED here vs AUTOINCREMENT there) and TIMESTAMPTZ replaces
-- SQLite's plain TEXT timestamp column for a bit more rigor now that we're
-- on a database that actually enforces it.

DROP TABLE IF EXISTS employees;
CREATE TABLE employees (
    employee_id         TEXT PRIMARY KEY,
    full_name           TEXT NOT NULL,          -- PII: never returned by governed tools
    department          TEXT NOT NULL,
    job_title            TEXT NOT NULL,
    job_level           TEXT NOT NULL,
    location             TEXT NOT NULL,
    hire_date            DATE NOT NULL,
    termination_date     DATE,
    termination_type    TEXT,                    -- 'Voluntary' | 'Involuntary' | NULL
    termination_reason  TEXT,
    employee_status      TEXT NOT NULL,           -- 'Active' | 'Terminated'
    salary               INTEGER NOT NULL,        -- sensitive: aggregate-only access
    performance_rating  TEXT NOT NULL,           -- sensitive: aggregate-only access
    manager              TEXT,
    last_promotion_date DATE
);

DROP TABLE IF EXISTS query_log;
CREATE TABLE query_log (
    id                   SERIAL PRIMARY KEY,
    timestamp             TIMESTAMPTZ NOT NULL,
    user_role            TEXT NOT NULL,
    question              TEXT,
    tool_used             TEXT,
    sql_executed          TEXT,
    governance_decision  TEXT NOT NULL,           -- 'ALLOWED' | 'BLOCKED'
    block_reason          TEXT,
    result_summary        TEXT,
    routing_method        TEXT                     -- 'llm' | 'rule_based'
);

DROP TABLE IF EXISTS feedback;
CREATE TABLE feedback (
    id               SERIAL PRIMARY KEY,
    timestamp         TIMESTAMPTZ NOT NULL,
    question          TEXT NOT NULL,
    tool_used         TEXT,
    answer            TEXT,
    feedback          TEXT NOT NULL,               -- 'up' | 'down' | 'report'
    error_type        TEXT,
    response_time_ms INTEGER
);

DROP TABLE IF EXISTS performance_log;
CREATE TABLE performance_log (
    id                    SERIAL PRIMARY KEY,
    timestamp              TIMESTAMPTZ NOT NULL,
    question               TEXT,
    tool_used              TEXT,
    routing_method         TEXT,                    -- 'llm' | 'rule_based'
    routing_time_ms        REAL,
    tool_execution_time_ms REAL,
    db_query_time_ms       REAL,
    validation_time_ms     REAL,
    total_time_ms           REAL
);
