"""将 Tool Provider 的结构化返回转换为 Agent 可引用的调查证据。"""

from __future__ import annotations

from hinataops.agent_core.tool_provider import ToolProvider
from hinataops.agent_core.models import Observation, ToolCall


class EvidenceCollector:
    """保留结构化 Tool 结果并提取统一证据，不生成根因结论。"""

    def __init__(self, provider: ToolProvider) -> None:
        self._provider = provider

    async def collect(self, call: ToolCall) -> Observation:
        """执行已授权调用，并将 Provider 结果映射为稳定 Observation。"""
        result = await self._provider.invoke(call)
        reliability = {
            "success": "complete",
            "no_data": "complete",
            "partial": "partial",
            "error": "failed",
        }[result.status]
        status_label = {
            "success": "完整",
            "no_data": "无匹配数据",
            "partial": "部分",
            "error": "失败",
        }[result.status]
        summary = f"{call.name} 返回来自 {result.source} 的{status_label}证据。"
        if result.warnings:
            summary += f" 警告：{'；'.join(result.warnings)}。"
        if result.error is not None:
            summary += f" 错误：{result.error.message}。"
        return Observation(
            source=result.source,
            tool_name=call.name,
            arguments=call.arguments,
            observed_at=result.observed_at,
            value=result.data,
            summary=summary,
            reliability=reliability,
            result_status=result.status,
            error=result.error,
        )
