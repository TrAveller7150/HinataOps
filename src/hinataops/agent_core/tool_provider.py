"""Agent Core 使用的传输无关 Tool Provider 端口和 Catalog。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from hinataops.agent_core.models import ToolCall
from hinataops.tooling.contracts import ToolDefinition, ToolResult


class ToolProviderError(RuntimeError):
    """Provider 发现、协议或调用边界违反 Core 契约时抛出。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable


class ToolProvider(Protocol):
    """Core 调用 Tool 的最小端口；实现可以使用 MCP、HTTP 或 SDK。"""

    async def list_tools(self) -> list[ToolDefinition]: ...

    async def invoke(self, call: ToolCall) -> ToolResult: ...


class ToolCatalog:
    """一次调查可见的 Tool 清单，阻止调用未发布能力。"""

    def __init__(self, tools: Sequence[ToolDefinition]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ToolProviderError("Tool Catalog 中存在重复名称")

    @classmethod
    async def load(cls, provider: ToolProvider) -> "ToolCatalog":
        """从 Provider 读取当前发布的 Tool，而不是由调查 Core 硬编码。"""
        return cls(await provider.list_tools())

    def require(self, name: str) -> ToolDefinition:
        """读取已发布 Tool；未知名称必须在远端调用前失败。"""
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolProviderError(f"未发布 Tool: '{name}'") from error

    @property
    def names(self) -> frozenset[str]:
        """返回不可变名称集合，供调查预算构造只读白名单。"""
        return frozenset(self._tools)

    @property
    def tools(self) -> tuple[ToolDefinition, ...]:
        """按名称稳定排序的 Tool 描述。"""
        return tuple(self._tools[name] for name in sorted(self._tools))
