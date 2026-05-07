import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam

JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def _strip_json_fence(text: str) -> str:
    match = JSON_FENCE_RE.match(text)
    return match.group(1).strip() if match else text.strip()


def _build_openai_messages(messages: list[dict[str, str]]) -> list[ChatCompletionMessageParam]:
    openai_messages: list[dict[str, str]] = []
    for message in messages:
        role = message["role"]
        content = message["content"]

        if role not in {"system", "user", "assistant", "developer"}:
            raise ValueError(f"Unsupported OpenAI chat role: {role!r}")
        openai_messages.append({"role": role, "content": content})

    return cast("list[ChatCompletionMessageParam]", openai_messages)


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
        return schema.model_validate_json(_strip_json_fence(text))


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible API 客户端 (兼容 OpenAI, DeepSeek, llama.cpp 等)"""

    def __init__(self, base_url: str, api_key: str, default_model: str) -> None:
        from openai import AsyncOpenAI

        self.default_model = default_model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def chat(self, messages: list[dict[str, str]], model: str | None = None, timeout: float = 30) -> str:
        response = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=_build_openai_messages(messages),
            timeout=timeout,
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("LLM response content is empty")
        return content

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        model: str | None = None,
        timeout: float = 30,
    ) -> BaseModel:
        response = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=_build_openai_messages(messages),
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("LLM response content is empty")
        return schema.model_validate_json(_strip_json_fence(content))


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
