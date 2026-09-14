"""HinataOps 通用 MCP Server 的启动与 Plugin 装配入口。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from hinataops.actions.repository import ActionRepository
from hinataops.actions.runtime import action_repository
from hinataops.ops_mcp.config import EnvironmentConfig, load_environment_config
from hinataops.ops_mcp.plugins import ToolsetContext, discover_toolsets
from hinataops.tooling.contracts import ToolResult

ConfigProvider = Callable[[], EnvironmentConfig]
ActionRepositoryProvider = Callable[[], ActionRepository]


def _register_topology_tool(mcp: FastMCP, config_provider: ConfigProvider) -> None:
    """注册仅返回静态受审核拓扑的通用基础查询 Tool。"""

    @mcp.tool(name="topology_get_service")
    async def topology_get_service(service: str) -> dict:
        """返回一个已配置服务的拓扑事实。"""
        config = config_provider()
        target = config.service(service)
        return ToolResult(
            status="success",
            environment=config.environment.name,
            source="topology",
            observed_at=datetime.now(UTC),
            data={
                "service": target.name,
                "container": target.container,
                "role": target.role,
                "depends_on": target.depends_on,
            },
        ).model_dump(mode="json")


def create_server(
    config_provider: ConfigProvider = load_environment_config,
    repository_provider: ActionRepositoryProvider = action_repository,
) -> FastMCP:
    """按通用配置发现、校验并装配已启用的领域 Toolset Plugin。"""
    mcp = FastMCP("HinataOps Ops")
    _register_topology_tool(mcp, config_provider)

    config = config_provider()
    plugins = discover_toolsets()
    context = ToolsetContext(config_provider, repository_provider)
    enabled_names: set[str] = set()
    for spec in config.toolsets:
        if not spec.enabled:
            continue
        if spec.name in enabled_names:
            raise ValueError(f"Toolset '{spec.name}' is enabled more than once")
        enabled_names.add(spec.name)
        try:
            plugin = plugins[spec.name]
        except KeyError as error:
            raise ValueError(f"Enabled Toolset plugin '{spec.name}' is not installed") from error
        plugin.register(mcp, context)
    return mcp


# Server 是通用信任边界：领域 Tool 只能由已安装、已启用的 Plugin 注册。
mcp = create_server()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
