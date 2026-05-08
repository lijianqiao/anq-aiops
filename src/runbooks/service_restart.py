"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: service_restart.py
@DateTime: 2026-05-08 14:33:00
@Docs: 服务重启 Runbook 参数校验、执行与验证逻辑
"""

from typing import Any

from pydantic import BaseModel, Field

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible

_HOSTNAME_RE = r"^[A-Za-z0-9._\-]{1,64}$"
_SERVICE_RE = r"^[A-Za-z0-9._\-@]{1,64}$"


class ServiceRestartParams(BaseModel):
    """服务重启参数"""
    target_host: str = Field(pattern=_HOSTNAME_RE)
    service_name: str = Field(pattern=_SERVICE_RE)


class ServiceRestartRunbook(BaseRunbook):
    """重启目标主机上的 systemd 服务"""

    def params_schema(self) -> type[BaseModel]:
        return ServiceRestartParams

    def dry_run(self, params: BaseModel) -> RunbookResult:
        p = ServiceRestartParams.model_validate(params.model_dump())
        return run_ansible(
            "service_restart.yml",
            extravars={"target_host": p.target_host, "service_name": p.service_name},
            check=True,
        )

    def execute(self, params: BaseModel) -> RunbookResult:
        p = ServiceRestartParams.model_validate(params.model_dump())
        return run_ansible(
            "service_restart.yml",
            extravars={"target_host": p.target_host, "service_name": p.service_name},
        )

    def rollback(self, snapshot: dict[str, Any]) -> bool:
        return False

    def verify(self, params: BaseModel) -> bool:
        p = ServiceRestartParams.model_validate(params.model_dump())
        result = run_ansible(
            "service_restart.yml",
            extravars={"target_host": p.target_host, "service_name": p.service_name},
            check=True,
        )
        if not result.success:
            return False
        return "service_state=active" in result.stdout
