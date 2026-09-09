from __future__ import annotations

import os
import tomllib
from pathlib import Path
from pydantic import BaseModel, Field

from hinataops.ops_mcp.policy import InfrastructureInstanceConfig, InstanceKind, ToolsetConfig


class ServiceConfig(BaseModel):
    """HinataOps 被允许观测的单个服务的静态拓扑信息。

    容器名来自受审核的配置，而非 MCP Tool 参数，避免 Agent 将拓扑查询扩展为
    对任意 Docker 容器的检查请求。
    """

    name: str
    # 逻辑服务名称与实际 Docker 容器名分离，避免调用方直接指定容器。
    container: str
    depends_on: list[str] = Field(default_factory=list)
    role: str


class EnvironmentDetails(BaseModel):
    """一个目标环境受限 SSH 传输所需的连接信息。

    引用的私钥文件仅保留在本机，并由被 Git 忽略的环境配置提供，绝不能写入
    可提交的配置模板。
    """

    name: str
    host: str
    user: str
    # 私钥仅保留在被忽略的本机环境文件中。
    identity_file: Path
    connect_timeout_seconds: int = Field(default=8, ge=1, le=60)
    command_timeout_seconds: int = Field(default=15, ge=1, le=60)


class ActionSettings(BaseModel):
    """一个环境中允许注册的写操作及其服务级最小权限范围。"""

    enabled: bool = False
    # 白名单使用逻辑服务名，而不是可由调用方替换的 Docker 容器名。
    allowed_services: list[str] = Field(default_factory=list)


class EnvironmentConfig(BaseModel):
    """一个目标环境已校验的拓扑与连接配置。"""

    environment: EnvironmentDetails
    services: list[ServiceConfig]
    instances: list[InfrastructureInstanceConfig]
    toolsets: list[ToolsetConfig]
    # Core 不解释该配置；仅由同名 Plugin 校验和消费。
    toolset_settings: dict[str, dict[str, object]] = Field(default_factory=dict)
    actions: ActionSettings = Field(default_factory=ActionSettings)

    def service(self, name: str) -> ServiceConfig:
        """按名称查找已配置服务；未找到时返回包含可选项的校验错误。"""
        for service in self.services:
            if service.name == name:
                return service
        available = ", ".join(item.name for item in self.services)
        raise ValueError(f"Unknown service '{name}'. Available services: {available}")

    def instance(self, instance_id: str, expected_kind: InstanceKind) -> InfrastructureInstanceConfig:
        """读取指定类型的受审核实例，阻止 Toolset 错接到不兼容基础设施。"""
        for instance in self.instances:
            if instance.id == instance_id:
                if instance.kind != expected_kind:
                    raise ValueError(
                        f"Instance '{instance_id}' is {instance.kind}, expected {expected_kind}"
                    )
                return instance
        raise ValueError(f"Infrastructure instance '{instance_id}' is not configured")

    def toolset_enabled(self, name: str) -> bool:
        """判断经配置审查的领域 Toolset 是否允许注册。"""
        return any(toolset.name == name and toolset.enabled for toolset in self.toolsets)

    def toolset_config(self, name: str) -> dict[str, object]:
        """返回指定 Plugin 的原始专属配置，由 Plugin 自行校验其领域模型。"""
        try:
            return self.toolset_settings[name]
        except KeyError as error:
            raise ValueError(f"Toolset '{name}' is missing its configuration") from error


def load_environment_config(path: Path | None = None) -> EnvironmentConfig:
    """加载显式指定或由 ``HINATAOPS_CONFIG`` 选定的本地环境配置。"""
    # 默认配置文件被刻意忽略，使同一份源码能保留脱敏模板，而每位开发者使用自己的私钥路径。
    configured_path = path or Path(
        os.environ.get("HINATAOPS_CONFIG", "config/environments/local.toml")
    )
    with configured_path.open("rb") as file:
        return EnvironmentConfig.model_validate(tomllib.load(file))
