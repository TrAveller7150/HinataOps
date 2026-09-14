import asyncio
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.evidence import EvidenceCollector
from hinataops.agent_core.models import ToolCall
from hinataops.agent_core.tool_provider import ToolCatalog, ToolProviderError
from hinataops.tooling.contracts import ToolDefinition, ToolError, ToolResult

class FakeToolProvider:
    """验证 Core 端口不依赖 MCP SDK 或 MCP 返回类型。"""

    def __init__(self, result: ToolResult | None = None) -> None:
        self.calls: list[ToolCall] = []
        self.result = result or ToolResult(
            status="success",
            environment="test",
            source="redis",
            observed_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
            data={"lag": 0},
        )

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="redis_stream_summary",
                description="读取受限 Redis Stream 摘要。",
                input_schema={
                    "type": "object",
                    "properties": {"language": {"enum": ["python", "sql"]}},
                },
            )
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return self.result


def test_catalog_discovers_tools_through_transport_neutral_provider() -> None:
    catalog = asyncio.run(ToolCatalog.load(FakeToolProvider()))

    assert catalog.require("redis_stream_summary").description == "读取受限 Redis Stream 摘要。"
    with pytest.raises(ToolProviderError, match="未发布"):
        catalog.require("docker_restart_service")


def test_provider_invocation_uses_shared_tool_result() -> None:
    provider = FakeToolProvider()
    call = ToolCall(name="redis_stream_summary", arguments={"language": "python"})

    result = asyncio.run(provider.invoke(call))

    assert provider.calls == [call]
    assert result.status == "success"
    assert result.data == {"lag": 0}


def test_evidence_collector_consumes_tool_result_without_mcp_metadata() -> None:
    provider = FakeToolProvider()
    call = ToolCall(name="redis_stream_summary", arguments={"language": "python"})

    observation = asyncio.run(EvidenceCollector(provider).collect(call))

    assert observation.result_status == "success"
    assert observation.reliability == "complete"
    assert observation.value == {"lag": 0}
    assert observation.error is None


def test_evidence_preserves_no_data_and_retryable_error_semantics() -> None:
    no_data_provider = FakeToolProvider(
        ToolResult(
            status="no_data",
            environment="test",
            source="mysql",
            observed_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
            data={"window_minutes": 15, "tasks": []},
        )
    )
    no_data = asyncio.run(
        EvidenceCollector(no_data_provider).collect(
            ToolCall(name="mysql_pipeline_summary", arguments={"window_minutes": 15})
        )
    )

    assert no_data.result_status == "no_data"
    assert no_data.reliability == "complete"
    assert no_data.error is None

    tool_error = ToolError(
        kind="ssh_command_timeout",
        message="Docker 状态查询超时",
        retryable=True,
    )
    error_provider = FakeToolProvider(
        ToolResult(
            status="error",
            environment="test",
            source="docker",
            observed_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC),
            data={},
            error=tool_error,
        )
    )
    failed = asyncio.run(
        EvidenceCollector(error_provider).collect(ToolCall(name="docker_runtime"))
    )

    assert failed.result_status == "error"
    assert failed.reliability == "failed"
    assert failed.error == tool_error


def test_catalog_rejects_duplicate_tool_names() -> None:
    class DuplicateProvider(FakeToolProvider):
        async def list_tools(self) -> list[ToolDefinition]:
            definition = ToolDefinition(
                name="redis_stream_summary",
                description=None,
                input_schema={"type": "object"},
            )
            return [definition, definition]

    with pytest.raises(ToolProviderError, match="重复"):
        asyncio.run(ToolCatalog.load(DuplicateProvider()))
