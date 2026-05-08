# Policy 层与 Shadow Mode 操作手册

> 配套架构文档：[docs/生产级 AIOps 架构设计.md](生产级 AIOps 架构设计.md) §9.1 + §8

## 一图速览

```
告警 → agent_diagnose → resolve_runbook → evaluate_policy → 三分支
                                                            ├─ DENY:           飞书通知拒绝 + 写审计 (decision=denied)
                                                            ├─ ALLOW + live:   直接执行 + 事后通知 (decision=auto_approved)
                                                            └─ APPROVAL_REQUIRED 或 ALLOW + shadow:
                                                                                飞书审批卡片 → 等待 → 执行 (decision=approved)
```

## 核心概念

- **Decision**：Policy 评估结果，三选一：`allow` / `approval_required` / `deny`
- **AIOPS_MODE**：进程级开关
  - `live`（默认）：按 Policy 决策执行，ALLOW 直接跳过审批
  - `shadow`：ALLOW 退化成审批，飞书卡片标 🌓 但仍要人点按钮，用于调试新规则
- **host_tier**：主机分级 `production` / `staging` / `dev`，从 `.env` 的 IP 列表推导
- **决策顺序**：所有 deny 规则 → 所有 require_approval → 所有 allow → 默认 approval_required（保守）

## 模式开关

`.env` 里：

```bash
AIOPS_MODE=live     # 默认，按 policy 自动执行
AIOPS_MODE=shadow   # 调试新规则时临时切，不真执行
```

切完重启容器：

```bash
sudo docker compose -f ./docker-compose.yml restart aiops
```

## 主机分级

`.env`：

```bash
PRODUCTION_HOSTS=192.168.198.130,192.168.198.131
STAGING_HOSTS=192.168.198.140
# 不在两者中的主机自动归 dev
```

policy 规则里就能用：

```yaml
- name: production_requires_approval
  effect: require_approval
  conditions:
    - host_tier: production
```

## 默认规则速查

7 条默认规则在 [src/policy/policies.yaml](../src/policy/policies.yaml)：

| 类型 | 规则名 | 触发条件 |
|---|---|---|
| 🚫 DENY | `deny_root_path_cleanup` | 清根目录或 /etc/usr/bin/boot/lib |
| 🚫 DENY | `deny_database_restart` | 重启 mysql/mariadb/postgres/mongodb/redis |
| 👤 APPROVAL | `production_requires_approval` | 生产主机 |
| 👤 APPROVAL | `high_risk_requires_approval` | agent 自评 high |
| 👤 APPROVAL | `low_confidence_requires_approval` | confidence < 0.9 |
| 🤖 ALLOW | `low_risk_disk_cleanup` | /tmp,/var/tmp,/var/log,/var/cache,/opt/cache + low + ≥0.9 |
| 🤖 ALLOW | `low_risk_stateless_service_restart` | nginx, redis-server + low + ≥0.9 |

## 添加新规则

编辑 `src/policy/policies.yaml`，**重启容器**生效（policies.yaml 是容器 mount 进去的，
改完不用重 build 镜像）：

```bash
sudo docker compose -f ./docker-compose.yml restart aiops
```

### 规则 schema

```yaml
policies:
  - name: <唯一规则名，建议蛇形命名>
    description: <人类可读，会写到飞书卡片 / 审计>
    effect: allow | require_approval | deny
    conditions:
      - <field>: <value>           # 默认 eq
      - <field>: { in: [...] }     # 显式 operator
      - <field>: { gte: 0.9 }
```

### 字段速查

| ctx 字段 | 含义 |
|---|---|
| `runbook_id` | `disk_cleanup` / `service_restart` |
| `params.path` | disk_cleanup 的清理路径 |
| `params.service_name` | service_restart 的服务名 |
| `params.target_host` | 目标 IP |
| `risk_level` | agent 评估的风险 `low` / `medium` / `high` |
| `confidence` | agent 置信度 0.0~1.0 |
| `host_tier` | `production` / `staging` / `dev` |
| `host_ip` | 同 `params.target_host` |
| `alert.severity` | 告警级别 |

### 操作符速查

| op | 用法 | 例子 |
|---|---|---|
| `eq`（默认） | `field: value` | `risk_level: low` |
| `ne` | `field: { ne: value }` | `host_tier: { ne: production }` |
| `in` | `field: { in: [...] }` | `params.path: { in: ["/tmp", "/var/log"] }` |
| `not_in` | `field: { not_in: [...] }` | |
| `gte` / `lte` | `field: { gte: 0.85 }` | `confidence: { gte: 0.85 }` |
| `gt` / `lt` | `field: { lt: 0.5 }` | |

## 离线测试一条规则

不发真告警，直接调引擎：

