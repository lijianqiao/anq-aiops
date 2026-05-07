# AGENTS.md

## Cursor Cloud specific instructions

### 项目概述

AIOps 智能告警处置平台：Zabbix 告警 → LLM 根因分析 → 飞书审批 → Ansible 自动修复。
技术栈：Python 3.14 + FastAPI + Temporal + Redis Stream + Ansible。

### 基础设施服务

应用运行需要 Redis、PostgreSQL、Temporal 三个后端服务。它们通过 `docker-compose.yml` 启动：

```bash
sudo dockerd &>/tmp/dockerd.log &
sleep 3
sudo docker compose up -d postgres redis temporal
```

> **注意**：Cloud Agent VM 需要 `fuse-overlayfs` + `iptables-legacy` 才能运行 Docker。
> 若 Docker daemon 未启动，参考以下步骤：安装 fuse-overlayfs、配置 `/etc/docker/daemon.json` 使用 `fuse-overlayfs` storage-driver、切换 iptables 到 legacy 模式。

### 运行应用（开发模式）

必须覆盖注入的环境变量（默认指向 Docker 内部 DNS），使其指向 `localhost`：

```bash
export TEMPORAL_ADDRESS=localhost:7233
export REDIS_URL=redis://localhost:6379/0
export FEISHU_APP_ID=  # 留空跳过飞书监听
export FEISHU_APP_SECRET=
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

健康检查：`curl http://localhost:8000/health` → `{"status":"ok"}`

### 常用开发命令

| 操作 | 命令 |
|------|------|
| 安装依赖 | `uv sync --extra dev` |
| 运行测试 | `uv run pytest tests/ -v` |
| Lint 检查 | `uv run ruff check src/ tests/` |
| 格式化检查 | `uv run ruff format --check src/ tests/` |
| 类型检查 | `uv run mypy src/` |

### 已知问题（预存在于代码库中）

- `tests/test_activities_llm.py` 中 3 个测试因 mock 使用 `object()` 占位而失败（缺少 `select_client_for_agent` 方法），属于预存在的测试缺陷。
- `ruff check` 有 6 个预存在的 lint 警告（E402 + SIM117），均在测试文件中。
- `mypy` strict 模式有约 46 个预存在的类型错误。

### 关键注意事项

- 系统注入的环境变量（如 `TEMPORAL_ADDRESS`、`REDIS_URL`）优先级高于 `.env` 文件，本地开发时需在 shell 中显式 `export` 覆盖。
- 飞书功能需要真实的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，留空可跳过飞书监听，应用仍可正常启动。
- 测试 `/webhook/zabbix` 端点时使用 `$ZABBIX_WEBHOOK_TOKEN` 环境变量的值作为 `X-Zabbix-Token` header。
