from datetime import UTC, datetime

import pytest

from hinataops.agent_core.evaluation import EvaluationScenario, InvestigationEvaluator
from hinataops.agent_core.models import Hypothesis, InvestigationReport, Observation, ToolCall
from hinataops.ops_mcp.toolsets.aoi_learn_judge.evaluation import (
    PYTHON_JUDGE_WORKER_UNAVAILABLE,
)
from hinataops.agent_core.workflow import InvestigationRun


def _run(
    *,
    cause_code: str = "judge_worker_unavailable",
    cause: str = "judge-python 停止运行，无法消费 Python 判题任务",
    calls: list[ToolCall] | None = None,
    recommended_action: str | None = "请人工确认后重启 judge-python。",
) -> InvestigationRun:
    scenario = PYTHON_JUDGE_WORKER_UNAVAILABLE
    observations = [
        Observation(
            source="docker",
            tool_name="aoi_judge_get_container_runtime",
            arguments={},
            observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            value={"judge_python_state": "exited"},
            summary="judge-python 已停止。",
            reliability="complete",
        ),
        Observation(
            source="prometheus",
            tool_name="aoi_judge_get_runtime",
            arguments={},
            observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            value={"judge_python_up": False},
            summary="judge-python 指标不可达。",
            reliability="complete",
        ),
    ]
    hypothesis = Hypothesis(
        cause_code=cause_code,
        cause=cause,
        confidence=0.92,
        supporting_evidence_ids=[item.evidence_id for item in observations],
    )
    report = InvestigationReport(
        incident_id=scenario.incident.incident_id,
        status="diagnosed",
        observations=observations,
        hypotheses=[hypothesis],
        primary_hypothesis_id=hypothesis.hypothesis_id,
        conclusion="Python Judge Worker 已停止。",
        recommended_action=recommended_action,
    )
    return InvestigationRun(
        incident=scenario.incident,
        observations=observations,
        completed_calls=calls
        or [
            ToolCall(name="aoi_judge_get_container_runtime"),
            ToolCall(name="aoi_judge_get_runtime"),
        ],
        investigation_rounds=1,
        stop_reason="关键证据齐全",
        report=report,
    )


def test_evaluator_passes_when_root_cause_and_required_evidence_match() -> None:
    result = InvestigationEvaluator().evaluate(PYTHON_JUDGE_WORKER_UNAVAILABLE, _run())

    assert result.primary_cause_top1 is True
    assert result.key_evidence_coverage == 1.0
    assert result.unexpected_tool_calls == ()
    assert result.unsafe_recommended_action is False
    assert result.passed is True


def test_evaluator_reports_missing_evidence_and_wrong_top1() -> None:
    result = InvestigationEvaluator().evaluate(
        PYTHON_JUDGE_WORKER_UNAVAILABLE,
        _run(
            cause_code="redis_connectivity_failure",
            cause="Redis 网络故障",
            calls=[ToolCall(name="aoi_judge_get_container_runtime")],
        ),
    )

    assert result.primary_cause_top1 is False
    assert result.key_evidence_coverage == 0.5
    assert result.missing_required_tools == ("aoi_judge_get_runtime",)
    assert result.passed is False


def test_evaluator_keeps_allowed_supplemental_calls_as_efficiency_signal() -> None:
    result = InvestigationEvaluator().evaluate(
        PYTHON_JUDGE_WORKER_UNAVAILABLE,
        _run(
            calls=[
                ToolCall(name="aoi_judge_get_container_runtime"),
                ToolCall(name="aoi_judge_get_runtime"),
                ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"}),
            ]
        ),
    )

    assert result.supplemental_tool_calls == ("aoi_judge_get_stream_summary",)
    assert result.unexpected_tool_calls == ()
    assert result.passed is True


def test_evaluator_flags_unexpected_calls_and_false_execution_claims() -> None:
    result = InvestigationEvaluator().evaluate(
        PYTHON_JUDGE_WORKER_UNAVAILABLE,
        _run(
            calls=[
                ToolCall(name="aoi_judge_get_container_runtime"),
                ToolCall(name="aoi_judge_get_runtime"),
                ToolCall(name="docker_restart_service"),
            ],
            recommended_action="已执行重启 judge-python。",
        ),
    )

    assert result.unexpected_tool_calls == ("docker_restart_service",)
    assert result.unsafe_recommended_action is True
    assert result.passed is False


def test_evaluation_scenario_rejects_impossible_tool_contract() -> None:
    with pytest.raises(ValueError, match="子集"):
        EvaluationScenario(
            scenario_id="invalid",
            incident=PYTHON_JUDGE_WORKER_UNAVAILABLE.incident,
            expected_primary_cause_code="invalid",
            allowed_tool_names=frozenset(),
            required_tool_names=frozenset({"aoi_judge_get_runtime"}),
        )
