import asyncio
import json
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.gateway import GatewayToolResult, ToolCatalog, ToolDescriptor
from hinataops.agent_core.llm import LlmInvestigationPlanner, ModelPlanningDecision
from hinataops.agent_core.models import IncidentRequest, Observation, ToolCall
from hinataops.agent_core.planner import PlannerError, PlanningContext
from hinataops.agent_core.policy import InvestigationBudget
from hinataops.agent_core.workflow import InvestigationWorkflow


class FakeStructuredOutputClient:
    """记录 Prompt 与 Schema，替代真实模型来固定 Planner 的输入输出边界。"""

    def __init__(self, decision: dict[str, object]) -> None:
        self._decision = decision
        self.system_prompt = ""
        self.user_prompt = ""
        self.schema: dict[str, object] = {}

    async def create_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.schema = schema
        return self._decision


class CatalogOnlyGateway:
    """验证 Planner 失败时工作流不会把请求发送至 MCP Gateway。"""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(
                name="aoi_judge_get_stream_summary",
                description="Redis Stream 摘要",
                input_schema={"type": "object"},
            ),
            ToolDescriptor(
                name="docker_restart_service",
                description="重启服务",
                input_schema={"type": "object"},
            ),
        ]

    async def call_tool(self, call: ToolCall) -> GatewayToolResult:
        self.calls.append(call)
        raise AssertionError("非法 LLM 决策不能到达 MCP Gateway")


def _context() -> PlanningContext:
    catalog = ToolCatalog(
        [
            ToolDescriptor(
                name="aoi_judge_get_stream_summary",
                description="Redis Stream 摘要",
                input_schema={"type": "object"},
            ),
            ToolDescriptor(
                name="docker_restart_service",
                description="重启服务",
                input_schema={"type": "object"},
            ),
        ]
    )
    return PlanningContext(
        incident=IncidentRequest(query="Python 判题无结果", target_environment="aoi-local"),
        catalog=catalog,
        observations=[
            Observation(
                source="redis",
                tool_name="aoi_judge_get_stream_summary",
                arguments={"language": "sql"},
                observed_at=datetime(2026, 9, 9, tzinfo=UTC),
                value={"lag": 0},
                summary="SQL Stream 无积压。",
                reliability="complete",
            )
        ],
        completed_calls=[
            ToolCall(
                name="aoi_judge_get_stream_summary", arguments={"language": "sql"}
            )
        ],
        investigation_round=2,
    )


def test_llm_planner_exposes_only_allowed_tools_and_parses_decision() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [
                {
                    "name": "aoi_judge_get_stream_summary",
                    "arguments_json": '{"language":"python"}',
                }
            ],
            "finish_reason": None,
        }
    )
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    decision = asyncio.run(planner.decide(_context()))

    assert decision.tool_calls == [
        ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"})
    ]
    prompt = json.loads(client.user_prompt)
    assert prompt["incident"]["query"] == "Python 判题无结果"
    assert [tool["name"] for tool in prompt["allowed_readonly_tools"]] == [
        "aoi_judge_get_stream_summary"
    ]
    assert "docker_restart_service" not in client.user_prompt
    assert client.schema["additionalProperties"] is False
    assert set(client.schema["required"]) == {"tool_calls", "finish_reason"}


def test_llm_planner_rejects_invalid_structured_decision() -> None:
    client = FakeStructuredOutputClient({"tool_calls": [], "finish_reason": None})
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    with pytest.raises(PlannerError, match="结构化输出"):
        asyncio.run(planner.decide(_context()))


def test_workflow_stops_before_mcp_when_llm_selects_unallowed_tool() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [
                {"name": "docker_restart_service", "arguments_json": "{}"}
            ],
            "finish_reason": None,
        }
    )
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )
    gateway = CatalogOnlyGateway()
    workflow = InvestigationWorkflow(
        gateway,
        planner,
        InvestigationBudget(readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})),
    )

    result = asyncio.run(
        workflow.run(IncidentRequest(query="判题卡住", target_environment="aoi-local"))
    )

    assert gateway.calls == []
    assert "Planner 输出不可用" in result.stop_reason
    assert "未授权 Tool" in result.stop_reason


def test_llm_output_schema_keeps_dynamic_tool_arguments_as_a_json_string() -> None:
    schema = ModelPlanningDecision.model_json_schema()
    call_definition = schema["$defs"]["ModelToolCall"]

    assert call_definition["properties"]["arguments_json"]["type"] == "string"
    assert call_definition["additionalProperties"] is False
