"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: diagnostic_tools.py
@DateTime: 2026-05-08 14:31:00
@Docs: 提供 ReAct Agent 可调用的只读诊断工具及 OpenAI 工具 schema

ReAct Agent 可调用的只读诊断工具

设计原则：
- 只暴露具名工具（get_disk_usage 等），不暴露通用 run_command
- LLM 不能任意执行 shell，能调的命令在工具内部固定
- 入参（host / service / paths）做正则白名单校验，防止注入
- 所有命令都是只读，没有写操作

工具结果会塞进 conversation 给下一轮 LLM 看，所以截断到 4000 字符。
"""

import asyncio
import re
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

from src.config import settings

# 复用 disk_cleanup runbook 的主机白名单格式
_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9._\-@]{1,64}$")
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/\-]{0,128}$")
_TOOL_RESULT_LIMIT = 4000
ToolHandler = Callable[..., Awaitable[str]]


def _validate_host(host: str) -> str:
    if not isinstance(host, str) or not _HOST_RE.match(host):
        raise ValueError(f"invalid host: {host!r}")
    return host


def _validate_service(svc: str) -> str:
    if not isinstance(svc, str) or not _SERVICE_RE.match(svc):
        raise ValueError(f"invalid service: {svc!r}")
    return svc


def _validate_path(p: str) -> str:
    if not isinstance(p, str) or not _SAFE_PATH_RE.match(p) or ".." in p:
        raise ValueError(f"invalid path: {p!r}")
    return p


def _truncate(text: str) -> str:
    if len(text) <= _TOOL_RESULT_LIMIT:
        return text
    return text[:_TOOL_RESULT_LIMIT] + f"\n... [truncated, total {len(text)} chars]"


def _run_remote_shell(host: str, cmd: str, timeout: int = 30) -> str:
    """通过 ansible-runner 在远端跑一条 shell 命令，返回 stdout

    注意：调用方必须先用 _validate_* 函数校验过参数，否则会有注入风险。
    """
    import ansible_runner

    r = ansible_runner.run(
        private_data_dir=settings.ansible_private_data_dir,
        inventory=settings.ansible_inventory,
        host_pattern=host,
        module="shell",
        module_args=cmd,
        quiet=True,
        timeout=timeout,
    )
    out = r.stdout.read() if r.stdout else ""
    err = r.stderr.read() if r.stderr else ""
    if r.status != "successful":
        return f"[ansible status={r.status}]\nstdout:\n{out}\nstderr:\n{err}"
    return out


# ---------- 工具实现 ----------


async def get_disk_usage(host: str) -> str:
    """查所有挂载点的使用率"""
    host = _validate_host(host)
    out = await asyncio.to_thread(
        _run_remote_shell, host, "df -h --output=source,size,used,avail,pcent,target | head -20"
    )
    return _truncate(out)


async def get_directory_sizes(host: str, paths: list[str]) -> str:
    """查指定目录的大小（用于定位空间占用）

    paths 必须是绝对路径列表，且不允许 .. 等危险字符
    """
    host = _validate_host(host)
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list")
    safe = [_validate_path(p) for p in paths]
    cmd = "du -sh " + " ".join(shlex.quote(p) for p in safe) + " 2>/dev/null | sort -h"
    out = await asyncio.to_thread(_run_remote_shell, host, cmd)
    return _truncate(out)


async def get_service_status(host: str, service: str) -> str:
    """查 systemd 服务状态 + 最近 10 分钟日志"""
    host = _validate_host(host)
    service = _validate_service(service)
    qsvc = shlex.quote(service)
    cmd = (
        f"systemctl status {qsvc} --no-pager -l 2>&1 | head -30; "
        f"echo '---journal---'; "
        f"journalctl -u {qsvc} --since '10 min ago' --no-pager 2>&1 | tail -30"
    )
    out = await asyncio.to_thread(_run_remote_shell, host, cmd)
    return _truncate(out)


async def list_failed_services(host: str) -> str:
    """列出所有 failed 状态的 systemd 服务"""
    host = _validate_host(host)
    out = await asyncio.to_thread(
        _run_remote_shell,
        host,
        "systemctl list-units --state=failed --no-pager --plain --no-legend",
    )
    return _truncate(out)


# ---------- OpenAI tool schema ----------

DIAGNOSTIC_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "查看目标主机的磁盘使用率（每个挂载点）。磁盘类告警必须先调这个看哪个挂载点紧张。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string", "description": "目标主机 IP（来自告警 host_ip 字段）"},
                },
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_directory_sizes",
            "description": "查看指定多个目录的实际大小，用于定位空间占用最大的目录。在选择 disk_cleanup 的 path 之前必须调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要查询的绝对路径列表，例如 ['/tmp', '/var/log', '/var/cache']",
                    },
                },
                "required": ["host", "paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "查看指定 systemd 服务的状态以及最近 10 分钟日志。服务类告警用来确认服务是否真的挂了以及为什么挂。",
            "parameters": {
                "type": "object",
                "properties": {
                    "host": {"type": "string"},
                    "service": {"type": "string", "description": "systemd 单元名，例如 nginx / redis-server"},
                },
                "required": ["host", "service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_failed_services",
            "description": "列出目标主机上所有处于 failed 状态的 systemd 服务，用于发现所有挂掉的服务。",
            "parameters": {
                "type": "object",
                "properties": {"host": {"type": "string"}},
                "required": ["host"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_action",
            "description": (
                "诊断完成后调用此工具给出最终执行计划。这是终止工具——调用后 Agent 结束。"
                "必须基于前面工具调用的事实来决策，不要凭直觉。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "runbook_id": {
                        "type": "string",
                        "enum": ["disk_cleanup", "service_restart", "none"],
                        "description": "选择 runbook。none 表示没有合适的自动修复方案，仅通知人工。",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Runbook 参数。"
                            "disk_cleanup: {target_host, path, min_age_days}（path 必须从 get_directory_sizes 的输出选最大那个，且必须是 /tmp、/var/log、/var/cache 之一）。"
                            "service_restart: {target_host, service_name}。"
                            "none: {}。"
                        ),
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "决策依据（1~3 句话），引用前面工具调用看到的事实",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "对此决策的置信度",
                    },
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "操作风险等级",
                    },
                },
                "required": ["runbook_id", "params", "reasoning", "confidence", "risk_level"],
            },
        },
    },
]


# 工具名 → 异步处理函数映射
TOOL_HANDLERS: dict[str, ToolHandler] = {
    "get_disk_usage": get_disk_usage,
    "get_directory_sizes": get_directory_sizes,
    "get_service_status": get_service_status,
    "list_failed_services": list_failed_services,
}
