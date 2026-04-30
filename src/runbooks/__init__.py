from src.runbooks.base import BaseRunbook
from src.runbooks.disk_cleanup import DiskCleanupRunbook
from src.runbooks.service_restart import ServiceRestartRunbook

RUNBOOK_REGISTRY: dict[str, type[BaseRunbook]] = {
    "disk_cleanup": DiskCleanupRunbook,
    "service_restart": ServiceRestartRunbook,
}
