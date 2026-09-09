import asyncio
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.evidence import EvidenceCollector
from hinataops.agent_core.gateway import (
    GatewayToolResult,
    ToolCatalog,
    ToolDescriptor,
    ToolGatewayError,
)
from hinataops.agent_core.models import ToolCall


class FakeToolGateway:
    """以固定 MCP 返回值验证 Agent Core，不依赖真实 HTTP Server。"""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="aoi_judge_get_stream_summary",
                description="返回判题 Stream 消费进度。",
                input_schema={"type": "object", "properties": {"language": {"enum": ["python", "sql"]}}},
            )
        ]

    async def call_tool(self, call: ToolCall) -> GatewayToolResult:
        self.calls.append(call)
        return GatewayToolResult(
            value={
                "metadata": {
                    "source": "redis",
                    "observed_at": "2026-09-09T00:00:00+00:00",
                    "complete": True,
                    "warnings": [],
                    "error": None,
                },
                "consumer_group": {"lag": 12},
            }
        )


def test_catalog_loads_published_tool_schema_and_rejects_unknown_tool() -> None:
    catalog = asyncio.run(ToolCatalog.load(FakeToolGateway()))

    assert catalog.require("aoi_judge_get_stream_summary").input_schema["type"] == "object"
    with pytest.raises(ToolGatewayError, match="未发布"):
        catalog.require("docker_restart_service")


def test_collector_converts_structured_mcp_result_to_observation() -> None:
    gateway = FakeToolGateway()
    collector = EvidenceCollector(gateway)
    call = ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"})

    observation = asyncio.run(collector.collect(call))

    assert gateway.calls == [call]
    assert observation.source == "redis"
    assert observation.observed_at == datetime(2026, 9, 9, tzinfo=UTC)
    assert observation.reliability == "complete"
    assert observation.value["consumer_group"] == {"lag": 12}


def test_collector_marks_incomplete_mcp_result_as_partial_evidence() -> None:
    class PartialGateway(FakeToolGateway):
        async def call_tool(self, call: ToolCall) -> GatewayToolResult:
            return GatewayToolResult(
                value={
                    "metadata": {
                        "source": "prometheus",
                        "observed_at": "2026-09-09T00:00:00+00:00",
                        "complete": False,
                        "warnings": ["部分指标缺失"],
                        "error": None,
                    }
                }
            )

    observation = asyncio.run(
        EvidenceCollector(PartialGateway()).collect(
            ToolCall(name="aoi_judge_get_runtime")
        )
    )

    assert observation.reliability == "partial"
    assert "部分指标缺失" in observation.summary


def test_collector_rejects_result_without_traceable_metadata() -> None:
    class MetadataMissingGateway(FakeToolGateway):
        async def call_tool(self, call: ToolCall) -> GatewayToolResult:
            return GatewayToolResult(value={"consumer_group": {"lag": 12}})

    with pytest.raises(ValueError, match="缺少 metadata"):
        asyncio.run(
            EvidenceCollector(MetadataMissingGateway()).collect(
                ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"})
            )
        )
