"""MCP Streamable HTTP Provider；不向 Agent Core 泄露 MCP 类型。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from hinataops.agent_core.models import ToolCall
from hinataops.agent_core.tool_provider import ToolProviderError
from hinataops.tooling.contracts import ToolDefinition, ToolError, ToolResult


class McpToolProvider:
    """通过 Streamable HTTP 发现和调用调查 Tool。"""

    def __init__(self, url: str) -> None:
        self._url = url

    async def list_tools(self) -> list[ToolDefinition]:
        """读取 MCP Catalog 并转换为通用 ToolDefinition。"""
        async with self._session() as session:
            result = await session.list_tools()
        try:
            return [
                ToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                )
                for tool in result.tools
            ]
        except Exception as error:
            raise ToolProviderError("MCP Tool Catalog 不符合 ToolDefinition 契约") from error

    async def invoke(self, call: ToolCall) -> ToolResult:
        """调用 MCP Tool，并将当前新旧结果封装转换为 ToolResult。"""
        async with self._session() as session:
            result = await session.call_tool(call.name, dict(call.arguments))
        if result.isError:
            raise ToolProviderError(
                f"MCP Tool '{call.name}' 返回协议错误",
                kind="mcp_protocol_error",
                retryable=False,
            )
        value = self._structured_value(result.structuredContent, result.content)
        return self._to_tool_result(value, call.name)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        """创建并关闭一次 MCP 会话。"""
        async with streamable_http_client(self._url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    @staticmethod
    def _structured_value(
        structured_content: dict[str, object] | None,
        content: list[object],
    ) -> dict[str, object]:
        """优先读取结构化内容，兼容单段 JSON 文本返回。"""
        if structured_content is not None:
            if not isinstance(structured_content, dict):
                raise ToolProviderError("MCP structuredContent 必须是 JSON 对象")
            return structured_content
        text_items = [item.text for item in content if isinstance(item, TextContent)]
        if len(text_items) != 1:
            raise ToolProviderError("MCP Tool 未返回单个结构化对象或 JSON 文本")
        try:
            value = json.loads(text_items[0])
        except json.JSONDecodeError as error:
            raise ToolProviderError("MCP Tool 文本结果不是 JSON") from error
        if not isinstance(value, dict):
            raise ToolProviderError("MCP Tool 结果必须是 JSON 对象")
        return value

    @classmethod
    def _to_tool_result(cls, value: dict[str, object], tool_name: str) -> ToolResult:
        """优先接受 V3 envelope，临时兼容 V2 metadata 返回。"""
        if "schema_version" in value:
            try:
                return ToolResult.model_validate(value)
            except Exception as error:
                raise ToolProviderError(
                    f"MCP Tool '{tool_name}' 返回不符合 ToolResult 契约"
                ) from error

        # V2 的 metadata 兼容仅位于 MCP 适配层，Core 和 EvidenceCollector 不再解析它。
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            raise ToolProviderError(f"MCP Tool '{tool_name}' 缺少 ToolResult envelope")
        try:
            environment = metadata["environment"]
            source = metadata["source"]
            if not isinstance(environment, str) or not isinstance(source, str):
                raise TypeError("environment/source 必须是字符串")
            observed_at = metadata["observed_at"]
            complete = metadata["complete"]
            if not isinstance(complete, bool):
                raise TypeError("complete 必须是布尔值")
            warnings = [str(item) for item in metadata.get("warnings", [])]
            raw_error = metadata.get("error")
            error = ToolError.model_validate(raw_error) if raw_error is not None else None
        except (KeyError, TypeError, ValueError) as cause:
            raise ToolProviderError(f"MCP Tool '{tool_name}' 的 V2 metadata 无效") from cause

        data = {key: item for key, item in value.items() if key != "metadata"}
        if complete and error is not None:
            raise ToolProviderError(f"MCP Tool '{tool_name}' 的 V2 metadata 状态矛盾")
        if complete:
            status = "success"
        elif error is not None:
            # V2 没有声明哪些字段是真实部分结果，当旧 Tool 同时返回错误和
            # 占位零值时必须保守视为失败，避免模型把占位值当成基础设施事实。
            status = "error"
            data = {}
        elif data and warnings:
            status = "partial"
        else:
            status = "error"
            data = {}
            error = error or ToolError(
                kind="incomplete_result",
                message="MCP Tool 未返回可用数据",
                retryable=False,
            )
        try:
            return ToolResult(
                status=status,
                environment=environment,
                source=source,
                observed_at=observed_at,
                data=data,
                warnings=warnings,
                error=error if status in {"error", "partial"} else None,
            )
        except Exception as cause:
            raise ToolProviderError(f"MCP Tool '{tool_name}' 的 V2 metadata 无法转换") from cause
