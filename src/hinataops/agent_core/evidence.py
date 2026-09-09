"""将 MCP Tool 的结构化返回转换为 Agent 可引用的调查证据。"""

from __future__ import annotations

from datetime import datetime

from hinataops.agent_core.gateway import ToolGateway
from hinataops.agent_core.models import Observation, ToolCall


class EvidenceCollector:
    """保留原始 Tool 返回并提取统一 metadata，不生成根因结论。"""

    def __init__(self, gateway: ToolGateway) -> None:
        self._gateway = gateway

    async def collect(self, call: ToolCall) -> Observation:
        """执行已授权调用，并将 MCP metadata 映射为稳定 Evidence 字段。"""
        result = await self._gateway.call_tool(call)
        metadata = self._metadata(result.value)
        warnings = metadata.get("warnings", [])
        reliability = "complete" if metadata.get("complete") else "partial"
        summary = f"{call.name} 返回来自 {metadata['source']} 的{reliability}证据。"
        if warnings:
            summary += f" 警告：{'；'.join(str(item) for item in warnings)}。"
        return Observation(
            source=str(metadata["source"]),
            tool_name=call.name,
            arguments=call.arguments,
            observed_at=datetime.fromisoformat(str(metadata["observed_at"])),
            value=result.value,
            summary=summary,
            reliability=reliability,
        )

    @staticmethod
    def _metadata(value: dict[str, object]) -> dict[str, object]:
        """拒绝缺少统一 metadata 的结果，防止无来源事实进入调查状态。"""
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("MCP Tool 返回缺少 metadata 对象")
        required = {"source", "observed_at", "complete"}
        if missing := required - set(metadata):
            raise ValueError(f"MCP Tool metadata 缺少字段: {sorted(missing)}")
        return metadata
