"""领域 Toolset Plugin 的发现与通用装配契约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Protocol

from mcp.server.fastmcp import FastMCP

from hinataops.actions.repository import ActionRepository
from hinataops.ops_mcp.config import EnvironmentConfig


ConfigProvider = Callable[[], EnvironmentConfig]
ActionRepositoryProvider = Callable[[], ActionRepository]
ENTRY_POINT_GROUP = "hinataops.toolsets"


@dataclass(frozen=True)
class ToolsetContext:
    """Core 交给 Plugin 的受限运行时依赖。"""

    config_provider: ConfigProvider  # 读取已校验环境与各 Plugin 原始配置的提供者。
    action_repository_provider: ActionRepositoryProvider  # 获取共享审批账本的提供者。


class ToolsetPlugin(Protocol):
    """独立领域包接入 HinataOps Core 的最小 SPI。"""

    @property
    def name(self) -> str: ...

    def register(self, mcp: FastMCP, context: ToolsetContext) -> None: ...


def discover_toolsets() -> dict[str, ToolsetPlugin]:
    """从已安装包的 entry point 加载 Toolset，并拒绝歧义的重复名称。"""
    plugins: dict[str, ToolsetPlugin] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        plugin = entry_point.load()()
        if plugin.name != entry_point.name:
            raise ValueError(
                f"Toolset entry point '{entry_point.name}' declared plugin name '{plugin.name}'"
            )
        if plugin.name in plugins:
            raise ValueError(f"Duplicate Toolset plugin name: '{plugin.name}'")
        plugins[plugin.name] = plugin
    return plugins
