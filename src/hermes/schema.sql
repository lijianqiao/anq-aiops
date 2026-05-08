-- Hermes 知识层：audit_records 表（存全部 workflow 执行记录）
-- 借鉴 Hermes Agent 的 SQLite + FTS5 思路，PG 14+ tsvector 等价方案

CREATE TABLE IF NOT EXISTS audit_records (
    id              BIGSERIAL PRIMARY KEY,
    event_id        TEXT NOT NULL,
    workflow_id     TEXT NOT NULL,
    decision        TEXT NOT NULL,
    runbook_id      TEXT,
    runbook_params  JSONB,
    hostname        TEXT NOT NULL,
    host_ip         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    event_name      TEXT NOT NULL,
    message         TEXT NOT NULL,
    verify          BOOLEAN,
    execute_success BOOLEAN,
    exec_stdout     TEXT,
    agent_reasoning TEXT,
    agent_confidence REAL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    fts             TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', coalesce(event_name, '')), 'A') ||
        setweight(to_tsvector('simple', coalesce(message, '')), 'B') ||
        setweight(to_tsvector('simple', coalesce(hostname, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(agent_reasoning, '')), 'D')
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_audit_fts ON audit_records USING GIN (fts);

CREATE INDEX IF NOT EXISTS idx_audit_host_time
    ON audit_records (host_ip, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_decision_success
    ON audit_records (decision, verify) WHERE verify IS NOT NULL;
