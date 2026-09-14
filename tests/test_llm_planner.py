import asyncio
import json
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.gateway import GatewayToolResult, ToolCatalog, ToolDescriptor
from hinataops.agent_core.llm import (
    LlmInvestigationPlanner,
    ModelPlanningDecision,
    ValidatedStructuredOutput,
)
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

    async def create_validated_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
        validate,
    ) -> ValidatedStructuredOutput:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.schema = schema
        try:
            value = validate(self._decision)
        except (TypeError, ValueError) as error:
            raise PlannerError("LLM 结构化输出不符合调查决策契约") from error
        return ValidatedStructuredOutput(value=value, attempts=1)


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
        remaining_tool_calls=5,
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
            "finish_reason_code": None,
            "decision_summary": "当前只有 SQL 队列证据，需检查 Python 队列。",
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
    assert "只读运维调查规划器" in client.system_prompt
    assert "决策摘要" in client.system_prompt
    assert client.schema["additionalProperties"] is False
    assert set(client.schema["required"]) == {
        "tool_calls",
        "finish_reason_code",
        "decision_summary",
    }


def test_llm_planner_rejects_invalid_structured_decision() -> None:
    client = FakeStructuredOutputClient({"tool_calls": [], "finish_reason_code": None})
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    with pytest.raises(PlannerError, match="结构化输出"):
        asyncio.run(planner.decide(_context()))


def test_llm_planner_compatibly_accepts_known_decision_summary_note() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [],
            "finish_reason_code": "evidence_sufficient",
            "decision_summary_note": "已完成必要采证。",
        }
    )
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    decision = asyncio.run(planner.decide(_context()))

    assert decision.decision_summary == "已完成必要采证。"
    assert decision.compatibility_note == "已忽略非执行兼容字段 decision_summary_note。"


def test_llm_planner_still_rejects_unknown_extra_fields() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [],
            "finish_reason_code": "evidence_sufficient",
            "decision_summary": "已完成必要采证。",
            "unexpected_note": "不能静默兼容。",
        }
    )
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    with pytest.raises(PlannerError, match="调查决策契约"):
        asyncio.run(planner.decide(_context()))


def test_workflow_stops_before_mcp_when_llm_selects_unallowed_tool() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [
                {"name": "docker_restart_service", "arguments_json": "{}"}
            ],
            "finish_reason_code": None,
            "decision_summary": "尝试选择重启服务。",
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
    assert "调查决策契约" in result.stop_reason
    assert result.planning_traces[0].status == "failed"
    assert "调查决策契约" in result.planning_traces[0].summary


def test_llm_output_schema_keeps_dynamic_tool_arguments_as_a_json_string() -> None:
    schema = ModelPlanningDecision.model_json_schema()
    call_definition = schema["$defs"]["ModelToolCall"]

    assert call_definition["properties"]["arguments_json"]["type"] == "string"
    assert call_definition["additionalProperties"] is False


def test_llm_planner_maps_finish_code_to_a_deterministic_stop_reason() -> None:
    client = FakeStructuredOutputClient(
        {
            "tool_calls": [],
            "finish_reason_code": "evidence_sufficient",
            "decision_summary": "已有证据已经足够。",
        }
    )
    planner = LlmInvestigationPlanner(
        client, readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
    )

    decision = asyncio.run(planner.decide(_context()))

    assert decision.finish_reason == "模型认为现有证据已足够，结束调查。"
