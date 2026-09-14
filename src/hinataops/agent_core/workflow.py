"""基于 LangGraph 的受预算调查循环。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from hinataops.agent_core.diagnosis import (
    DiagnosisContext,
    DiagnosisError,
    InconclusiveDiagnostician,
    InvestigationDiagnostician,
)
from hinataops.agent_core.evidence import EvidenceCollector
from hinataops.agent_core.events import InvestigationEvent, InvestigationEventListener
from hinataops.agent_core.gateway import ToolCatalog, ToolGateway, ToolGatewayError
from hinataops.agent_core.models import (
    IncidentRequest,
    InvestigationReport,
    Observation,
    PlanningTrace,
    RetryAttempt,
    ToolCall,
)
from hinataops.agent_core.planner import InvestigationPlanner, PlannerError, PlanningContext
from hinataops.agent_core.policy import InvestigationBudget, InvestigationPolicyError


class _WorkflowState(TypedDict):
    """LangGraph 运行时状态；仅保存可审查的领域对象与确定性控制字段。"""

    incident: IncidentRequest
    catalog: ToolCatalog | None
    observations: list[Observation]
    completed_calls: list[ToolCall]
    retry_attempts: list[RetryAttempt]
    planned_calls: list[ToolCall]
    planning_traces: list[PlanningTrace]
    investigation_rounds: int
    stop_reason: str | None
    report: InvestigationReport | None


class InvestigationRun:
    """一次图运行的只读结果与最终可审查诊断报告。"""

    def __init__(
        self,
        *,
        incident: IncidentRequest,
        observations: list[Observation],
        completed_calls: list[ToolCall],
        investigation_rounds: int,
        stop_reason: str,
        report: InvestigationReport,
        retry_attempts: list[RetryAttempt] | None = None,
        planning_traces: list[PlanningTrace] | None = None,
    ) -> None:
        self.incident = incident
        self.observations = observations
        self.completed_calls = completed_calls
        self.retry_attempts = retry_attempts or []
        self.planning_traces = planning_traces or []
        self.investigation_rounds = investigation_rounds
        self.stop_reason = stop_reason
        self.report = report


class InvestigationWorkflow:
    """协调 Planner、预算策略和 MCP EvidenceCollector 的只读调查图。"""

    def __init__(
        self,
        gateway: ToolGateway,
        planner: InvestigationPlanner,
        budget: InvestigationBudget,
        diagnostician: InvestigationDiagnostician | None = None,
        event_listener: InvestigationEventListener | None = None,
    ) -> None:
        self._gateway = gateway
        self._planner = planner
        self._budget = budget
        self._collector = EvidenceCollector(gateway)
        self._diagnostician = diagnostician or InconclusiveDiagnostician()
        self._event_listener = event_listener
        self._graph = self._build_graph()

    async def run(self, incident: IncidentRequest) -> InvestigationRun:
        """执行完整调查；所有退出路径均携带可解释的 stop_reason。"""
        result = await self._graph.ainvoke(
            {
                "incident": incident,
                "catalog": None,
                "observations": [],
                "completed_calls": [],
                "retry_attempts": [],
                "planned_calls": [],
                "planning_traces": [],
                "investigation_rounds": 0,
                "stop_reason": None,
                "report": None,
            }
        )
        return InvestigationRun(
            incident=result["incident"],
            observations=result["observations"],
            completed_calls=result["completed_calls"],
            retry_attempts=result["retry_attempts"],
            planning_traces=result["planning_traces"],
            investigation_rounds=result["investigation_rounds"],
            stop_reason=result["stop_reason"] or "调查图异常结束",
            report=result["report"],
        )

    def _build_graph(self):
        """构建显式循环，路由由预算和状态决定而非由 Planner 直接控制。"""
        graph = StateGraph(_WorkflowState)
        graph.add_node("load_catalog", self._load_catalog)
        graph.add_node("plan", self._plan)
        graph.add_node("authorize", self._authorize)
        graph.add_node("execute", self._execute)
        graph.add_node("diagnose", self._diagnose)
        graph.add_edge(START, "load_catalog")
        graph.add_edge("load_catalog", "plan")
        graph.add_edge("plan", "authorize")
        graph.add_conditional_edges(
            "authorize",
            self._after_authorize,
            {"execute": "execute", "finish": "diagnose"},
        )
        graph.add_conditional_edges(
            "execute",
            self._after_execute,
            {"plan": "plan", "finish": "diagnose"},
        )
        graph.add_edge("diagnose", END)
        return graph.compile()

    async def _load_catalog(self, state: _WorkflowState) -> dict[str, object]:
        """在图开始时加载 MCP 当前公开能力，避免硬编码领域 Tool 名称。"""
        try:
            catalog = await ToolCatalog.load(self._gateway)
        except Exception as error:
            return {"stop_reason": f"无法加载 MCP Tool Catalog: {type(error).__name__}"}
        self._emit(InvestigationEvent(kind="catalog_loaded", detail=f"发现 {len(catalog.tools)} 个 Tool"))
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
        investigation_round = state["investigation_rounds"] + 1
        try:
            decision = await self._planner.decide(
                PlanningContext(
                    incident=state["incident"],
                    catalog=catalog,
                    observations=state["observations"],
                    completed_calls=state["completed_calls"],
                    investigation_round=investigation_round,
                )
            )
        except PlannerError as error:
            trace = PlanningTrace(
                investigation_round=investigation_round,
                status="failed",
                summary=f"Planner 输出不可用：{error}",
            )
            self._emit(InvestigationEvent(kind="planning_failed", detail=trace.summary))
            return {
                "stop_reason": f"Planner 输出不可用: {error}",
                "planning_traces": [*state["planning_traces"], trace],
            }
        trace = PlanningTrace(
            investigation_round=investigation_round,
            status="planned" if decision.tool_calls else "finished",
            summary=decision.decision_summary,
            tool_calls=decision.tool_calls,
            finish_reason=decision.finish_reason,
        )
        traces = [*state["planning_traces"], trace]
        if not decision.tool_calls:
            self._emit(InvestigationEvent(kind="planning_finished", detail=trace.summary))
            return {"stop_reason": decision.finish_reason, "planning_traces": traces}
        self._emit(
            InvestigationEvent(
                kind="tools_planned", calls=tuple(decision.tool_calls), detail=trace.summary
            )
        )
        return {"planned_calls": decision.tool_calls, "planning_traces": traces}

    def _authorize(self, state: _WorkflowState) -> dict[str, object]:
        """逐项校验 Catalog、只读白名单、重复调用和预算，失败时不触碰 Gateway。"""
        catalog = state["catalog"]
        if state["stop_reason"] is not None or catalog is None:
            return {}
        staged_calls = list(state["completed_calls"])
        retry_attempts = list(state["retry_attempts"])
        try:
            for call in state["planned_calls"]:
                retry = self._retry_attempt(call, state["completed_calls"], state["observations"])
                catalog.require(call.name)
                self._budget.authorize(
                    call,
                    completed_calls=staged_calls,
                    investigation_round=state["investigation_rounds"] + 1,
                    retryable_fingerprints=self._retryable_fingerprints(
                        state["completed_calls"], state["observations"]
                    ),
                )
                staged_calls.append(call)
                if retry is not None:
                    retry_attempts.append(retry)
        except (InvestigationPolicyError, ToolGatewayError) as error:
            return {"stop_reason": str(error), "planned_calls": []}
        return {"retry_attempts": retry_attempts}

    async def _execute(self, state: _WorkflowState) -> dict[str, object]:
        """并发采集已经授权的只读 Tool，并把传输失败降级为失败证据。"""
        calls = state["planned_calls"]
        self._emit(InvestigationEvent(kind="tools_started", calls=tuple(calls)))
        retry_fingerprints = {item.call.fingerprint for item in state["retry_attempts"]}
        if any(call.fingerprint in retry_fingerprints for call in calls):
            await asyncio.sleep(self._RETRY_BACKOFF_SECONDS)
        results = await asyncio.gather(
            *(self._collector.collect(call) for call in calls), return_exceptions=True
        )
        observations = list(state["observations"])
        for call, result in zip(calls, results, strict=True):
            if isinstance(result, Exception):
                observation = Observation(
                    source="mcp",
                    tool_name=call.name,
                    arguments=call.arguments,
                    observed_at=datetime.now(UTC),
                    value={"error": type(result).__name__},
                    summary=f"{call.name} 采集失败，未获得可用基础设施证据。",
                    reliability="failed",
                )
                observations.append(observation)
                self._emit(InvestigationEvent(kind="tool_completed", observation=observation))
            else:
                observations.append(result)
                self._emit(InvestigationEvent(kind="tool_completed", observation=result))
        updates: dict[str, object] = {
            "observations": observations,
            "completed_calls": [*state["completed_calls"], *calls],
            "planned_calls": [],
            "investigation_rounds": state["investigation_rounds"] + 1,
        }
        if len(updates["completed_calls"]) >= self._budget.max_tool_calls:
            updates["stop_reason"] = "已达到 Tool 调用次数预算"
        return updates

    _RETRY_BACKOFF_SECONDS = 1.0

    @classmethod
    def _retry_attempt(
        cls,
        call: ToolCall,
        completed_calls: list[ToolCall],
        observations: list[Observation],
    ) -> RetryAttempt | None:
        """只有最近同参调用的 MCP 错误明确标记可重试时，才允许第二次采证。"""
        for previous_call, observation in zip(
            reversed(completed_calls), reversed(observations), strict=True
        ):
            if previous_call.fingerprint != call.fingerprint:
                continue
            reason = cls._retryable_reason(observation)
            if reason is None:
                return None
            return RetryAttempt(
                call=call,
                attempt=2,
                reason=reason,
                backoff_seconds=cls._RETRY_BACKOFF_SECONDS,
            )
        return None

    @staticmethod
    def _retryable_fingerprints(
        completed_calls: list[ToolCall], observations: list[Observation]
    ) -> frozenset[str]:
        """仅把带 MCP `retryable=true` 错误的最近观测授权为候选重试。"""
        return frozenset(
            call.fingerprint
            for call, observation in zip(completed_calls, observations, strict=True)
            if InvestigationWorkflow._retryable_reason(observation) is not None
        )

    @staticmethod
    def _retryable_reason(observation: Observation) -> str | None:
        """读取统一 metadata 的受控重试标记，不猜测普通失败是否适合重试。"""
        metadata = observation.value.get("metadata")
        if not isinstance(metadata, dict):
            return None
        error = metadata.get("error")
        if not isinstance(error, dict) or error.get("retryable") is not True:
            return None
        kind = error.get("kind")
        return str(kind) if isinstance(kind, str) and kind else "retryable_error"

    async def _diagnose(self, state: _WorkflowState) -> dict[str, object]:
        """在所有停止路径上构建报告；模型失败时返回确定性不确定结论。"""
        stop_reason = state["stop_reason"] or "调查图异常结束"
        context = DiagnosisContext(
            incident=state["incident"],
            observations=state["observations"],
            completed_calls=state["completed_calls"],
            stop_reason=stop_reason,
        )
        self._emit(InvestigationEvent(kind="diagnosis_started"))
        try:
            report = await self._diagnostician.diagnose(context)
        except DiagnosisError:
            report = await InconclusiveDiagnostician().diagnose(context)
        self._emit(InvestigationEvent(kind="diagnosis_completed"))
        return {"report": report}

    def _emit(self, event: InvestigationEvent) -> None:
        """隔离展示层回调异常，确保终端渲染不会影响调查与安全策略。"""
        if self._event_listener is None:
            return
        try:
            self._event_listener(event)
        except Exception:
            return

    @staticmethod
    def _after_authorize(state: _WorkflowState) -> Literal["execute", "finish"]:
        return "finish" if state["stop_reason"] is not None else "execute"

    def _after_execute(self, state: _WorkflowState) -> Literal["plan", "finish"]:
        return (
            "finish"
            if len(state["completed_calls"]) >= self._budget.max_tool_calls
            else "plan"
        )
