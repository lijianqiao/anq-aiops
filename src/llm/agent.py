"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: agent.py
@DateTime: 2026-05-08 14:31:00
@Docs: 实现 ReAct 诊断 Agent 的工具调用循环和执行计划生成

ReAct 诊断 Agent

流程：
  1. 收到 alert，给 LLM
  2. LLM 调用诊断工具（get_disk_usage / get_directory_sizes / ...）
  3. 把工具结果塞回 conversation
  4. 重复 2-3 直到 LLM 调用 propose_action 终止
  5. 返回 ActionPlan + 诊断轨迹

防御机制：
- 最多 5 轮，超出报错
- 单次工具结果截断到 4000 字符
- 工具入参 / runbook 参数都会经过校验
- 工具异常被捕获后塞进对话让 LLM 看到错误并自适应
"""

import json
import logging
from typing import Any, cast

from src.llm.client import LLMClient
from src.llm.diagnostic_tools import DIAGNOSTIC_TOOLS, TOOL_HANDLERS
from src.models import ActionPlan, Alert

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是 AIOps 诊断 Agent。收到告警后，你的目标是给出最合适的执行计划。

工作流程：
1. **先观察，再决策**：磁盘类告警必须先调 get_disk_usage 看哪个挂载点紧张，
   再调 get_directory_sizes 定位哪个目录占用最多——不要凭直觉选 path。
   服务类告警先调 list_failed_services 或 get_service_status 确认服务真的挂了。
2. **基于事实**：propose_action 时的 reasoning 必须引用前面工具调用看到的事实。
3. **严格 schema**：propose_action 的 params 必须严格匹配 runbook 的 schema：
   - disk_cleanup: {target_host, path, min_age_days}
     - path 必须是 /tmp、/var/log、/var/cache 之一，且应是 get_directory_sizes 输出里占用最大的那个
     - min_age_days 1~365 整数，建议 7
   - service_restart: {target_host, service_name}
     - service_name 必须是真实存在的 systemd 单元名（看 get_service_status 确认）
   - none: {} 表示没有合适的自动修复，仅人工通知
4. **target_host 永远填告警里的 host_ip**，不是主机名。
5. **不要发明新字段**（如 age / dir / file 等）。

可用工具：5 个诊断工具 + 1 个 propose_action 终止工具。
最多调 5 轮，所以工具调用要精准——每次工具调用都要服务于决策。
"""

PAST_CASES_SECTION_TEMPLATE = """

## Past Experiences（Hermes 知识层）

下面是过去对类似告警的处置历史。仅供参考，本次告警的实际处置方案必须以你
通过诊断工具收集到的事实为准。注意带 ❌ 的失败案例，避免重复同样的错误。

### 历史案例
{past_cases_text}

### 反例反馈
{negative_cases_text}
"""


class AgentLimitExceeded(Exception):
    """Agent 达到最大轮次仍未给出 propose_action"""


class AgentResult:
    """Agent 输出：plan 可能是 None 表示 LLM 选择了 'none'（不修复仅通知）"""

    def __init__(self, plan: ActionPlan | None, trace: list[dict[str, Any]]):
        self.plan = plan
        self.trace = trace


def _format_alert(alert: Alert) -> str:
    return (
        f"收到告警：\n"
        f"- 设备: {alert.hostname} ({alert.host_ip})\n"
        f"- 触发器: {alert.event_name}\n"
        f"- 详情: {alert.message}\n"
        f"- 严重: {alert.severity}\n"
        f"- 状态: {alert.status}\n"
        f"\n请先调用诊断工具收集事实，然后调用 propose_action 给出执行计划。"
    )


async def _execute_tool(name: str, raw_args: str) -> tuple[str, dict[str, Any]]:
    """执行一个工具调用，返回 (展示给 LLM 的字符串, 给 trace 用的结构化 dict)"""
    try:
        parsed_args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as exc:
        return f"tool args parse error: {exc}", {"error": "args parse error"}
    args = parsed_args if isinstance(parsed_args, dict) else {}

    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"unknown tool: {name}", {"error": "unknown tool"}

    try:
        result = await handler(**args)
        return result, {"args": args, "result_preview": result[:200]}
    except (ValueError, TypeError) as exc:
        # 入参校验失败，告诉 LLM 让它重试时纠正
        return f"tool input invalid: {exc}", {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"tool {name} crashed")
        return f"tool execution failed: {exc}", {"error": str(exc)}


