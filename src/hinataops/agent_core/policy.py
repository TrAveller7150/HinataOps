"""调查循环的确定性预算与只读 Tool 边界。"""

from __future__ import annotations

from dataclasses import dataclass

from hinataops.agent_core.models import ToolCall


class InvestigationPolicyError(ValueError):
    """Planner 请求越过调查预算或 Tool 权限边界时抛出。"""


@dataclass(frozen=True)
class InvestigationBudget:
    """在 LLM 之外强制执行的单次 Incident 调查预算。"""

    readonly_tool_names: frozenset[str]
    max_rounds: int = 3
    max_tool_calls: int = 4

    def authorize(
        self,
        call: ToolCall,
        *,
        completed_calls: list[ToolCall],
        investigation_round: int,
        retryable_fingerprints: frozenset[str] = frozenset(),
    ) -> None:
        """验证调用，不执行 Tool；调用方仅在成功后才能进入 MCP Gateway。"""
        if investigation_round > self.max_rounds:
            raise InvestigationPolicyError("已达到调查轮次预算")
        if call.name not in self.readonly_tool_names:
            raise InvestigationPolicyError("Tool 不在只读 Tool 白名单中")
        duplicate_count = sum(item.fingerprint == call.fingerprint for item in completed_calls)
        if duplicate_count:
            if call.fingerprint not in retryable_fingerprints:
                raise InvestigationPolicyError("不允许重复执行相同 Tool 和参数")
            if duplicate_count >= 2:
                raise InvestigationPolicyError("同一可重试 Tool 最多执行两次")
        if len(completed_calls) >= self.max_tool_calls:
            raise InvestigationPolicyError("已达到 Tool 调用次数预算")
