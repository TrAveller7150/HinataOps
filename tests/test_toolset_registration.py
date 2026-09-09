from pathlib import Path

import pytest
from pydantic import ValidationError

from hinataops.ops_mcp.config import load_environment_config
from hinataops.ops_mcp.config import ActionSettings
from hinataops.ops_mcp.plugins import discover_toolsets
from hinataops.ops_mcp.policy import ToolsetConfig
from hinataops.ops_mcp.server import create_server


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
