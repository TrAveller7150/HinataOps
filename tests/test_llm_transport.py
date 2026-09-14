import asyncio
from types import SimpleNamespace

import pytest

from hinataops.agent_core.llm import OpenAICompatibleStructuredOutputClient


class FakeCompletions:
    """捕获 Chat Completions 参数，避免测试中产生真实网络请求。"""

    def __init__(self, content: str = '{"status":"ok"}') -> None:
        self.kwargs: dict[str, object] = {}
        self._content = content

    async def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                message=SimpleNamespace(refusal=None, content=self._content)
                )
            ]
        )


class FakeOpenAIClient:
    """只实现 Adapter 本次调用所需的最小 SDK 对象层级。"""

    def __init__(self, content: str = '{"status":"ok"}') -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def test_json_object_mode_supplies_example_and_keeps_core_validation_boundary() -> None:
    sdk_client = FakeOpenAIClient()
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        response_format_mode="json_object",
        client=sdk_client,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        client.create_json(
            system_prompt="你是测试器。",
            user_prompt="请返回结果。",
            schema_name="test_result",
            schema={"type": "object"},
            json_example='{"status":"ok"}',
        )
    )

    assert result == {"status": "ok"}
    assert sdk_client.completions.kwargs["response_format"] == {"type": "json_object"}
    messages = sdk_client.completions.kwargs["messages"]
    assert isinstance(messages, list)
    assert "输出格式示例" in messages[0]["content"]
    assert "JSON Schema" in messages[0]["content"]
    assert '"type":"object"' in messages[0]["content"]


def test_client_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        OpenAICompatibleStructuredOutputClient(
            model="deepseek-flash",
            timeout_seconds=0,
            client=FakeOpenAIClient(),  # type: ignore[arg-type]
        )


def test_json_object_mode_accepts_a_single_markdown_code_fence() -> None:
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
        response_format_mode="json_object",
        client=FakeOpenAIClient("```json\n{\"status\":\"ok\"}\n```"),  # type: ignore[arg-type]
    )

    result = asyncio.run(
        client.create_json(
            system_prompt="你是测试器。",
            user_prompt="请返回结果。",
            schema_name="test_result",
            schema={"type": "object"},
            json_example='{"status":"ok"}',
        )
    )

    assert result == {"status": "ok"}
