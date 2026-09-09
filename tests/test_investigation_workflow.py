import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from hinataops.agent_core.gateway import GatewayToolResult, ToolDescriptor
from hinataops.agent_core.models import IncidentRequest, ToolCall
from hinataops.agent_core.planner import InvestigationPlanner, PlanningContext, PlanningDecision
from hinataops.agent_core.policy import InvestigationBudget
from hinataops.agent_core.workflow import InvestigationWorkflow


class FakeToolGateway:
    """以两个只读 Tool 模拟一次可复现的 MCP 调查环境。"""

    def __init__(self, tools: list[str]) -> None:
        self._tools = tools
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> list[ToolDescriptor]:
        return [
            ToolDescriptor(name=name, description=name, input_schema={"type": "object"})
            for name in self._tools
        ]

    async def call_tool(self, call: ToolCall) -> GatewayToolResult:
        self.calls.append(call)
        source = "redis" if "stream" in call.name else "prometheus"
        return GatewayToolResult(
            value={
                "metadata": {
                    "source": source,
                    "observed_at": "2026-09-09T00:00:00+00:00",
                    "complete": True,
                    "warnings": [],
                    "error": None,
                },
                "tool": call.name,
            }
        )


class ScriptedPlanner(InvestigationPlanner):
    """按预设决策驱动图，用于验证确定性编排边界而非 LLM 质量。"""

    def __init__(self, decisions: list[PlanningDecision]) -> None:
        self._decisions = decisions
        self.contexts: list[PlanningContext] = []

    async def decide(self, context: PlanningContext) -> PlanningDecision:
        self.contexts.append(context)
        return self._decisions.pop(0)


def _incident() -> IncidentRequest:
    return IncidentRequest(
        incident_id=uuid4(),
        query="Python 判题任务长时间没有结果",
        target_environment="aoi-local",
    )


def test_workflow_collects_planned_readonly_evidence_then_finishes() -> None:
    gateway = FakeToolGateway(
        ["aoi_judge_get_stream_summary", "aoi_judge_get_runtime"]
    )
    planner = ScriptedPlanner(
        [
            PlanningDecision(
                tool_calls=[
                    ToolCall(
                        name="aoi_judge_get_stream_summary",
                        arguments={"language": "python"},
                    ),
                    ToolCall(name="aoi_judge_get_runtime"),
                ]
            ),
            PlanningDecision(finish_reason="关键证据已经齐全"),
        ]
    )
    workflow = InvestigationWorkflow(
        gateway,
        planner,
        InvestigationBudget(
            readonly_tool_names=frozenset(
                {"aoi_judge_get_stream_summary", "aoi_judge_get_runtime"}
            )
        ),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert [call.name for call in gateway.calls] == [
        "aoi_judge_get_stream_summary",
        "aoi_judge_get_runtime",
    ]
    assert len(result.observations) == 2
    assert result.investigation_rounds == 1
    assert result.stop_reason == "关键证据已经齐全"
    assert len(planner.contexts) == 2


def test_workflow_rejects_published_write_tool_before_gateway_execution() -> None:
    gateway = FakeToolGateway(["aoi_judge_get_stream_summary", "docker_restart_service"])
    planner = ScriptedPlanner(
        [
            PlanningDecision(
                tool_calls=[
                    ToolCall(
                        name="docker_restart_service",
                        arguments={"action_id": "not-approved"},
                    )
                ]
            )
        ]
    )
    workflow = InvestigationWorkflow(
        gateway,
        planner,
        InvestigationBudget(
            readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})
        ),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert gateway.calls == []
    assert "只读 Tool 白名单" in result.stop_reason


def test_workflow_stops_deterministically_when_total_tool_budget_is_exhausted() -> None:
    gateway = FakeToolGateway(["aoi_judge_get_stream_summary", "aoi_judge_get_runtime"])
    planner = ScriptedPlanner(
        [
            PlanningDecision(
                tool_calls=[
                    ToolCall(
                        name="aoi_judge_get_stream_summary",
                        arguments={"language": "python"},
                    )
                ]
            ),
            PlanningDecision(tool_calls=[ToolCall(name="aoi_judge_get_runtime")]),
        ]
    )
    workflow = InvestigationWorkflow(
        gateway,
        planner,
        InvestigationBudget(
            readonly_tool_names=frozenset(
                {"aoi_judge_get_stream_summary", "aoi_judge_get_runtime"}
            ),
            max_tool_calls=1,
        ),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert [call.name for call in gateway.calls] == ["aoi_judge_get_stream_summary"]
    assert result.stop_reason == "已达到 Tool 调用次数预算"
    assert len(planner.contexts) == 1
