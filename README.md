# AIOps

> 告警进来，AI 诊断给建议，人点一下执行，机器自动修。

智能告警处置平台：Zabbix 告警 → LLM 根因分析 → 飞书审批 → Ansible 自动修复。

## 架构

```
Zabbix Webhook
     ↓
  FastAPI (ingest)
     ↓
  Redis Stream (buffer)
     ↓
  Temporal Workflow
     ├── LLM RCA 分析 (rca_analyze)
     ├── LLM Action Plan (plan_action)
     ├── LLM Risk Evaluation (evaluate_risk)
     ├── 飞书卡片推送 (AI 分析 + 审批按钮)
     ├── 等待人工审批
     ├── Ansible Runbook 执行
     └── 审计日志
```

## 技术栈

| 层 | 技术 |
|---|------|
| API | FastAPI + Uvicorn |
| 工作流引擎 | Temporal |
| 消息总线 | Redis Stream |
| LLM | OpenAI / Anthropic / DeepSeek / 本地 llama.cpp |
| 通知 | 飞书机器人 Webhook |
| 自动化 | Ansible Runner |
| 数据库 | PostgreSQL (Temporal) |

## 快速开始

```bash
# 1. 配置环境变量
cp .env.example .env
# 编辑 .env 填入飞书 Webhook、LLM API Key 等

# 2. 启动所有服务
docker compose up -d

# 3. 测试告警
curl -X POST http://localhost:8000/webhook/zabbix \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "test-001",
    "event_name": "Disk usage > 90%",
    "severity": "high",
    "hostname": "web-server-01",
    "host_ip": "192.168.1.13",
    "trigger_id": "10001",
    "message": "Disk usage is 95% on /tmp",
    "timestamp": "2026-05-01T10:00:00Z",
    "status": "problem"
  }'
```

## 项目结构

```
src/
├── main.py                 # FastAPI 入口 + Temporal Worker
├── config.py               # 环境变量配置
├── models.py               # Pydantic 数据模型
├── api/webhook.py          # Zabbix/飞书 Webhook 端点
├── bus/
│   ├── producer.py         # Redis Stream 生产者
│   └── consumer.py         # Redis Stream 消费者
├── workflows/
│   └── alert_workflow.py   # Temporal 主工作流
├── activities/
│   ├── feishu.py           # 飞书卡片推送
│   ├── llm.py              # LLM 分析 Activities
│   ├── runbook.py          # Runbook 执行
│   └── audit.py            # 审计日志
├── llm/
│   ├── client.py           # LLM 客户端抽象 (OpenAI/Anthropic)
│   ├── router.py           # 主备模型切换
│   ├── circuit_breaker.py  # 熔断器
│   └── prompts.py          # Prompt 模板
└── runbooks/
    ├── base.py             # Runbook 基类
    ├── disk_cleanup.py     # 磁盘清理
    └── service_restart.py  # 服务重启
```

## 环境变量

参见 `.env.example`。核心配置：

| 变量 | 说明 |
|------|------|
| `FEISHU_WEBHOOK_URL` | 飞书机器人 Webhook 地址 |
| `LLM_PRIMARY_PROVIDER` | 主 LLM 供应商 (openai/anthropic) |
| `LLM_PRIMARY_API_KEY` | 主 LLM API Key |
| `LLM_FALLBACK_BASE_URL` | 备用 LLM 地址 (本地 llama.cpp) |
