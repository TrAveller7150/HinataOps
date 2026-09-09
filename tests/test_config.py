from pathlib import Path

import pytest

from hinataops.ops_mcp.config import load_environment_config


def test_load_example_environment() -> None:
    config = load_environment_config(
        Path("config/environments/aoi-local.example.toml")
    )

    assert config.environment.name == "aoi-local"
    assert config.service("judge-python").depends_on == ["redis"]
    assert config.judge_stream("python").consumer_group == "judge-python-workers"
    assert config.instance("aoi-redis", "redis").container == "aoi-learn-redis-1"
    assert config.toolset_enabled("aoi_learn_judge") is True


def test_unknown_service_explains_available_names() -> None:
    config = load_environment_config(
        Path("config/environments/aoi-local.example.toml")
    )

    with pytest.raises(ValueError, match="judge-python"):
        config.service("missing")
