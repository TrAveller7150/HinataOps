"""调查图依赖的 Planner 契约；P3.3 使用脚本实现，P3.4 再接入 LLM。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from hinataops.agent_core.gateway import ToolCatalog
from hinataops.agent_core.models import IncidentRequest, Observation, ToolCall


class PlannerError(RuntimeError):
    """Planner 无法生成可执行的受限调查决策时抛出。"""


class PlanningDecision(BaseModel):
    """Planner 一轮的受限输出：选择有限 Tool，或明确结束调查。"""

    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    finish_reason: str | None = Field(default=None, min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_decision(self) -> "PlanningDecision":
        """空决策必须说明结束原因，避免图因模型空输出而无意义循环。"""
        if not self.tool_calls and self.finish_reason is None:
            raise ValueError("未选择 Tool 时必须提供 finish_reason")
        if self.tool_calls and self.finish_reason is not None:
            raise ValueError("选择 Tool 的调查轮次不能同时结束")
        return self


@dataclass(frozen=True)
class PlanningContext:
    """Planner 可见的最小调查上下文，不含 Gateway、密钥或写操作权限。"""

    incident: IncidentRequest
    catalog: ToolCatalog
    observations: list[Observation]
    completed_calls: list[ToolCall]
    investigation_round: int


class InvestigationPlanner(Protocol):
    """依据当前证据选择下一轮只读检查或结束调查的策略端口。"""

    async def decide(self, context: PlanningContext) -> PlanningDecision: ...
