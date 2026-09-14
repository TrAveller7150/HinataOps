"""与传输方式无关的 Tool 定义和结果契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator


ToolResultStatus = Literal["success", "no_data", "partial", "error"]


class ToolDefinition(BaseModel):
    """模型可以选择的 Tool 描述，不包含 Provider 或基础设施连接细节。"""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,127}$")
    description: str | None = None
    input_schema: dict[str, object]

    @model_validator(mode="after")
    def require_object_input_schema(self) -> "ToolDefinition":
        """Tool 参数必须是 JSON 对象，避免把调用参数解释为任意 JSON 值。"""
        if self.input_schema.get("type") != "object":
            raise ValueError("input_schema.type 必须为 object")
        return self


class ToolError(BaseModel):
    """不泄露敏感细节的 Tool 失败信息。"""

    kind: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{1,127}$")
    message: str = Field(min_length=1, max_length=500)
    retryable: bool


class ToolResult(BaseModel):
    """所有调查 Tool 共享的结果语义，不依赖 MCP、HTTP 或本地调用方式。"""

    schema_version: Literal["1"] = "1"
    status: ToolResultStatus
    environment: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=64)
    observed_at: AwareDatetime
    data: dict[str, object] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    error: ToolError | None = None

    @model_validator(mode="after")
    def validate_status_semantics(self) -> "ToolResult":
        """保持无数据、部分结果和失败之间的确定性边界。"""
        if self.status in {"success", "no_data"} and self.error is not None:
            raise ValueError("success/no_data 结果不能携带 error")
        if self.status == "partial" and not self.data:
            raise ValueError("partial 结果必须包含可用 data")
        if self.status == "partial" and not self.warnings and self.error is None:
            raise ValueError("partial 结果必须说明 warnings 或 error")
        if self.status == "error":
            if self.error is None:
                raise ValueError("error 结果必须携带 error")
            if self.data:
                raise ValueError("error 结果不能携带业务 data")
        return self
