"""Agent Core 访问 MCP Tool Catalog 与执行 Tool 的唯一传输边界。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from hinataops.agent_core.models import ToolCall


class ToolGatewayError(RuntimeError):
    """MCP 传输、结果格式或 Tool Catalog 违反 Agent Core 契约时抛出。"""


@dataclass(frozen=True)
class ToolDescriptor:
    """Agent 选择 Tool 时需要的公开名称、说明和输入 Schema。"""

    name: str
    description: str | None
    input_schema: dict[str, object]


@dataclass(frozen=True)
class GatewayToolResult:
    """Gateway 已解析的结构化 MCP Tool 返回，不在此层解释业务含义。"""

    value: dict[str, object]


class ToolGateway(Protocol):
    """Agent Core 使用的 MCP 客户端端口；实现不得泄露基础设施访问细节。"""

    async def list_tools(self) -> list[ToolDescriptor]: ...

    async def call_tool(self, call: ToolCall) -> GatewayToolResult: ...


class ToolCatalog:
    """一次调查可见的 MCP Tool 清单，阻止 Planner 调用未发布能力。"""

    def __init__(self, tools: Sequence[ToolDescriptor]) -> None:
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ToolGatewayError("MCP Tool Catalog 中存在重复名称")

    @classmethod
    async def load(cls, gateway: ToolGateway) -> "ToolCatalog":
        """从 MCP Server 读取当前发布的 Tool，而非由 Agent 硬编码名称。"""
        return cls(await gateway.list_tools())

    def require(self, name: str) -> ToolDescriptor:
        """读取已发布 Tool；未知名称必须在调用远端前失败。"""
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolGatewayError(f"MCP 未发布 Tool: '{name}'") from error

    @property
    def names(self) -> frozenset[str]:
        """返回不可变名称集合，供调查预算构造只读白名单。"""
        return frozenset(self._tools)


class StreamableHttpToolGateway:
    """通过 Streamable HTTP 与独立 Ops MCP Server 通信的生产 Gateway。"""

    def __init__(self, url: str) -> None:
        self._url = url

    async def list_tools(self) -> list[ToolDescriptor]:
        """建立短生命周期 MCP 会话并读取 Tool Catalog。"""
        async with self._session() as session:
            result = await session.list_tools()
        return [
            ToolDescriptor(
                name=tool.name,
                description=tool.description,
                input_schema=tool.inputSchema,
            )
            for tool in result.tools
        ]

    async def call_tool(self, call: ToolCall) -> GatewayToolResult:
        """调用一个已由上层策略授权的 Tool，并只接受结构化对象结果。"""
        async with self._session() as session:
            result = await session.call_tool(call.name, dict(call.arguments))
        if result.isError:
            raise ToolGatewayError(f"MCP Tool '{call.name}' 返回错误")
        return GatewayToolResult(value=self._structured_value(result.structuredContent, result.content))

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        """创建会自动初始化和关闭的 MCP 会话，避免 Core 管理底层 HTTP 流。"""
        async with streamable_http_client(self._url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    @staticmethod
    def _structured_value(
        structured_content: dict[str, object] | None,
        content: list[object],
    ) -> dict[str, object]:
        """优先读取 MCP structuredContent，兼容 FastMCP 的单段 JSON 文本返回。"""
        if structured_content is not None:
            return structured_content
        text_items = [item.text for item in content if isinstance(item, TextContent)]
        if len(text_items) != 1:
            raise ToolGatewayError("MCP Tool 未返回单个结构化对象或 JSON 文本")
        try:
            value = json.loads(text_items[0])
        except json.JSONDecodeError as error:
            raise ToolGatewayError("MCP Tool 文本结果不是 JSON") from error
        if not isinstance(value, dict):
            raise ToolGatewayError("MCP Tool 结果必须是 JSON 对象")
        return value
