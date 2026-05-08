"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: base.py
@DateTime: 2026-05-08 14:13:00
@Docs: 定义 Runbook 基类和 Ansible 执行封装
"""

import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from src.config import settings
from src.models import RunbookResult


def _read_runner_stream(stream: Any) -> str:
    """读取 ansible-runner 返回的输出流，兼容字符串、字节和类文件对象。"""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    if isinstance(stream, str):
        return stream
    read = getattr(stream, "read", None)
    if callable(read):
        value = read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value) if value is not None else ""
    return str(stream)


def run_ansible(playbook: str, extravars: dict[str, Any], check: bool = False) -> RunbookResult:
    """执行 Ansible Playbook，返回 RunbookResult"""
    import ansible_runner

    start = time.monotonic()
    r = ansible_runner.run(
        private_data_dir=settings.ansible_private_data_dir,
        playbook=playbook,
        inventory=settings.ansible_inventory,
        extravars=extravars,
        **({"cmdline": "--check"} if check else {}),
    )
    duration = time.monotonic() - start
    stdout = _read_runner_stream(getattr(r, "stdout", None))
    stderr = _read_runner_stream(getattr(r, "stderr", None))
    return RunbookResult(
        success=r.status == "successful",
        stdout=stdout,
        stderr=stderr,
        duration_sec=round(duration, 2),
    )


class BaseRunbook(ABC):
    """每个 Runbook 必须实现五要素"""

    @abstractmethod
    def params_schema(self) -> type[BaseModel]:
        """参数 Schema"""

    @abstractmethod
    def dry_run(self, params: BaseModel) -> RunbookResult:
        """仿真执行，不产生副作用"""

    @abstractmethod
    def execute(self, params: BaseModel) -> RunbookResult:
        """实际执行"""

    @abstractmethod
    def rollback(self, snapshot: dict[str, Any]) -> bool:
        """回滚到快照状态"""

    @abstractmethod
    def verify(self, params: BaseModel) -> bool:
        """反向验证：目标是否恢复健康"""
