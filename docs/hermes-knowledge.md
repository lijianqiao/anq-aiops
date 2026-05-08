# Hermes 知识层操作手册

配套架构：`docs/生产级 AIOps 架构设计.md` §2 和 §11。

## 工作原理

每次 agent 诊断前：

```text
agent_diagnose(alert)
  ├─ Hermes.query_similar_cases(alert) → Top-3 历史案例
  ├─ 注入到 system prompt 的 Past Experiences 段
  └─ ReAct 多轮诊断循环
```

每次 workflow 完成后：

```text
write_audit
  ├─ JSONL 审计日志，始终写入
  └─ PostgreSQL audit_records，失败不阻塞主流程
```

## 配置

```bash
HERMES_DB_URL=postgresql://temporal:temporal@postgres:5432/temporal
```

留空 `HERMES_DB_URL` 可紧急关闭知识层。关闭后审计仍会写 JSONL，agent 只是不注入历史案例。

## Schema

`audit_records` 表字段见 `src/hermes/schema.sql`。

关键字段：

- `event_name`、`message`、`hostname`、`agent_reasoning` 会进入 `tsvector` 全文索引。
- `verify` 标记 runbook 最终是否验证通过。
- `runbook_params` 使用 `JSONB` 保存执行参数。
- `exec_stdout` 只保留尾部文本，避免 Ansible 输出过大。

## 常用查询

查看最近成功案例：

```sql
SELECT created_at, host_ip, event_name, runbook_id, decision
FROM audit_records
WHERE verify = true AND created_at > NOW() - INTERVAL '7 days'
ORDER BY created_at DESC;
```

检索类似磁盘告警：

```sql
SELECT event_id, host_ip, event_name, runbook_id,
       ts_rank(fts, websearch_to_tsquery('simple', 'disk tmp')) AS rank
FROM audit_records
WHERE fts @@ websearch_to_tsquery('simple', 'disk tmp')
ORDER BY rank DESC
LIMIT 5;
```

## 性能排查

查询超过 200ms 时，先看 GIN 索引是否命中：

```sql
EXPLAIN ANALYZE
SELECT *
FROM audit_records
WHERE fts @@ websearch_to_tsquery('simple', 'disk tmp')
LIMIT 3;
```

如果表持续增大，可按月分区或归档一年前的历史数据。当前内网告警量下，单表加 GIN 索引足够。
