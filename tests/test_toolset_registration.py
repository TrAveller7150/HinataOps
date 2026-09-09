from pathlib import Path

from hinataops.ops_mcp.config import load_environment_config
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
