import asyncio
from types import SimpleNamespace

import pytest

from hinataops.agent_core.llm import OpenAICompatibleStructuredOutputClient
from hinataops.agent_core.planner import PlannerError


class FakeCompletions:
    """捕获 Chat Completions 参数，避免测试中产生真实网络请求。"""

    def __init__(
        self,
        content: str | list[str] = '{"status":"ok"}',
        *,
        finish_reason: str | None = "stop",
        reasoning_content: str | None = None,
    ) -> None:
        self.kwargs: dict[str, object] = {}
        self.calls: list[dict[str, object]] = []
        self._contents = [content] if isinstance(content, str) else content
        self._finish_reason = finish_reason
        self._reasoning_content = reasoning_content

    async def create(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        self.calls.append(kwargs)
        content = self._contents.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=self._finish_reason,
                    message=SimpleNamespace(
                        refusal=None,
                        content=content,
                        reasoning_content=self._reasoning_content,
                    ),
                )
            ]
        )


class FakeOpenAIClient:
    """只实现 Adapter 本次调用所需的最小 SDK 对象层级。"""

    def __init__(
        self,
        content: str | list[str] = '{"status":"ok"}',
        *,
        finish_reason: str | None = "stop",
        reasoning_content: str | None = None,
    ) -> None:
        self.completions = FakeCompletions(
            content,
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
        )
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
            schema={"type": "object", "required": ["status"]},
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


def test_client_retries_once_when_first_response_is_not_json() -> None:
    sdk_client = FakeOpenAIClient(["这不是 JSON", '{"status":"ok"}'])
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
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
    assert len(sdk_client.completions.calls) == 2
    retry_messages = sdk_client.completions.calls[1]["messages"]
    assert isinstance(retry_messages, list)
    assert "上一响应未通过结构化输出契约校验" in retry_messages[0]["content"]


def test_client_does_not_expose_raw_non_json_response_after_retry() -> None:
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
        response_format_mode="json_object",
        client=FakeOpenAIClient(["敏感调试内容", "仍然不是 JSON"]),  # type: ignore[arg-type]
    )

    with pytest.raises(PlannerError, match="响应字符数") as error:
        asyncio.run(
            client.create_json(
                system_prompt="你是测试器。",
                user_prompt="请返回结果。",
                schema_name="test_result",
                schema={"type": "object"},
                json_example='{"status":"ok"}',
            )
        )

    assert "敏感调试内容" not in str(error.value)


def test_client_retries_when_json_fails_semantic_contract_validation() -> None:
    sdk_client = FakeOpenAIClient(['{"status":"invalid"}', '{"status":"ok"}'])
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
        response_format_mode="json_object",
        client=sdk_client,  # type: ignore[arg-type]
    )

    output = asyncio.run(
        client.create_validated_json(
            system_prompt="你是测试器。",
            user_prompt="请返回结果。",
            schema_name="test_result",
            schema={"type": "object", "required": ["status"]},
            json_example='{"status":"ok"}',
            validate=lambda value: value
            if value["status"] == "ok"
            else (_ for _ in ()).throw(ValueError("status 必须为 ok")),
        )
    )

    assert output.value == {"status": "ok"}
    assert output.attempts == 2
    assert output.recovery_note == "第 2 次输出通过结构化契约校验。"
    messages = sdk_client.completions.calls[1]["messages"]
    assert isinstance(messages, list)
    assert "status 必须为 ok" in messages[0]["content"]
    assert "顶层 JSON 对象必须包含以下字段" in messages[0]["content"]


def test_prompted_json_mode_omits_response_format_for_incompatible_providers() -> None:
    sdk_client = FakeOpenAIClient()
    client = OpenAICompatibleStructuredOutputClient(
        model="provider-model",
        response_format_mode="prompted_json",
        client=sdk_client,  # type: ignore[arg-type]
    )

    asyncio.run(
        client.create_json(
            system_prompt="你是测试器。",
            user_prompt="请返回结果。",
            schema_name="test_result",
            schema={"type": "object"},
            json_example='{"status":"ok"}',
        )
    )

    assert "response_format" not in sdk_client.completions.kwargs


def test_client_records_safe_empty_content_metadata_without_reasoning_text() -> None:
    client = OpenAICompatibleStructuredOutputClient(
        model="deepseek-flash",
        response_format_mode="json_object",
        client=FakeOpenAIClient(
            ["", ""], finish_reason="length", reasoning_content="private reasoning"
        ),  # type: ignore[arg-type]
    )

    with pytest.raises(PlannerError, match="返回空内容") as error:
        asyncio.run(
            client.create_json(
                system_prompt="你是测试器。",
                user_prompt="请返回结果。",
                schema_name="test_result",
                schema={"type": "object"},
                json_example='{"status":"ok"}',
            )
        )

    message = str(error.value)
    assert "第 1 次" in message
    assert "第 2 次" in message
    assert "finish_reason=length" in message
    assert "reasoning_content=present" in message
    assert "private reasoning" not in message
