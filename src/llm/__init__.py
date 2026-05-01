from src.config import settings
from src.llm.circuit_breaker import CircuitBreaker
from src.llm.client import AnthropicClient, LLMClient, OpenAICompatibleClient
from src.llm.router import LLMRouter


def _create_client(provider: str, base_url: str, api_key: str, model: str) -> LLMClient:
    if provider == "anthropic":
        return AnthropicClient(api_key=api_key, default_model=model)
    return OpenAICompatibleClient(base_url=base_url, api_key=api_key, default_model=model)


def create_llm_router() -> LLMRouter:
    primary = _create_client(
        settings.llm_primary_provider,
        settings.llm_primary_base_url,
        settings.llm_primary_api_key,
        settings.llm_primary_model,
    )
    fallback = _create_client(
        settings.llm_fallback_provider,
        settings.llm_fallback_base_url,
        settings.llm_fallback_api_key,
        settings.llm_fallback_model,
    )
    cb = CircuitBreaker(
        threshold=settings.llm_circuit_breaker_threshold,
        window_sec=300,
    )
    return LLMRouter(primary=primary, fallback=fallback, circuit_breaker=cb, timeout=settings.llm_timeout)


__all__ = ["LLMRouter", "create_llm_router"]
