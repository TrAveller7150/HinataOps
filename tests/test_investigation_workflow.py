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


class RetryableGateway(FakeToolGateway):
    """第一次返回 MCP 明确可重试的部分证据，第二次返回完整证据。"""

    async def call_tool(self, call: ToolCall) -> GatewayToolResult:
        self.calls.append(call)
        complete = len(self.calls) == 2
        return GatewayToolResult(
            value={
                "metadata": {
                    "source": "prometheus",
                    "observed_at": "2026-09-09T00:00:00+00:00",
                    "complete": complete,
                    "warnings": [],
                    "error": None
                    if complete
                    else {"kind": "timeout", "message": "超时", "retryable": True},
                },
                "tool": call.name,
            }
        )


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
                ],
                compatibility_note="已忽略非执行兼容字段 decision_summary_note。",
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
    assert [item.status for item in result.planning_traces] == ["planned", "finished"]
    assert result.planning_traces[0].tool_calls == [
        ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"}),
        ToolCall(name="aoi_judge_get_runtime"),
    ]
    assert result.planning_traces[0].compatibility_note == (
        "已忽略非执行兼容字段 decision_summary_note。"
    )


def test_workflow_emits_display_events_without_changing_evidence_collection() -> None:
    """展示层只消费事件，不能改变 Planner、预算或 MCP Tool 执行结果。"""
    call = ToolCall(name="aoi_judge_get_runtime")
    received: list[str] = []
    workflow = InvestigationWorkflow(
        FakeToolGateway([call.name]),
        ScriptedPlanner(
            [
                PlanningDecision(tool_calls=[call]),
                PlanningDecision(finish_reason="关键证据已经齐全"),
            ]
        ),
        InvestigationBudget(readonly_tool_names=frozenset({call.name})),
        event_listener=lambda event: received.append(event.kind),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert result.stop_reason == "关键证据已经齐全"
    assert received == [
        "catalog_loaded",
        "tools_planned",
        "tools_started",
        "tool_completed",
        "planning_finished",
        "diagnosis_started",
        "diagnosis_completed",
    ]


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


def test_workflow_allows_one_closing_decision_after_evidence_round_limit() -> None:
    """四批采证后仍应允许 Planner 解释性结束，而不是直接标记轮次预算耗尽。"""
    stream = "aoi_judge_get_stream_summary"
    runtime = "aoi_judge_get_runtime"
    planner = ScriptedPlanner(
        [
            PlanningDecision(tool_calls=[ToolCall(name=stream, arguments={"language": "python"})]),
            PlanningDecision(tool_calls=[ToolCall(name=runtime)]),
            PlanningDecision(tool_calls=[ToolCall(name=stream, arguments={"language": "sql"})]),
            PlanningDecision(tool_calls=[ToolCall(name=runtime, arguments={"scope": "all"})]),
            PlanningDecision(finish_reason="已完成四批采证，结束调查。"),
        ]
    )
    workflow = InvestigationWorkflow(
        FakeToolGateway([stream, runtime]),
        planner,
        InvestigationBudget(
            readonly_tool_names=frozenset({stream, runtime}),
            max_rounds=4,
            max_tool_calls=7,
        ),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert result.investigation_rounds == 4
    assert result.stop_reason == "已完成四批采证，结束调查。"
    assert [trace.status for trace in result.planning_traces] == [
        "planned",
        "planned",
        "planned",
        "planned",
        "finished",
    ]
    assert planner.contexts[-1].remaining_tool_calls == 0
    assert planner.contexts[-1].remaining_evidence_rounds == 0


def test_workflow_retries_one_explicitly_retryable_partial_observation() -> None:
    call = ToolCall(name="aoi_judge_get_runtime")
    gateway = RetryableGateway([call.name])
    workflow = InvestigationWorkflow(
        gateway,
        ScriptedPlanner(
            [
                PlanningDecision(tool_calls=[call]),
                PlanningDecision(tool_calls=[call]),
                PlanningDecision(finish_reason="关键证据已经齐全"),
            ]
        ),
        InvestigationBudget(readonly_tool_names=frozenset({call.name}), max_tool_calls=3),
    )

    result = asyncio.run(workflow.run(_incident()))

    assert gateway.calls == [call, call]
    assert [item.reliability for item in result.observations] == ["partial", "complete"]
    assert result.retry_attempts[0].attempt == 2
    assert result.retry_attempts[0].reason == "timeout"
    assert result.retry_attempts[0].backoff_seconds == 1.0
    assert result.stop_reason == "关键证据已经齐全"
