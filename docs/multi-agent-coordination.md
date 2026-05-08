# Phase 4: 多 Agent 协同操作手册

配套架构：`docs/生产级 AIOps 架构设计.md` §6 和 §9.6。

## 四层防护

```text
[A] Webhook 入口                 [B] Consumer 关联              [C] Workflow 执行
┌─────────────────────┐  ┌──────────────────────────┐  ┌──────────────────┐
│ rate_limit (100/min)│→ │ correlate (30s window)    │→ │ action_mutex     │
│ overload guard (50) │  │  ├─ quick_filter (4 rule) │  │ (per host)       │
└─────────────────────┘  │  └─ llm_judge (cache)     │  └──────────────────┘
                         └──────────────────────────┘
```

## 配置项

| env | 默认 | 说明 |
|---|---:|---|
| `ALERT_RATE_LIMIT_PER_MIN` | 100 | 单分钟 Zabbix 告警入口上限 |
| `MAX_PENDING_WORKFLOWS` | 50 | in-flight workflow 上限，超过后入口返回 503 |
| `CORRELATOR_WINDOW_SEC` | 30 | 告警关联窗口，窗口内衍生告警会被抑制 |

## 调优建议

- 告警平稳但偶发尖峰：调高 `ALERT_RATE_LIMIT_PER_MIN`。
- workflow 执行慢导致入口过载：调高 `MAX_PENDING_WORKFLOWS` 或增加 worker 副本。
- 误合并偏多：调小 `CORRELATOR_WINDOW_SEC`，例如 10 秒。
- 漏关联偏多：调大 `CORRELATOR_WINDOW_SEC`，并检查 `quick_filter` 规则。

## 端到端验证

同 host 多告警合并：

```bash
for i in 1 2 3; do
  curl -X POST http://localhost:8000/webhook/zabbix \
    -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"event_id":"phase4-test-'$i'","event_name":"FS / 90%","severity":"high","hostname":"aiops-target","host_ip":"192.168.198.130","trigger_id":"phase4-test","message":"test","timestamp":"2026-05-09T10:00:00Z","status":"problem"}'
  sleep 1
done
```

期望：第一条启动 workflow，后两条在 consumer 侧被识别为衍生告警并 ack。

风暴限流：

```bash
for i in $(seq 1 120); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/webhook/zabbix \
    -H "Authorization: Bearer ${ZABBIX_WEBHOOK_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"event_id":"storm-'$i'","event_name":"FS / 90%","severity":"high","hostname":"aiops-target","host_ip":"192.168.198.130","trigger_id":"storm","message":"test","timestamp":"2026-05-09T10:00:00Z","status":"problem"}'
done
```

期望：超过 `ALERT_RATE_LIMIT_PER_MIN` 后返回 429。
