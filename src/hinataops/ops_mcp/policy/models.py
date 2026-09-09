"""基础设施实例及其只读预算的配置模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

InstanceKind = Literal["docker", "mysql", "redis", "prometheus"]


class ReadonlyInstancePolicy(BaseModel):
    """约束单个基础设施实例的查询范围与返回规模。

    这些预算由服务端配置，而不是由 MCP Client 传入。这样即使未来增加通用查询
    Tool，也不能借由请求参数扩大单次调查的资源占用或上下文体积。
    """

    max_output_bytes: int = Field(default=262_144, ge=1_024, le=1_048_576)
    max_items: int = Field(default=100, ge=1, le=1_000)
    timeout_seconds: int = Field(default=15, ge=1, le=60)
    audit_enabled: bool = True


class InfrastructureInstanceConfig(BaseModel):
    """一个经配置审查的只读基础设施访问实例。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    kind: InstanceKind
    # MySQL、Redis 的固定访问入口；不得来自 MCP Tool 参数。
    container: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    access_container: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
    )
    policy: ReadonlyInstancePolicy = Field(default_factory=ReadonlyInstancePolicy)

    @model_validator(mode="after")
    def validate_access_path(self) -> "InfrastructureInstanceConfig":
        """确保每种实例都有固定访问入口，而非接受调用方指定容器。"""
        if self.kind in {"mysql", "redis"} and not self.container:
            raise ValueError(f"{self.kind} instance '{self.id}' requires container")
        if self.kind == "prometheus" and not self.access_container:
            raise ValueError(f"prometheus instance '{self.id}' requires access_container")
        return self


class ToolsetConfig(BaseModel):
    """一个领域 Toolset 的启用开关，避免未审核能力意外暴露为 MCP Tool。"""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    enabled: bool = True
