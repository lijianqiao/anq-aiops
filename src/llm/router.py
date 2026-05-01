import logging

from pydantic import BaseModel

from src.llm.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.llm.client import LLMClient

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

    def _primary_allowed(self) -> bool:
        try:
            self.circuit_breaker.check()
            return True
        except CircuitBreakerOpen:
            return False
