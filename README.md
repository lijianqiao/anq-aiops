# AIOps

[中文文档](README_zh.md)

AIOps is an intelligent alert remediation platform that connects Zabbix alerts, LLM-based diagnosis, Feishu approvals, Temporal workflows, Redis Streams, PostgreSQL knowledge storage, and Ansible runbooks into a controlled human-in-the-loop operations pipeline.

It is designed for small infrastructure teams that want repeatable incident handling: alerts are correlated and rate-limited, diagnosis is explainable, risky actions require approval, execution is audited, and successful or rejected cases become reusable operational knowledge.

## Features

- Zabbix webhook ingestion with token authentication.
- Redis Stream buffering and deduplication.
- Temporal workflow orchestration for alert handling.
- LLM diagnostic agent with primary/fallback routing and circuit breaker protection.
- Policy engine with live/shadow execution modes.
- Feishu interactive approval cards and result notifications.
- Ansible runbook execution with isolated runner directories.
- Multi-agent coordination: alert correlation, storm protection, pending workflow guard, and per-host action mutex.
- Hermes knowledge layer backed by PostgreSQL for historical case retrieval and feedback injection.
- Phase 8 SOP loop: incident summaries, rejected-decision feedback, SOP candidate generation, and optional GitHub PR automation.
- Meta-monitoring channel for AIOps self-health alerts.

## Architecture

```text
Zabbix
  -> FastAPI /webhook/zabbix
  -> Redis Stream
  -> Alert consumer
  -> Temporal AlertWorkflow
       -> LLM diagnostic agent
       -> Policy evaluation
       -> Feishu approval
       -> Host action mutex
       -> Ansible runbook
       -> Verification
       -> Audit + Hermes knowledge
       -> Incident summary + SOP feedback loop
```

## Tech Stack

| Layer | Technology |
| --- | --- |
| API | FastAPI, Uvicorn |
| Workflow | Temporal |
| Message bus | Redis Streams |
| Knowledge store | PostgreSQL, asyncpg, full-text search |
| LLM | OpenAI-compatible APIs, Anthropic, local fallback |
| Notification and approval | Feishu Open Platform |
| Automation | Ansible Runner |
| Scheduler | APScheduler |
| Tooling | uv, pytest, Ruff, mypy |

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```bash
ZABBIX_WEBHOOK_TOKEN=change-me-to-a-strong-token
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_RECEIVE_ID=oc_xxx
FEISHU_RECEIVE_ID_TYPE=chat_id
LLM_PRIMARY_API_KEY=sk-xxx
```

If you only want to start the app without Feishu callbacks, leave `FEISHU_APP_ID` and `FEISHU_APP_SECRET` empty.

### 2. Start services

```bash
docker compose up -d postgres redis temporal aiops
```

Check health:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

### 3. Send a test alert

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

## Development

Install dependencies:

```bash
uv sync --extra dev
```

Run the application locally against Docker services:

```bash
export TEMPORAL_ADDRESS=localhost:7233
export REDIS_URL=redis://localhost:6379/0
export FEISHU_APP_ID=
export FEISHU_APP_SECRET=
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

Run checks:

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/
```

## Repository Layout

```text
src/
  api/             FastAPI routes
  activities/      Temporal activities: LLM, Feishu, runbook, audit, SOP
  bus/             Redis Stream producer and consumer
  coordination/    rate limiting, pending gauge, action mutex
  correlator/      alert correlation
  hermes/          PostgreSQL knowledge and feedback layer
  llm/             LLM clients, router, diagnostic agent
  policy/          policy engine and YAML rules
  runbooks/        runbook implementations
  scheduler/       APScheduler jobs
  sop/             SOP markdown and PR helpers
  workflows/       Temporal workflows
docs/              architecture, operations, and testing docs
tests/             unit and integration tests
```

## Key Configuration

See `.env.example` for the full list.

| Variable | Purpose |
| --- | --- |
| `ZABBIX_WEBHOOK_TOKEN` | Required token for `/webhook/zabbix`; sent via `X-Zabbix-Token` or `Authorization: Bearer ...`. |
| `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | Feishu app credentials for sending cards and receiving approval callbacks. |
| `FEISHU_RECEIVE_ID`, `FEISHU_RECEIVE_ID_TYPE` | Default Feishu recipient. |
| `LLM_PRIMARY_*`, `LLM_FALLBACK_*` | Primary and fallback model configuration. |
| `AIOPS_MODE` | `live` executes allowed actions automatically; `shadow` keeps approvals in the loop. |
| `HERMES_DB_URL` | PostgreSQL DSN for historical case storage. |
| `GITHUB_TOKEN` | Optional token for automatic SOP PR creation. |
| `SOP_GEN_SCHEDULE_HOUR` | Daily SOP generation hour; set to `-1` to disable. |

## Deployment and Testing Docs

- `docs/AIOps 集群说明.md`: four-VM lab environment setup.
- `docs/zabbix-integration.md`: Zabbix webhook media type setup.
- `docs/policy-mode.md`: policy and live/shadow mode.
- `docs/multi-agent-coordination.md`: correlation, storm protection, and mutex verification.
- `docs/hermes-knowledge.md`: PostgreSQL knowledge layer operations.
- `docs/sop-feedback.md`: SOP and feedback loop operations.
- `docs/superpowers/specs/phase8-cluster-test-guide.md`: Phase 8 cluster acceptance test steps.

## Security Notes

- Never commit `.env` or real API keys.
- Keep `ZABBIX_WEBHOOK_TOKEN` strong and synchronized between AIOps and Zabbix.
- Review all generated SOP PRs before merging.
- Keep production runbooks small, auditable, and protected by policy rules.
