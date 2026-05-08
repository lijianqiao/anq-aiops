"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: base.py
@DateTime: 2026-05-08 14:13:00
@Docs: 定义 Runbook 基类和 Ansible 执行封装
"""

import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
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
    source_dir = Path(settings.ansible_private_data_dir)
    source_playbook = Path(playbook)
    if not source_playbook.is_absolute():
        source_playbook = source_dir / source_playbook
    source_inventory = Path(settings.ansible_inventory)

    with tempfile.TemporaryDirectory(prefix="aiops-runner-") as runner_dir:
        runner_path = Path(runner_dir)
        runner_playbook = runner_path / source_playbook.name
        runner_inventory = runner_path / source_inventory.name
        shutil.copy2(source_playbook, runner_playbook)
        shutil.copy2(source_inventory, runner_inventory)

        # 用绝对路径传 inventory：ansible-runner 收到相对路径时会去
        # private_data_dir/inventory/ 子目录里找，单文件放在根下找不到 →
        # ansible-playbook 拿不到 -i 参数 → "Could not match host pattern"
        r = ansible_runner.run(
            private_data_dir=str(runner_path),
            playbook=runner_playbook.name,
            inventory=str(runner_inventory),
            extravars=extravars,
            **({"cmdline": "--check"} if check else {}),
        )
        stdout = _read_runner_stream(getattr(r, "stdout", None))
        stderr = _read_runner_stream(getattr(r, "stderr", None))
        success = r.status == "successful"

    duration = time.monotonic() - start
    return RunbookResult(
        success=success,
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
