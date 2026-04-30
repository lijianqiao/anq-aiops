from pydantic import BaseModel

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible


class DiskCleanupParams(BaseModel):
    """磁盘清理参数"""
    target_host: str
    path: str = "/tmp"
    min_age_days: int = 7


class DiskCleanupRunbook(BaseRunbook):
    """清理目标主机上指定路径下的过期文件"""

    def params_schema(self) -> type[BaseModel]:
        return DiskCleanupParams

    def dry_run(self, params: BaseModel) -> RunbookResult:
        p = DiskCleanupParams.model_validate(params.model_dump())
        return run_ansible(
            "disk_cleanup.yml",
            extravars={"target_host": p.target_host, "path": p.path, "min_age_days": p.min_age_days},
            check=True,
        )

    def execute(self, params: BaseModel) -> RunbookResult:
        p = DiskCleanupParams.model_validate(params.model_dump())
        return run_ansible(
            "disk_cleanup.yml",
            extravars={"target_host": p.target_host, "path": p.path, "min_age_days": p.min_age_days},
        )

    def rollback(self, snapshot: dict) -> bool:
        return False

    def verify(self, params: BaseModel) -> bool:
        p = DiskCleanupParams.model_validate(params.model_dump())
        result = run_ansible(
            "disk_cleanup.yml",
            extravars={"target_host": p.target_host, "path": p.path, "min_age_days": p.min_age_days},
            check=True,
        )
        if not result.success:
            return False
        for line in result.stdout.splitlines():
            if "disk_usage=" in line:
                usage = int(line.split("disk_usage=")[1].strip())
                return usage < 80
        return False