class DiagnosticAgent:
    """基于 LLM tool calling 的告警诊断 Agent"""

    def __init__(
        self,
        llm_client: LLMClient,
        max_turns: int = 5,
        timeout_per_call: float = 60,
        past_cases_text: str = "",
        negative_cases_text: str = "",
    ) -> None:
        self.llm = llm_client
        self.max_turns = max_turns
        self.timeout_per_call = timeout_per_call
        self.past_cases_text = past_cases_text
        self.negative_cases_text = negative_cases_text

    async def diagnose(self, alert: Alert) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": _format_alert(alert)},
        ]
        trace: list[dict[str, Any]] = []

        for turn in range(self.max_turns):
            response = await self.llm.chat_with_tools(
                messages=messages,
                tools=DIAGNOSTIC_TOOLS,
                timeout=self.timeout_per_call,
            )
            tool_calls = response.get("tool_calls") or []

            if not tool_calls:
                # LLM 没调任何工具，只回了文本——这是没用的，直接报错
                content = response.get("content") or ""
                logger.warning(f"Agent turn {turn}: LLM returned text without tool call: {content[:200]}")
                raise AgentLimitExceeded(f"agent did not call any tool at turn {turn}; content={content[:200]!r}")

            # 把 assistant 这轮回复加进对话历史（必须，否则 tool 消息没法接 tool_call_id）
            # reasoning_content 是推理模型的 vendor 扩展，必须原样 roundtrip 否则 API 会 400
            messages.append(
                {
                    "role": "assistant",
                    "content": response.get("content"),
                    "tool_calls": tool_calls,
                    "reasoning_content": response.get("reasoning_content"),
                }
            )

            # 看是不是 propose_action（终止）
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                if fn_name == "propose_action":
                    try:
                        parsed_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError as exc:
                        raise AgentLimitExceeded(f"propose_action args parse error: {exc}") from exc
                    args = parsed_args if isinstance(parsed_args, dict) else {}
                    trace.append({"turn": turn, "tool": "propose_action", "args": args})
                    return _build_result(args, trace)

            # 不是 propose_action，那就执行所有诊断工具调用，把结果塞回去
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = tc["function"]["arguments"]
                logger.info(f"Agent turn {turn} calling {fn_name}({fn_args[:200]})")
                tool_output, trace_entry = await _execute_tool(fn_name, fn_args)
                trace.append({"turn": turn, "tool": fn_name, **trace_entry})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_output,
                    }
                )

        raise AgentLimitExceeded(f"agent did not converge in {self.max_turns} turns")

    def _system_prompt(self) -> str:
        """返回带 Hermes 历史案例的 system prompt。"""
        if not self.past_cases_text.strip() and not self.negative_cases_text.strip():
            return SYSTEM_PROMPT
        past_section = PAST_CASES_SECTION_TEMPLATE.format(
            past_cases_text=self.past_cases_text or "未找到历史相似案例。",
            negative_cases_text=self.negative_cases_text or "未找到人工标注反例。",
        )
        return f"{SYSTEM_PROMPT.rstrip()}\n{past_section}"


def _build_result(propose_args: dict[str, Any], trace: list[dict[str, Any]]) -> AgentResult:
    runbook_id = cast(str | None, propose_args.get("runbook_id"))
    if runbook_id == "none":
        return AgentResult(plan=None, trace=trace)

    params = propose_args.get("params")
    plan = ActionPlan(
        runbook_id=runbook_id or "",
        params=params if isinstance(params, dict) else {},
        risk_level=str(propose_args.get("risk_level", "medium")),
        requires_approval=True,
        reasoning=str(propose_args.get("reasoning", "")),
        trace=trace,
        confidence=float(propose_args.get("confidence", 0.0)),
    )
    return AgentResult(plan=plan, trace=trace)
