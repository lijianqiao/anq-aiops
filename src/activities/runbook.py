from temporalio import activity

from src.models import ExecutionResult, RunbookResult
from src.runbooks import RUNBOOK_REGISTRY


@activity.defn
async def execute_runbook(runbook_id: str, params_json: str) -> str:
    """执行 Runbook：dry-run → execute → verify。返回 ExecutionResult JSON"""
    if runbook_id not in RUNBOOK_REGISTRY:
        raise ValueError(f"Unknown runbook: {runbook_id}")

    runbook_cls = RUNBOOK_REGISTRY[runbook_id]
    runbook = runbook_cls()
    schema = runbook.params_schema()
    params = schema.model_validate_json(params_json)

    # 1. dry-run
    dry_result = runbook.dry_run(params)
    if not dry_result.success:
        result = ExecutionResult(
            dry_run=dry_result,
            execute=RunbookResult(success=False, stdout="", stderr="dry-run failed", duration_sec=0),
            verify=False,
            snapshot={},
        )
        return result.model_dump_json()

    # 2. execute
    exec_result = runbook.execute(params)

    # 3. verify
    verified = False
    if exec_result.success:
        verified = runbook.verify(params)

    # 4. rollback if verify failed
    rolled_back = False
    if exec_result.success and not verified:
        snapshot = {"params": params_json, "exec_stdout": exec_result.stdout}
        rolled_back = runbook.rollback(snapshot)

    result = ExecutionResult(
        dry_run=dry_result,
        execute=exec_result,
        verify=verified,
        snapshot={"params": params_json},
        rolled_back=rolled_back,
    )
    return result.model_dump_json()
