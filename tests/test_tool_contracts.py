from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hinataops.tooling.contracts import ToolDefinition, ToolError, ToolResult


OBSERVED_AT = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


def _result(**overrides: object) -> ToolResult:
    values: dict[str, object] = {
        "status": "success",
        "environment": "aoi-local",
        "source": "redis",
        "observed_at": OBSERVED_AT,
        "data": {"lag": 0},
    }
    values.update(overrides)
    return ToolResult.model_validate(values)


def test_tool_definition_requires_object_input_schema() -> None:
    definition = ToolDefinition(
        name="redis_stream_summary",
        description="读取受限 Redis Stream 摘要。",
        input_schema={
            "type": "object",
            "properties": {"language": {"enum": ["python", "sql"]}},
        },
    )

    assert definition.name == "redis_stream_summary"
    assert definition.input_schema["type"] == "object"

    with pytest.raises(ValidationError, match="input_schema"):
        ToolDefinition(
            name="redis_stream_summary",
            description=None,
            input_schema={"type": "string"},
        )


@pytest.mark.parametrize("name", ["Redis Stream", "docker.runtime", "_private"])
def test_tool_definition_rejects_unstable_names(name: str) -> None:
    with pytest.raises(ValidationError, match="name"):
        ToolDefinition(name=name, description=None, input_schema={"type": "object"})


def test_success_result_contains_complete_data_without_error() -> None:
    result = _result()

    assert result.schema_version == "1"
    assert result.status == "success"
    assert result.data == {"lag": 0}
    assert result.error is None


def test_no_data_is_a_successful_query_outcome() -> None:
    result = _result(
        status="no_data",
        source="mysql",
        data={"window_minutes": 15, "tasks": []},
    )

    assert result.status == "no_data"
    assert result.data["tasks"] == []
    assert result.error is None


@pytest.mark.parametrize("status", ["success", "no_data"])
def test_successful_outcomes_reject_error(status: str) -> None:
    with pytest.raises(ValidationError, match="error"):
        _result(
            status=status,
            error=ToolError(
                kind="ssh_command_timeout",
                message="远端查询超时",
                retryable=True,
            ),
        )


def test_partial_result_requires_usable_data_and_degradation_detail() -> None:
    result = _result(
        status="partial",
        source="prometheus",
        data={"worker_up": True},
        warnings=["沙箱池容量指标缺失"],
    )

    assert result.status == "partial"
    assert result.warnings == ["沙箱池容量指标缺失"]

    with pytest.raises(ValidationError, match="partial"):
        _result(status="partial", data={}, warnings=[])


def test_error_result_requires_error_and_rejects_business_data() -> None:
    result = _result(
        status="error",
        source="docker",
        data={},
        error=ToolError(
            kind="ssh_command_timeout",
            message="Docker 状态查询超时",
            retryable=True,
        ),
    )

    assert result.error is not None
    assert result.error.retryable is True

    with pytest.raises(ValidationError, match="error"):
        _result(status="error", data={}, error=None)
    with pytest.raises(ValidationError, match="data"):
        _result(
            status="error",
            data={"state": "unknown"},
            error=ToolError(
                kind="target_unavailable",
                message="目标不可用",
                retryable=False,
            ),
        )


def test_source_is_extensible_beyond_current_aoi_integrations() -> None:
    result = _result(source="loki")

    assert result.source == "loki"


def test_result_requires_timezone_aware_observation_time() -> None:
    with pytest.raises(ValidationError, match="observed_at"):
        _result(observed_at=datetime(2026, 9, 14, 8, 0))


def test_result_rejects_unknown_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        _result(schema_version="2")
