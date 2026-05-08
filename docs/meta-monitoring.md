# 元监控运维手册

> 配套架构文档：[docs/生产级 AIOps 架构设计.md](生产级%20AIOps%20架构设计.md) §10。

## 设计原则：不能用自己监控自己

AIOps 自身故障必须由完全独立的链路告警：

- 独立 Docker container：`meta-monitor`
- 独立飞书自定义机器人：与 AIOps 主审批群分开
- 不复用 `src/*` 任何代码，不连接 Temporal/PG，不依赖主应用进程

## 5 个 Probe

| Probe | 目标 | 失败条件 |
|---|---|---|
| `fastapi` | `GET http://aiops:8000/health` | HTTP 非 200、`status` 非 `ok` 或连接失败 |
| `temporal` | TCP 7233 三次握手 | 连接拒绝或超时 |
| `redis` | TCP 6379 三次握手 | 连接拒绝或超时 |
| `lark` | `GET https://open.feishu.cn/open-apis/` | HTTP >= 500 或超时 |
| `llm` | `GET $LLM_PRIMARY_BASE_URL` | HTTP >= 500；未配置时跳过 |

## 设置独立飞书机器人

1. 飞书里创建一个独立运维告警群，不要使用 AIOps 主审批群。
2. 群设置 → 群机器人 → 添加机器人 → 自定义机器人。
3. 复制 webhook URL，填入 `.env` 的 `META_FEISHU_WEBHOOK_URL`。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `META_AIOPS_URL` | `http://aiops:8000` | FastAPI 健康检查地址 |
| `META_TEMPORAL_ADDR` | Docker Compose 内置默认 | Temporal gRPC 地址 |
| `META_REDIS_ADDR` | `redis:6379` | Redis 地址 |
| `META_INTERVAL_SEC` | `60` | 探测间隔秒数 |
| `META_FEISHU_WEBHOOK_URL` | 空 | 独立飞书自定义机器人 webhook |
| `LLM_PRIMARY_BASE_URL` | 空 | LLM 探测目标；空值时跳过 |

## 告警去重

- 同一组件 5 分钟内只发送 1 次失败告警。
- 组件恢复后发送 1 次 `RECOVERED` 消息。
- 去重状态保存在 `meta-monitor` 进程内存中，容器重启后重新计算。

## 启动与查看日志

```bash
sudo docker compose build meta-monitor
sudo docker compose up -d meta-monitor
sudo docker compose logs meta-monitor --tail 30
```

日志中应出现：

```text
meta_monitor started, probing every 60s
```

## 验证元监控本身可用

```bash
sudo docker compose stop redis
# 1 分钟内独立飞书运维群应收到：AIOps healthcheck FAIL [redis]

sudo docker compose start redis
# 1 分钟内独立飞书运维群应收到：AIOps healthcheck RECOVERED [redis]
```

如果没有收到任何消息，优先检查：

1. `META_FEISHU_WEBHOOK_URL` 是否填写的是独立自定义机器人 webhook。
2. `sudo docker compose logs meta-monitor --tail 100` 是否有 `META_FEISHU_WEBHOOK_URL not configured`。
3. `sudo docker compose ps meta-monitor` 是否显示容器仍在运行。

最坏情况下，`meta-monitor` 自己也可能挂掉；这种情况只能通过第二层外部检查补足，建议每周人工抽查一次 `docker compose ps meta-monitor`。
