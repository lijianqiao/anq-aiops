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


def _build_openai_messages(messages: list[dict[str, Any]]) -> list[ChatCompletionMessageParam]:
    """转 OpenAI 消息格式。支持 system/user/assistant/developer/tool 五种 role；
    assistant 可携带 tool_calls；tool 必须带 tool_call_id。
    """
    openai_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role not in {"system", "user", "assistant", "developer", "tool"}:
            raise ValueError(f"Unsupported OpenAI chat role: {role!r}")

        out: dict[str, Any] = {"role": role, "content": message.get("content")}
        # assistant 在 tool 调用回合 content 可以为 None，但要带 tool_calls
        if role == "assistant" and message.get("tool_calls"):
            out["tool_calls"] = message["tool_calls"]
        if role == "tool":
            if "tool_call_id" not in message:
                raise ValueError("tool message must include tool_call_id")
            out["tool_call_id"] = message["tool_call_id"]
        openai_messages.append(out)

    return cast("list[ChatCompletionMessageParam]", openai_messages)


class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, timeout: float = 30) -> str:
        """发送消息，返回纯文本响应"""

    async def chat_json(
        self,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        model: str | None = None,
        timeout: float = 30,
    ) -> BaseModel:
        """发送消息，返回 Pydantic 模型"""
        text = await self.chat(messages, model=model, timeout=timeout)
        return schema.model_validate_json(_strip_json_fence(text))

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict],
        model: str | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """ReAct agent 用的多轮 tool calling 接口。

        返回 {"content": str | None, "tool_calls": list}，每个 tool_call 形如
        {"id": str, "type": "function", "function": {"name": str, "arguments": str}}
        """
        raise NotImplementedError("This client does not support tool calling")


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible API 客户端 (兼容 OpenAI, DeepSeek, llama.cpp 等)"""

    def __init__(self, base_url: str, api_key: str, default_model: str) -> None:
        from openai import AsyncOpenAI

        self.default_model = default_model
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, timeout: float = 30) -> str:
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
        messages: list[dict[str, Any]],
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

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict],
        model: str | None = None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        response = await self._client.chat.completions.create(
            model=model or self.default_model,
            messages=_build_openai_messages(messages),
            tools=tools,
            tool_choice="auto",
            timeout=timeout,
        )
        msg = response.choices[0].message
        return {
            "content": msg.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in (msg.tool_calls or [])
            ],
        }


class AnthropicClient(LLMClient):
    """Anthropic Claude API 客户端"""

    def __init__(self, api_key: str, default_model: str) -> None:
        from anthropic import AsyncAnthropic

        self.default_model = default_model
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(self, messages: list[dict[str, Any]], model: str | None = None, timeout: float = 30) -> str:
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
        # 寻找第一个 text block（避免未来出现 tool_use 时 content[0] 不是 text）
        for block in response.content:
            if getattr(block, "type", None) == "text":
                return block.text
        raise RuntimeError("Anthropic response has no text block")
