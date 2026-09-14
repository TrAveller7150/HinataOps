import asyncio
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

from hinataops.ops_mcp.adapters import SshCommandError
from hinataops.ops_mcp.config import load_environment_config
from hinataops.ops_mcp.config import ActionSettings
from hinataops.ops_mcp.contracts import new_metadata
from hinataops.ops_mcp.plugins import ToolsetContext, discover_toolsets
from hinataops.ops_mcp.policy import ToolsetConfig
from hinataops.ops_mcp.server import create_server
from hinataops.ops_mcp.toolsets.aoi_learn_judge.pipeline_summary import (
    MysqlJudgePipelineSummary,
)
from hinataops.ops_mcp.toolsets.aoi_learn_judge.plugin import AoiLearnJudgePlugin
from hinataops.tooling.contracts import ToolResult


def test_server_exposes_only_enabled_domain_toolset() -> None:
    """验证领域 Tool 名称准确，且未提前开放通用任意查询入口。"""
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    server = create_server(lambda: config)

    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert tool_names == {
        "topology_get_service",
        "aoi_judge_get_container_runtime",
        "aoi_judge_get_stream_summary",
        "aoi_judge_get_pipeline_summary",
        "aoi_judge_get_runtime",
    }
    assert not any(name.startswith(("mysql_", "redis_", "prometheus_", "docker_")) for name in tool_names)


def test_server_registers_restart_only_when_actions_are_explicitly_enabled() -> None:
    """验证写 Tool 不会因环境配置遗漏而默认暴露。"""
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    enabled_config = config.model_copy(
        update={"actions": ActionSettings(enabled=True, allowed_services=["judge-python"])}
    )

    tool_names = {tool.name for tool in create_server(lambda: enabled_config)._tool_manager.list_tools()}

    assert "docker_restart_service" in tool_names


def test_aoi_learn_plugin_is_discovered_from_package_entry_point() -> None:
    """验证 Core 不直接导入领域 Toolset，仍能发现已安装的 AoiLearn Plugin。"""
    assert "aoi_learn_judge" in discover_toolsets()


def test_server_rejects_an_enabled_plugin_that_is_not_installed() -> None:
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    missing_plugin_config = config.model_copy(
        update={
            "toolsets": [ToolsetConfig(name="missing_toolset", enabled=True)],
            "toolset_settings": {},
        }
    )

    with pytest.raises(ValueError, match="not installed"):
        create_server(lambda: missing_plugin_config)


def test_server_does_not_require_a_disabled_plugin_to_be_installed() -> None:
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    disabled_plugin_config = config.model_copy(
        update={
            "toolsets": [ToolsetConfig(name="missing_toolset", enabled=False)],
            "toolset_settings": {},
        }
    )

    tool_names = {
        tool.name for tool in create_server(lambda: disabled_plugin_config)._tool_manager.list_tools()
    }
    assert tool_names == {"topology_get_service"}


def test_server_rejects_invalid_plugin_specific_configuration() -> None:
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    invalid_plugin_config = config.model_copy(
        update={"toolset_settings": {"aoi_learn_judge": {}}}
    )

    with pytest.raises(ValidationError):
        create_server(lambda: invalid_plugin_config)


def test_topology_tool_returns_v3_tool_result() -> None:
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    server = create_server(lambda: config)

    raw_result = asyncio.run(
        server._tool_manager.get_tool("topology_get_service").fn("server")
    )
    result = ToolResult.model_validate(raw_result)

    assert result.status == "success"
    assert result.environment == "aoi-local"
    assert result.source == "topology"
    assert result.data["container"] == "aoi-learn-server-1"


def test_aoi_observation_tool_returns_error_without_placeholder_data(monkeypatch) -> None:
    class FailingToolset:
        async def get_container_runtime(self):
            raise SshCommandError("ssh_command_timeout", "SSH command timed out")

    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    plugin = AoiLearnJudgePlugin()
    monkeypatch.setattr(plugin, "_toolset", lambda current: FailingToolset())
    server = FastMCP("fixture")
    plugin._register_observation_tools(
        server,
        ToolsetContext(lambda: config, lambda: None),
    )

    raw_result = asyncio.run(
        server._tool_manager.get_tool("aoi_judge_get_container_runtime").fn()
    )
    result = ToolResult.model_validate(raw_result)

    assert result.status == "error"
    assert result.data == {}
    assert result.error is not None
    assert result.error.kind == "ssh_command_timeout"
    assert result.error.retryable is True


def test_aoi_pipeline_tool_marks_empty_window_as_no_data(monkeypatch) -> None:
    class EmptyPipelineToolset:
        async def get_pipeline_summary(self, window_minutes: int):
            return MysqlJudgePipelineSummary(
                metadata=new_metadata("aoi-local", "mysql"),
                window_minutes=window_minutes,
                task_statuses=[],
                outbox_statuses=[],
            )

    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    plugin = AoiLearnJudgePlugin()
    monkeypatch.setattr(plugin, "_toolset", lambda current: EmptyPipelineToolset())
    server = FastMCP("fixture")
    plugin._register_observation_tools(
        server,
        ToolsetContext(lambda: config, lambda: None),
    )

    raw_result = asyncio.run(
        server._tool_manager.get_tool("aoi_judge_get_pipeline_summary").fn(15)
    )
    result = ToolResult.model_validate(raw_result)

    assert result.status == "no_data"
    assert result.data == {
        "window_minutes": 15,
        "task_statuses": [],
        "outbox_statuses": [],
    }
