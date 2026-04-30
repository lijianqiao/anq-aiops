from pydantic import BaseModel

from src.models import RunbookResult
from src.runbooks.base import BaseRunbook, run_ansible


class ServiceRestartParams(BaseModel):
    """服务重启参数"""
    target_host: str
    service_name: str


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

    def rollback(self, snapshot: dict) -> bool:
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
