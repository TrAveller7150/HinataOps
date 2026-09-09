"""基于 LangGraph 的受预算调查循环。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from hinataops.agent_core.evidence import EvidenceCollector
from hinataops.agent_core.gateway import ToolCatalog, ToolGateway, ToolGatewayError
from hinataops.agent_core.models import IncidentRequest, Observation, ToolCall
from hinataops.agent_core.planner import InvestigationPlanner, PlanningContext
from hinataops.agent_core.policy import InvestigationBudget, InvestigationPolicyError


class _WorkflowState(TypedDict):
    """LangGraph 运行时状态；仅保存可审查的领域对象与确定性控制字段。"""

    incident: IncidentRequest
    catalog: ToolCatalog | None
    observations: list[Observation]
    completed_calls: list[ToolCall]
    planned_calls: list[ToolCall]
    investigation_rounds: int
    stop_reason: str | None


class InvestigationRun:
    """一次图运行的只读结果，后续 P3.4 将据此生成正式调查报告。"""

    def __init__(
        self,
        *,
        incident: IncidentRequest,
        observations: list[Observation],
        completed_calls: list[ToolCall],
        investigation_rounds: int,
        stop_reason: str,
    ) -> None:
        self.incident = incident
        self.observations = observations
        self.completed_calls = completed_calls
        self.investigation_rounds = investigation_rounds
        self.stop_reason = stop_reason


class InvestigationWorkflow:
    """协调 Planner、预算策略和 MCP EvidenceCollector 的只读调查图。"""

    def __init__(
        self,
        gateway: ToolGateway,
        planner: InvestigationPlanner,
        budget: InvestigationBudget,
    ) -> None:
        self._gateway = gateway
        self._planner = planner
        self._budget = budget
        self._collector = EvidenceCollector(gateway)
        self._graph = self._build_graph()

    async def run(self, incident: IncidentRequest) -> InvestigationRun:
        """执行完整调查；所有退出路径均携带可解释的 stop_reason。"""
        result = await self._graph.ainvoke(
            {
                "incident": incident,
                "catalog": None,
                "observations": [],
                "completed_calls": [],
                "planned_calls": [],
                "investigation_rounds": 0,
                "stop_reason": None,
            }
        )
        return InvestigationRun(
            incident=result["incident"],
            observations=result["observations"],
            completed_calls=result["completed_calls"],
            investigation_rounds=result["investigation_rounds"],
            stop_reason=result["stop_reason"] or "调查图异常结束",
        )

    def _build_graph(self):
        """构建显式循环，路由由预算和状态决定而非由 Planner 直接控制。"""
        graph = StateGraph(_WorkflowState)
        graph.add_node("load_catalog", self._load_catalog)
        graph.add_node("plan", self._plan)
        graph.add_node("authorize", self._authorize)
        graph.add_node("execute", self._execute)
        graph.add_edge(START, "load_catalog")
        graph.add_edge("load_catalog", "plan")
        graph.add_edge("plan", "authorize")
        graph.add_conditional_edges(
            "authorize",
            self._after_authorize,
            {"execute": "execute", "finish": END},
        )
        graph.add_conditional_edges(
            "execute",
            self._after_execute,
            {"plan": "plan", "finish": END},
        )
        return graph.compile()

    async def _load_catalog(self, state: _WorkflowState) -> dict[str, object]:
        """在图开始时加载 MCP 当前公开能力，避免硬编码领域 Tool 名称。"""
        try:
            catalog = await ToolCatalog.load(self._gateway)
        except Exception as error:
            return {"stop_reason": f"无法加载 MCP Tool Catalog: {type(error).__name__}"}
        return {"catalog": catalog}

    async def _plan(self, state: _WorkflowState) -> dict[str, object]:
        """让 Planner 基于现有证据选择下一轮检查，但不授予其执行权限。"""
        catalog = state["catalog"]
        if catalog is None:
            return {"stop_reason": "MCP Tool Catalog 不可用"}
        if state["investigation_rounds"] >= self._budget.max_rounds:
            return {"stop_reason": "已达到调查轮次预算"}
        if len(state["completed_calls"]) >= self._budget.max_tool_calls:
            return {"stop_reason": "已达到 Tool 调用次数预算"}
        decision = await self._planner.decide(
            PlanningContext(
                incident=state["incident"],
                catalog=catalog,
                observations=state["observations"],
                completed_calls=state["completed_calls"],
                investigation_round=state["investigation_rounds"] + 1,
            )
        )
        if not decision.tool_calls:
            return {"stop_reason": decision.finish_reason}
        return {"planned_calls": decision.tool_calls}

    def _authorize(self, state: _WorkflowState) -> dict[str, object]:
        """逐项校验 Catalog、只读白名单、重复调用和预算，失败时不触碰 Gateway。"""
        catalog = state["catalog"]
        if state["stop_reason"] is not None or catalog is None:
            return {}
        staged_calls = list(state["completed_calls"])
        try:
            for call in state["planned_calls"]:
                catalog.require(call.name)
                self._budget.authorize(
                    call,
                    completed_calls=staged_calls,
                    investigation_round=state["investigation_rounds"] + 1,
                )
                staged_calls.append(call)
        except (InvestigationPolicyError, ToolGatewayError) as error:
            return {"stop_reason": str(error), "planned_calls": []}
        return {}

    async def _execute(self, state: _WorkflowState) -> dict[str, object]:
        """并发采集已经授权的只读 Tool，并把传输失败降级为失败证据。"""
        calls = state["planned_calls"]
        results = await asyncio.gather(
            *(self._collector.collect(call) for call in calls), return_exceptions=True
        )
        observations = list(state["observations"])
        for call, result in zip(calls, results, strict=True):
            if isinstance(result, Exception):
                observations.append(
                    Observation(
                        source="mcp",
                        tool_name=call.name,
                        arguments=call.arguments,
                        observed_at=datetime.now(UTC),
                        value={"error": type(result).__name__},
                        summary=f"{call.name} 采集失败，未获得可用基础设施证据。",
                        reliability="failed",
                    )
                )
            else:
                observations.append(result)
        updates: dict[str, object] = {
            "observations": observations,
            "completed_calls": [*state["completed_calls"], *calls],
            "planned_calls": [],
            "investigation_rounds": state["investigation_rounds"] + 1,
        }
        if len(updates["completed_calls"]) >= self._budget.max_tool_calls:
            updates["stop_reason"] = "已达到 Tool 调用次数预算"
        return updates

    @staticmethod
    def _after_authorize(state: _WorkflowState) -> Literal["execute", "finish"]:
        return "finish" if state["stop_reason"] is not None else "execute"

    def _after_execute(self, state: _WorkflowState) -> Literal["plan", "finish"]:
        return (
            "finish"
            if len(state["completed_calls"]) >= self._budget.max_tool_calls
            else "plan"
        )
