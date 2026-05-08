-- Phase 8: SOP 反馈闭环 schema 升级

ALTER TABLE audit_records
    ADD COLUMN IF NOT EXISTS feedback_label TEXT,
    ADD COLUMN IF NOT EXISTS feedback_reason TEXT,
    ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_audit_feedback_label
    ON audit_records (feedback_label, host_ip)
    WHERE feedback_label IS NOT NULL;

CREATE OR REPLACE VIEW sop_candidates AS
SELECT
    host_ip,
    runbook_id,
    split_part(event_name, ' ', 1) AS event_keyword,
    count(*) AS success_count,
    array_agg(id ORDER BY created_at DESC) AS sample_ids,
    max(created_at) AS last_success_at
FROM audit_records
WHERE verify = true
  AND decision IN ('approved', 'auto_approved')
  AND runbook_id IS NOT NULL
GROUP BY host_ip, runbook_id, split_part(event_name, ' ', 1)
HAVING count(*) >= 3;
