"""组装只读调查工作流的应用服务，不包含评测 Ground Truth。"""

from __future__ import annotations

import json

from hinataops.agent_core.events import InvestigationEventListener
from hinataops.agent_core.llm import (
    LlmInvestigationDiagnostician,
    LlmInvestigationPlanner,
    StructuredOutputClient,
)
from hinataops.agent_core.models import IncidentRequest
from hinataops.integrations.mcp_provider import McpToolProvider
from hinataops.agent_core.policy import InvestigationBudget
from hinataops.agent_core.workflow import InvestigationRun, InvestigationWorkflow


async def run_readonly_investigation(
    *,
    mcp_url: str,
    client: StructuredOutputClient,
    incident: IncidentRequest,
    readonly_tool_names: frozenset[str],
    max_tool_calls: int,
    max_rounds: int = 3,
    event_listener: InvestigationEventListener | None = None,
    allowed_cause_codes: frozenset[str] = frozenset(),
) -> InvestigationRun:
    """运行一次受预算的只读调查；预期根因只可由评测调用方额外传入。"""
    workflow = InvestigationWorkflow(
        McpToolProvider(mcp_url),
        LlmInvestigationPlanner(client, readonly_tool_names=readonly_tool_names),
        InvestigationBudget(
            readonly_tool_names=readonly_tool_names,
            max_tool_calls=max_tool_calls,
            max_rounds=max_rounds,
        ),
        LlmInvestigationDiagnostician(client, allowed_cause_codes=allowed_cause_codes),
        event_listener=event_listener,
    )
    return await workflow.run(incident)


def render_investigation_archive(*, model: str, profile_id: str, run: InvestigationRun) -> str:
    """生成不含评测结果的 UTF-8 审计记录，供人工调查 CLI 归档。"""
    payload = {
        "model": model,
        "profile_id": profile_id,
        "incident": run.incident.model_dump(mode="json"),
        "stop_reason": run.stop_reason,
        "completed_tool_calls": [call.model_dump(mode="json") for call in run.completed_calls],
        "retry_attempts": [item.model_dump(mode="json") for item in run.retry_attempts],
        "planning_trace": [item.model_dump(mode="json") for item in run.planning_traces],
        "diagnosis_error": run.diagnosis_error,
        "report": run.report.model_dump(mode="json"),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
