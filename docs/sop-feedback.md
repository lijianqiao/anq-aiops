# Phase 8: SOP 自动化与反馈闭环操作手册

## 三件事

### 1. 故障简报

每次 workflow 完成后，AIOps 会发送一条飞书故障简报，包含告警、决策、Runbook、验证结果和一段经验沉淀。

### 2. SOP 候选 PR

每天定时任务会扫描 `sop_candidates` 视图，找出累积成功次数足够的同类处置案例，生成 `docs/sop/<category>/<name>.md`，并在配置 `GITHUB_TOKEN` 后尝试创建 PR。

Review 时重点检查：

- description 是否准确描述触发条件。
- procedure 步骤是否可执行。
- pitfalls 是否来自真实失败或风险场景。
- 是否泄露生产敏感信息。

### 3. 反馈闭环

运维在飞书卡片拒绝执行时需要填写原因。workflow 会把该原因写入 `audit_records.feedback_label` 和 `feedback_reason`。下次类似告警诊断前，Agent prompt 会注入这些反例，提醒 LLM 避坑。

## 常用 SQL

```sql
SELECT feedback_label, count(*)
FROM audit_records
WHERE feedback_label IS NOT NULL
GROUP BY feedback_label;
```

```sql
SELECT *
FROM sop_candidates
ORDER BY success_count DESC;
```

## 关闭路径

- 不想自动开 PR：清空 `GITHUB_TOKEN`。
- 不想定时生成 SOP：设置 `SOP_GEN_SCHEDULE_HOUR=-1`。
- 不想注入反例：临时关闭 Hermes 知识层或调整 `query_negative_cases` 的 limit。
