import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel


@pytest.fixture(autouse=True)
def _mock_llm_deps():
    """Mock openai/anthropic packages since they are not installed yet."""
    mock_openai = MagicMock()
    mock_anthropic = MagicMock()
    with patch.dict(sys.modules, {"openai": mock_openai, "anthropic": mock_anthropic}):
        yield


from src.llm.client import AnthropicClient, LLMClient, OpenAICompatibleClient


class SampleResponse(BaseModel):
    answer: str
    score: float


def test_openai_client_inherits_llm_client():
    client = OpenAICompatibleClient(base_url="http://test", api_key="test", default_model="gpt-4o")
    assert isinstance(client, LLMClient)


def test_anthropic_client_inherits_llm_client():
    client = AnthropicClient(api_key="test", default_model="claude-sonnet-4-20250514")
    assert isinstance(client, LLMClient)


@pytest.mark.asyncio
async def test_openai_client_chat_json():
    client = OpenAICompatibleClient(base_url="http://test", api_key="test", default_model="gpt-4o")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"answer": "yes", "score": 0.9}'

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await client.chat_json(
            messages=[{"role": "user", "content": "test"}],
            schema=SampleResponse,
        )

    assert isinstance(result, SampleResponse)
    assert result.answer == "yes"
    assert result.score == 0.9


@pytest.mark.asyncio
async def test_openai_client_chat_json_timeout():
    client = OpenAICompatibleClient(base_url="http://test", api_key="test", default_model="gpt-4o")

    with patch.object(client, "_client") as mock_openai:
        mock_openai.chat.completions.create = AsyncMock(side_effect=TimeoutError("timeout"))
        with pytest.raises(TimeoutError):
            await client.chat_json(
                messages=[{"role": "user", "content": "test"}],
                schema=SampleResponse,
                timeout=1,
            )
