import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from hinataops.agent_core.models import ToolCall
from hinataops.agent_core.tool_provider import ToolProviderError
from hinataops.integrations.mcp_provider import McpToolProvider
from hinataops.tooling.contracts import ToolError


def test_mcp_provider_accepts_v3_tool_result_envelope() -> None:
    result = McpToolProvider._to_tool_result(
        {
            "schema_version": "1",
            "status": "success",
            "environment": "fixture",
            "source": "loki",
            "observed_at": datetime(2026, 9, 14, tzinfo=UTC),
            "data": {"matches": 2},
            "warnings": [],
            "error": None,
        },
        "logs_search",
    )

    assert result.status == "success"
    assert result.source == "loki"
    assert result.data == {"matches": 2}


def test_mcp_provider_maps_legacy_v2_metadata_only_inside_adapter() -> None:
    result = McpToolProvider._to_tool_result(
        {
            "metadata": {
                "environment": "aoi-local",
                "source": "redis",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "complete": False,
                "warnings": ["Consumer Group 不存在"],
            },
            "stream_key": "judge-tasks-python",
        },
        "redis_stream_summary",
    )

    assert result.status == "partial"
    assert result.data == {"stream_key": "judge-tasks-python"}
    assert result.warnings == ["Consumer Group 不存在"]
    assert result.error is None


def test_mcp_provider_treats_legacy_error_with_placeholder_fields_as_error() -> None:
    result = McpToolProvider._to_tool_result(
        {
            "metadata": {
                "environment": "aoi-local",
                "source": "docker",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "complete": False,
                "error": {
                    "kind": "ssh_command_timeout",
                    "message": "Docker 查询超时",
                    "retryable": True,
                },
            },
            "services": [],
            "sandbox_pool_running": 0,
        },
        "docker_runtime",
    )

    assert result.status == "error"
    assert result.data == {}
    assert result.error is not None
    assert result.error.retryable is True


def test_mcp_provider_rejects_unexplained_incomplete_legacy_data_as_evidence() -> None:
    result = McpToolProvider._to_tool_result(
        {
            "metadata": {
                "environment": "aoi-local",
                "source": "prometheus",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "complete": False,
            },
            "runtimes": [],
        },
        "judge_runtime",
    )

    assert result.status == "error"
    assert result.data == {}
    assert result.error is not None
    assert result.error.kind == "incomplete_result"


def test_mcp_provider_does_not_confuse_legacy_business_status_with_envelope() -> None:
    result = McpToolProvider._to_tool_result(
        {
            "metadata": {
                "environment": "fixture",
                "source": "docker",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "complete": True,
            },
            "status": "healthy",
        },
        "docker_runtime",
    )

    assert result.status == "success"
    assert result.data == {"status": "healthy"}


def test_mcp_provider_rejects_invalid_result_envelope() -> None:
    with pytest.raises(ToolProviderError, match="ToolResult 契约"):
        McpToolProvider._to_tool_result(
            {
                "schema_version": "1",
                "status": "error",
                "environment": "fixture",
                "source": "docker",
                "observed_at": "2026-09-14T00:00:00+00:00",
                "data": {"state": "unknown"},
            },
            "docker_runtime",
        )


def test_mcp_provider_exposes_protocol_error_through_public_invoke() -> None:
    class ProtocolErrorSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            return SimpleNamespace(isError=True, structuredContent=None, content=[])

    class ProtocolErrorProvider(McpToolProvider):
        @asynccontextmanager
        async def _session(self):
            yield ProtocolErrorSession()

    with pytest.raises(ToolProviderError, match="协议错误") as captured:
        asyncio.run(
            ProtocolErrorProvider("http://fixture/mcp").invoke(
                ToolCall(name="docker_runtime")
            )
        )

    assert captured.value.kind == "mcp_protocol_error"
    assert captured.value.retryable is False


def test_mcp_provider_returns_v3_result_through_public_invoke() -> None:
    class SuccessfulSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            return SimpleNamespace(
                isError=False,
                structuredContent={
                    "schema_version": "1",
                    "status": "no_data",
                    "environment": "fixture",
                    "source": "mysql",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "data": {"tasks": []},
                },
                content=[],
            )

    class SuccessfulProvider(McpToolProvider):
        @asynccontextmanager
        async def _session(self):
            yield SuccessfulSession()

    result = asyncio.run(
        SuccessfulProvider("http://fixture/mcp").invoke(
            ToolCall(name="mysql_pipeline_summary")
        )
    )

    assert result.status == "no_data"
    assert result.data == {"tasks": []}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ([TextContent(type="text", text="not-json")], "不是 JSON"),
        (
            [
                TextContent(type="text", text="{}"),
                TextContent(type="text", text="{}"),
            ],
            "未返回单个",
        ),
    ],
)
def test_mcp_provider_rejects_invalid_text_result_through_public_invoke(
    content: list[TextContent], message: str
) -> None:
    class InvalidTextSession:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            return SimpleNamespace(
                isError=False,
                structuredContent=None,
                content=content,
            )

    class InvalidTextProvider(McpToolProvider):
        @asynccontextmanager
        async def _session(self):
            yield InvalidTextSession()

    with pytest.raises(ToolProviderError, match=message):
        asyncio.run(
            InvalidTextProvider("http://fixture/mcp").invoke(
                ToolCall(name="docker_runtime")
            )
        )


def test_mcp_provider_rejects_legacy_metadata_with_non_boolean_complete() -> None:
    with pytest.raises(ToolProviderError, match="V2 metadata 无效"):
        McpToolProvider._to_tool_result(
            {
                "metadata": {
                    "environment": "aoi-local",
                    "source": "docker",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "complete": "false",
                }
            },
            "docker_runtime",
        )


def test_mcp_provider_rejects_contradictory_legacy_metadata() -> None:
    with pytest.raises(ToolProviderError, match="状态矛盾"):
        McpToolProvider._to_tool_result(
            {
                "metadata": {
                    "environment": "aoi-local",
                    "source": "docker",
                    "observed_at": "2026-09-14T00:00:00+00:00",
                    "complete": True,
                    "error": {
                        "kind": "target_unavailable",
                        "message": "目标不可用",
                        "retryable": False,
                    },
                }
            },
            "docker_runtime",
        )