```bash
sudo docker compose -f ./docker-compose.yml exec aiops python -c "
from src.policy.engine import evaluate_policy
result = evaluate_policy(
    runbook_id='disk_cleanup',
    params={'target_host': '192.168.198.130', 'path': '/tmp'},
    alert={'severity': 'high'},
    plan={'risk_level': 'low', 'confidence': 0.95},
)
print(result.model_dump_json(indent=2))
"
```

输出例：

```json
{
  "decision": "allow",
  "matched_policy": "low_risk_disk_cleanup",
  "reason": "临时目录/日志/缓存清理 + 低风险 + 高置信度 → 自动"
}
```

## 飞书卡片 Policy 标签

| 标签 | 含义 | 按钮 |
|---|---|---|
| 🤖 **自动执行**（规则 X，无需审批） | live + ALLOW | 无 |
| 🌓 **Shadow 模式**：本应自动执行（规则 X）但仍走人工审批 | shadow + ALLOW | 有 |
| 👤 **需要人工审批**（规则 X）：reason | APPROVAL_REQUIRED | 有 |
| 🚫 **拒绝执行**（规则 X）：reason | DENY | 无（DENY workflow 直接早返回） |

## 审计标签

`audit.log` 里 `decision` 字段：

| 值 | 含义 |
|---|---|
| `auto_approved` | Policy ALLOW + live 模式，自动执行 |
| `approved` | 经过人工审批 |
| `rejected` | 人工点了"拒绝" |
| `timeout` | 30 分钟没人审批 |
| `denied` | Policy DENY，没机会执行 |
| `unsupported` | 关键词降级匹配也找不到合适 runbook |

统计自动率：

```bash
total=$(wc -l < /opt/aiops/audit.log)
auto=$(grep -c '"decision":"auto_approved"' /opt/aiops/audit.log)
echo "自动执行率: ${auto}/${total}"
```

## 常见排错

### 一条告警预期自动执行但实际走了人工审批

1. 看飞书卡片底部的 **Policy** 行，那里写了具体命中的规则
2. 命中 `default：默认走审批` → 没命中任何 allow 规则。检查 conditions 是否完全匹配
3. 命中 `production_requires_approval` 等 require_approval 规则 → 那是设计如此
4. 命中 `low_confidence_requires_approval` → agent 给的 confidence < 0.9

### 一条告警被 DENY 但你觉得应该执行

绝大多数 DENY 命中的是 `deny_database_restart` 或 `deny_root_path_cleanup`——这些是红线，**不要轻易放开**。如果业务确实需要，专门加一条更细的 allow 规则覆盖（同阶段顺序遍历，但 deny 阶段优先）。

### YAML 写错了

容器启动不会报错（lazy load），**第一条告警**才会触发 yaml 解析。如果损坏：

- `evaluate_policy_activity` 内部捕获异常，降级为 APPROVAL_REQUIRED
- 飞书卡片 Policy 行显示 `policy evaluation error, falling back to manual approval: ...`
- 容器日志里能看到完整 traceback：`docker compose logs aiops | grep -A 10 "policy evaluation failed"`
- 修完 yaml 后**下一条告警**就好，不用重启容器（每次评估都重新读文件）

### Shadow 跑稳了想切 live

```bash
# 编辑 .env
sed -i 's/^AIOPS_MODE=shadow/AIOPS_MODE=live/' .env
sudo docker compose -f ./docker-compose.yml restart aiops

# 看日志确认配置生效
sudo docker compose -f ./docker-compose.yml exec aiops python -c "from src.config import settings; print('mode:', settings.aiops_mode)"
# 期望: mode: live
```

## 上线灰度建议

参照 [生产级 AIOps 架构设计.md §8](生产级 AIOps 架构设计.md)：

1. **第 1 周（shadow）**：所有 ALLOW 决策仍走人工审批，观察决策准确率
2. **第 2-3 周（live + dev only）**：切 live，但 `PRODUCTION_HOSTS` 留空，让生产机器全部走 require_approval
3. **第 4 周起（live + 渐扩）**：把生产机器加进 `PRODUCTION_HOSTS`，然后单独写 allow 规则放开特定低风险场景

灰度过程中重点看 audit log：

```bash
# 自动执行成功率
grep '"decision":"auto_approved"' /opt/aiops/audit.log | jq -r '.execution_result.verify' | sort | uniq -c

# 人工拒绝率（高 = agent/policy 误判多，要回顾规则）
grep '"decision":"rejected"' /opt/aiops/audit.log | wc -l
```

## 参考

- 实施计划：[docs/superpowers/plans/2026-05-08-phase3-policy-shadow-mode.md](superpowers/plans/2026-05-08-phase3-policy-shadow-mode.md)（git ignored）
- 默认规则：[src/policy/policies.yaml](../src/policy/policies.yaml)
- 引擎源码：[src/policy/engine.py](../src/policy/engine.py)
- Activity：[src/activities/policy.py](../src/activities/policy.py)
- Workflow 集成：[src/workflows/alert_workflow.py](../src/workflows/alert_workflow.py)
