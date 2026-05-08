"""
@Author: li
@Email: lijianqiao2906@live.com
@FileName: router.py
@DateTime: 2026-05-08 14:31:00
@Docs: 提供 LLM 主备路由、熔断与 Agent 诊断降级能力
"""

import logging

from pydantic import BaseModel

from src.llm.agent import AgentResult, DiagnosticAgent
from src.llm.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.llm.client import LLMClient
from src.models import Alert

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """主备模型均不可用"""


class LLMRouter:
    """LLM 路由器：主备切换 + 熔断"""

    def __init__(
        self,
        primary: LLMClient,
        fallback: LLMClient,
        circuit_breaker: CircuitBreaker | None = None,
        timeout: float = 30,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.circuit_breaker = circuit_breaker
        self.timeout = timeout

    async def invoke(
        self,
        prompt: str,
        schema: type[BaseModel],
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ) -> BaseModel:
        messages = [{"role": "user", "content": prompt}]

        if self.circuit_breaker is None or self._primary_allowed():
            try:
                result = await self.primary.chat_json(
                    messages=messages, schema=schema, model=primary_model, timeout=self.timeout
                )
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()
                return result
            except Exception as e:
                logger.warning(f"Primary LLM failed: {e}")
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()

        try:
            result = await self.fallback.chat_json(
                messages=messages, schema=schema, model=fallback_model, timeout=self.timeout
            )
            return result
        except Exception as e:
            logger.error(f"Fallback LLM failed: {e}")
            raise LLMUnavailable("Both primary and fallback LLM failed") from e

    def select_client_for_agent(self) -> LLMClient:
        """给 ReAct agent 用：返回当前可用的 client（先主后备）。

        Agent 的多轮交互需要全程用同一个 client（保持 conversation 一致），
        所以这里只在开头选一次 client，不在 turn 之间切换。
        """
        if self.circuit_breaker is None or self._primary_allowed():
            return self.primary
        return self.fallback

    async def diagnose_with_agent(self, alert: Alert, max_turns: int = 5, past_cases_text: str = "") -> AgentResult:
        """使用 ReAct Agent 诊断告警，主模型失败时重试备用模型。

        Agent 的多轮 tool calling 会绑定单个客户端；因此失败后用备用模型重新开始一次诊断，
        不在同一轮对话中途切换客户端。
        """
        if self.circuit_breaker is None or self._primary_allowed():
            try:
                result = await DiagnosticAgent(
                    self.primary,
                    max_turns=max_turns,
                    past_cases_text=past_cases_text,
                ).diagnose(alert)
                if self.circuit_breaker:
                    self.circuit_breaker.record_success()
                return result
            except Exception as exc:
                logger.warning(f"Primary Agent LLM failed: {exc}")
                if self.circuit_breaker:
                    self.circuit_breaker.record_failure()

        try:
            return await DiagnosticAgent(
                self.fallback,
                max_turns=max_turns,
                past_cases_text=past_cases_text,
            ).diagnose(alert)
        except Exception as exc:
            logger.error(f"Fallback Agent LLM failed: {exc}")
            raise LLMUnavailable("主备模型均无法完成 Agent 诊断") from exc

    def _primary_allowed(self) -> bool:
        if self.circuit_breaker is None:
            return True
        try:
            self.circuit_breaker.check()
            return True
        except CircuitBreakerOpen:
            return False
