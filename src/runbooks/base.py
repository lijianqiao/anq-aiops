import time
from abc import ABC, abstractmethod

from pydantic import BaseModel

from src.config import settings
from src.models import RunbookResult


def run_ansible(playbook: str, extravars: dict, check: bool = False) -> RunbookResult:
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
    stdout = r.stdout.read() if r.stdout else ""
    stderr = r.stderr.read() if r.stderr else ""
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
    def rollback(self, snapshot: dict) -> bool:
        """回滚到快照状态"""

    @abstractmethod
    def verify(self, params: BaseModel) -> bool:
        """反向验证：目标是否恢复健康"""
