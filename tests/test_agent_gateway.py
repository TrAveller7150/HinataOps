import asyncio
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.evidence import EvidenceCollector
from hinataops.agent_core.models import ToolCall
from hinataops.agent_core.tool_provider import ToolCatalog, ToolProviderError
from hinataops.tooling.contracts import ToolDefinition, ToolResult


class FakeToolProvider:
    """以固定 Tool 结果验证 Agent Core，不依赖 MCP SDK 或 HTTP Server。"""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="aoi_judge_get_stream_summary",
                description="返回判题 Stream 消费进度。",
                input_schema={
                    "type": "object",
                    "properties": {"language": {"enum": ["python", "sql"]}},
                },
            )
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(
            status="success",
            environment="aoi-local",
            source="redis",
            observed_at=datetime(2026, 9, 9, tzinfo=UTC),
            data={"consumer_group": {"lag": 12}},
        )


def test_catalog_loads_published_tool_schema_and_rejects_unknown_tool() -> None:
    catalog = asyncio.run(ToolCatalog.load(FakeToolProvider()))

    assert catalog.require("aoi_judge_get_stream_summary").input_schema["type"] == "object"
    with pytest.raises(ToolProviderError, match="未发布"):
        catalog.require("docker_restart_service")


def test_collector_converts_tool_result_to_observation() -> None:
    provider = FakeToolProvider()
    collector = EvidenceCollector(provider)
    call = ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"})

    observation = asyncio.run(collector.collect(call))

    assert provider.calls == [call]
    assert observation.source == "redis"
    assert observation.observed_at == datetime(2026, 9, 9, tzinfo=UTC)
    assert observation.result_status == "success"
    assert observation.reliability == "complete"
    assert observation.value["consumer_group"] == {"lag": 12}


def test_collector_marks_partial_tool_result_as_partial_evidence() -> None:
    class PartialProvider(FakeToolProvider):
        async def invoke(self, call: ToolCall) -> ToolResult:
            return ToolResult(
                status="partial",
                environment="aoi-local",
                source="prometheus",
                observed_at=datetime(2026, 9, 9, tzinfo=UTC),
                data={"worker_up": True},
                warnings=["部分指标缺失"],
            )

    observation = asyncio.run(
        EvidenceCollector(PartialProvider()).collect(ToolCall(name="aoi_judge_get_runtime"))
    )

    assert observation.result_status == "partial"
    assert observation.reliability == "partial"
    assert "部分指标缺失" in observation.summary
