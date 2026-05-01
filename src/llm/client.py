from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    async def chat(self, messages: list[dict[str, str]], model: str | None = None, timeout: float = 30) -> str:
        """发送消息，返回纯文本响应"""

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        model: str | None = None,
        timeout: float = 30,
    ) -> BaseModel:
        """发送消息，返回 Pydantic 模型"""
        text = await self.chat(messages, model=model, timeout=timeout)
        return schema.model_validate_json(text)


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible API 客户端 (兼容 OpenAI, DeepSeek, llama.cpp 等)"""

    def __init__(self, base_url: str, api_key: str, default_model: str) -> None:
        from openai import AsyncOpenAI

        self.default_model = default_model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def chat(self, messages: list[dict[str, str]], model: str | None = None, timeout: float = 30) -> str:
        response = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=messages,
            timeout=timeout,
        )
        return response.choices[0].message.content


class AnthropicClient(LLMClient):
    """Anthropic Claude API 客户端"""

    def __init__(self, api_key: str, default_model: str) -> None:
        from anthropic import AsyncAnthropic

        self.default_model = default_model
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(self, messages: list[dict[str, str]], model: str | None = None, timeout: float = 30) -> str:
        system_msg = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                chat_messages.append(msg)

        kwargs: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": chat_messages,
            "max_tokens": 4096,
            "timeout": timeout,
        }
        if system_msg:
            kwargs["system"] = system_msg

        response = await self._client.messages.create(**kwargs)
        return response.content[0].text
