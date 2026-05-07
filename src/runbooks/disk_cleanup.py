import re

from pydantic import BaseModel, Field

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible

# 白名单：限制 LLM 给出离谱参数把生产删空
_HOSTNAME_RE = r"^[A-Za-z0-9._\-]{1,64}$"
_PATH_RE = r"^/(tmp|var/log|var/cache)(/[A-Za-z0-9._\-/]*)?$"


class DiskCleanupParams(BaseModel):
    """磁盘清理参数"""
    target_host: str = Field(pattern=_HOSTNAME_RE)
    path: str = Field(default="/tmp", pattern=_PATH_RE)
    min_age_days: int = Field(default=7, ge=1, le=365)


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
            match = re.search(r"disk_usage=(\d+)", line)
            if match:
                usage = int(match.group(1))
                return usage < 80
        return False
