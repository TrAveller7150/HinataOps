"""调查结束后的证据评估与可审查诊断报告契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hinataops.agent_core.models import IncidentRequest, InvestigationReport, Observation, ToolCall


class DiagnosisError(RuntimeError):
    """诊断器无法基于本次证据生成有效报告时抛出。"""


@dataclass(frozen=True)
class DiagnosisContext:
    """诊断器可见的调查终态；不包含 Tool Provider、凭证或写操作能力。"""

    incident: IncidentRequest
    observations: list[Observation]
    completed_calls: list[ToolCall]
    stop_reason: str


class InvestigationDiagnostician(Protocol):
    """将一次已停止的调查转换为正式报告的端口。"""

    async def diagnose(self, context: DiagnosisContext) -> InvestigationReport: ...


class InconclusiveDiagnostician:
    """模型不可用时的确定性降级：只陈述调查事实，不猜测根因。"""

    async def diagnose(self, context: DiagnosisContext) -> InvestigationReport:
        """始终输出可审查的不确定结论，保留原始 Observation。"""
        return InvestigationReport(
            incident_id=context.incident.incident_id,
            status="inconclusive",
            observations=context.observations,
            hypotheses=[],
            conclusion=f"未形成可确认的根因。调查停止原因：{context.stop_reason}",
        )
