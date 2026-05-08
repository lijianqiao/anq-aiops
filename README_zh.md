# AIOps 智能告警处置平台

[English README](README.md)

AIOps 是一个面向运维场景的智能告警处置平台，把 Zabbix 告警、LLM 根因分析、飞书审批、Temporal 工作流、Redis Stream、PostgreSQL 经验库和 Ansible Runbook 串成一条可审计、可回滚、可人工介入的处置链路。

项目目标不是让 AI 直接“黑盒操作生产”，而是让 AI 负责诊断、归纳和生成建议，由策略引擎和人工审批控制风险，并把成功经验与拒绝反馈沉淀为后续可复用的 SOP。

## 功能特性

- Zabbix Webhook 接入，支持 token 鉴权。
- Redis Stream 缓冲与 `event_id` 去重。
- Temporal Workflow 编排完整告警处置流程。
- LLM 诊断 Agent，支持主备模型、熔断和降级。
- Policy Engine，支持 `live` / `shadow` 执行模式。
- 飞书交互卡片审批、拒绝原因反馈、结果通知。
- Ansible Runner 执行 Runbook，并使用隔离目录避免 `--check` 污染。
- 多 Agent 协同保护：告警关联、风暴限流、pending workflow 保护、同主机互斥。
- Hermes 知识层：PostgreSQL 存储历史审计记录，相似案例检索并注入 Agent prompt。
- Phase 8 反馈闭环：故障简报、拒绝反例、SOP 候选生成、可选 GitHub PR 自动化。
- AIOps 自身元监控通道。

## 架构

```text
Zabbix
  -> FastAPI /webhook/zabbix
  -> Redis Stream
  -> Alert Consumer
  -> Temporal AlertWorkflow
       -> LLM 诊断
       -> Policy 评估
       -> 飞书审批
       -> 同主机互斥
       -> Ansible Runbook
       -> 执行后验证
       -> 审计 + Hermes 经验库
       -> 故障简报 + SOP 反馈闭环
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| API | FastAPI, Uvicorn |
| 工作流 | Temporal |
| 消息总线 | Redis Streams |
| 经验库 | PostgreSQL, asyncpg, 全文检索 |
| LLM | OpenAI 兼容 API、Anthropic、本地模型兜底 |
| 通知与审批 | 飞书开放平台 |
| 自动化 | Ansible Runner |
| 定时任务 | APScheduler |
| 工程工具 | uv, pytest, Ruff, mypy |

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
```

至少需要配置：

```bash
ZABBIX_WEBHOOK_TOKEN=换成强随机字符串
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_RECEIVE_ID=oc_xxx
FEISHU_RECEIVE_ID_TYPE=chat_id
LLM_PRIMARY_API_KEY=sk-xxx
```

如果只是启动应用、不测试飞书回调，可以先把 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 留空。

### 2. 启动服务

```bash
docker compose up -d postgres redis temporal aiops
```

健康检查：

```bash
curl http://localhost:8000/health
```

期望返回：

```json
{"status":"ok"}
```

### 3. 手动发送测试告警

```bash
TOKEN="$(grep '^ZABBIX_WEBHOOK_TOKEN=' .env | cut -d= -f2-)"

curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -H "X-Zabbix-Token: ${TOKEN}" \
  -d '{
    "event_id": "test-001",
    "event_name": "Disk usage > 90%",
    "severity": "high",
    "hostname": "aiops-target",
    "host_ip": "192.168.198.130",
    "trigger_id": "10001",
    "message": "Disk usage is 95% on /tmp",
    "timestamp": "2026-05-09T00:00:00+08:00",
    "status": "problem"
  }'
```

## 本地开发

安装依赖：

```bash
uv sync --extra dev
```

本地开发模式连接 Docker 后端服务：

```bash
export TEMPORAL_ADDRESS=localhost:7233
export REDIS_URL=redis://localhost:6379/0
export FEISHU_APP_ID=
export FEISHU_APP_SECRET=
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

常用检查：

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/
```

## 项目结构

```text
src/
  api/             FastAPI 路由
  activities/      Temporal Activities：LLM、飞书、Runbook、审计、SOP
  bus/             Redis Stream 生产者与消费者
  coordination/    限流、pending 计数、动作互斥
  correlator/      告警关联
  hermes/          PostgreSQL 经验库与反馈标注
  llm/             LLM 客户端、路由、诊断 Agent
  policy/          策略引擎和 YAML 规则
  runbooks/        Runbook 实现
  scheduler/       APScheduler 定时任务
  sop/             SOP Markdown 和 PR 工具
  workflows/       Temporal Workflows
docs/              架构、运维和测试文档
tests/             单元测试和集成测试
```

## 关键配置

完整配置见 `.env.example`。

| 变量 | 说明 |
| --- | --- |
| `ZABBIX_WEBHOOK_TOKEN` | `/webhook/zabbix` 鉴权 token，Zabbix 需通过 `X-Zabbix-Token` 或 `Authorization: Bearer ...` 传入。 |
| `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | 飞书应用凭据，用于发卡片和接收审批回调。 |
| `FEISHU_RECEIVE_ID`, `FEISHU_RECEIVE_ID_TYPE` | 默认飞书接收对象。 |
| `LLM_PRIMARY_*`, `LLM_FALLBACK_*` | 主备模型配置。 |
| `AIOPS_MODE` | `live` 自动执行 allow 动作；`shadow` 保留人工审批。 |
| `HERMES_DB_URL` | Hermes PostgreSQL 连接串。 |
| `GITHUB_TOKEN` | 可选，用于自动创建 SOP PR。 |
| `SOP_GEN_SCHEDULE_HOUR` | 每日 SOP 生成时间；设为 `-1` 可禁用。 |

## 运维与测试文档

- `docs/AIOps 集群说明.md`：4 台 VM 实验集群说明。
- `docs/zabbix-integration.md`：Zabbix Webhook 配置。
- `docs/policy-mode.md`：Policy 与 live/shadow 模式。
- `docs/multi-agent-coordination.md`：告警关联、风暴保护和互斥验证。
- `docs/hermes-knowledge.md`：Hermes 知识层运维。
- `docs/sop-feedback.md`：SOP 自动化与反馈闭环操作。
- `docs/superpowers/specs/phase8-cluster-test-guide.md`：Phase 8 集群验收步骤。

## 安全注意事项

- 不要提交 `.env` 或真实密钥。
- `ZABBIX_WEBHOOK_TOKEN` 必须足够强，并与 Zabbix Webhook 配置保持一致。
- LLM 生成的 SOP 必须经过人工 review 后再合并。
- 生产 Runbook 应保持小而清晰，并由 Policy 规则保护。
