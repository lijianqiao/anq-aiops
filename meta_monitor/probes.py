"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: probes.py
@DateTime: 2026-05-08 14:10:43 UTC
@Docs: 元监控五个独立健康探测函数
"""

import os
import socket
from collections.abc import Callable

import httpx

ProbeResult = tuple[bool, str]
ProbeFunc = Callable[[], ProbeResult]

_PROBE_TIMEOUT = 2.0
_DEFAULT_TEMPORAL_ADDR = "temporal" + ":7233"


def _tcp_probe(host: str, port: int, label: str) -> ProbeResult:
    """
    通用 TCP 三次握手检测

    Args:
        host: 目标主机名或 IP
        port: 目标端口
        label: 组件名称

    Returns:
        健康状态与诊断信息
    """
    try:
        sock = socket.create_connection((host, port), timeout=_PROBE_TIMEOUT)
        sock.close()
        return True, f"{label} tcp ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{label} tcp fail: {exc}"


def _split_host_port(addr: str, default_port: int) -> tuple[str, int]:
    """
    解析 host:port 地址

    Args:
        addr: 地址字符串
        default_port: 未指定端口时使用的默认端口

    Returns:
        主机与端口
    """
    if ":" not in addr:
        return addr, default_port
    host, port = addr.rsplit(":", 1)
    return host, int(port)


def _env_or_default(name: str, default: str) -> str:
    """
    读取环境变量，空字符串按未配置处理

    Args:
        name: 环境变量名
        default: 默认值

    Returns:
        最终配置值
    """
    return os.environ.get(name, "").strip() or default


def probe_fastapi(base_url: str) -> ProbeResult:
    """
    探测 AIOps FastAPI /health 端点

    Args:
        base_url: FastAPI 基础地址

    Returns:
        健康状态与诊断信息
    """
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            response = client.get(f"{base_url.rstrip('/')}/health")
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = {}
            if data.get("status") in {None, "ok"}:
                return True, "fastapi /health ok"
            return False, f"fastapi /health bad status: {data.get('status')}"
        return False, f"fastapi /health http {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"fastapi unreachable: {exc}"


def probe_temporal(addr: str) -> ProbeResult:
    """
    探测 Temporal gRPC TCP 端口

    Args:
        addr: Temporal 地址，例如默认 Docker Compose 服务名加 7233 端口

    Returns:
        健康状态与诊断信息
    """
    host, port = _split_host_port(addr, 7233)
    return _tcp_probe(host, port, "temporal")


def probe_redis(addr: str) -> ProbeResult:
    """
    探测 Redis TCP 端口

    Args:
        addr: Redis 地址，例如 redis:6379

    Returns:
        健康状态与诊断信息
    """
    host, port = _split_host_port(addr, 6379)
    return _tcp_probe(host, port, "redis")


def probe_lark_ws() -> ProbeResult:
    """
    探测飞书开放平台是否可达

    Returns:
        健康状态与诊断信息
    """
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            response = client.get("https://open.feishu.cn/open-apis/")
        if response.status_code < 500:
            return True, f"lark http {response.status_code}"
        return False, f"lark http {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"lark unreachable: {exc}"


def probe_llm() -> ProbeResult:
    """
    探测 LLM 主提供商基础地址是否可达

    Returns:
        健康状态与诊断信息
    """
    base_url = os.environ.get("LLM_PRIMARY_BASE_URL", "").strip()
    if not base_url:
        return True, "llm probe skipped (no LLM_PRIMARY_BASE_URL)"

    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            response = client.get(base_url)
        if response.status_code < 500:
            return True, f"llm http {response.status_code}"
        return False, f"llm http {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"llm unreachable: {exc}"


PROBES: dict[str, ProbeFunc] = {
    "fastapi": lambda: probe_fastapi(_env_or_default("META_AIOPS_URL", "http://aiops:8000")),
    "temporal": lambda: probe_temporal(_env_or_default("META_TEMPORAL_ADDR", _DEFAULT_TEMPORAL_ADDR)),
    "redis": lambda: probe_redis(_env_or_default("META_REDIS_ADDR", "redis:6379")),
    "lark": probe_lark_ws,
    "llm": probe_llm,
}
