from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from hinataops.agent_core.models import (
    Hypothesis,
    IncidentRequest,
    InvestigationReport,
    Observation,
    ToolCall,
)
from hinataops.agent_core.policy import InvestigationBudget, InvestigationPolicyError


def _incident() -> IncidentRequest:
    return IncidentRequest(
        incident_id=uuid4(),
        query="Python 判题任务长时间没有结果",
        target_environment="aoi-local",
    )


def _observation() -> Observation:
    return Observation(
        evidence_id=uuid4(),
        source="redis",
        tool_name="aoi_judge_get_stream_summary",
        arguments={"language": "python"},
        observed_at=datetime.now(UTC),
        value={"consumer_group": {"lag": 12}},
        summary="Python Judge Consumer Group lag 为 12。",
        reliability="complete",
    )


def test_hypothesis_rejects_overlapping_supporting_and_contradicting_evidence() -> None:
    evidence_id = uuid4()

    with pytest.raises(ValidationError, match="不能同时"):
        Hypothesis(
            hypothesis_id=uuid4(),
            cause="Judge Worker 停止消费",
            confidence=0.7,
            supporting_evidence_ids=[evidence_id],
            contradicting_evidence_ids=[evidence_id],
        )


def test_report_requires_diagnosis_to_reference_existing_evidence() -> None:
    observation = _observation()
    hypothesis = Hypothesis(
        hypothesis_id=uuid4(),
        cause="Judge Worker 停止消费",
        confidence=0.8,
        supporting_evidence_ids=[observation.evidence_id],
    )

    report = InvestigationReport(
        incident_id=_incident().incident_id,
        status="diagnosed",
        observations=[observation],
        hypotheses=[hypothesis],
        primary_hypothesis_id=hypothesis.hypothesis_id,
        conclusion="Worker 无法消费 Redis Stream。",
    )

    assert report.primary_hypothesis_id == hypothesis.hypothesis_id


def test_report_rejects_hypothesis_evidence_that_was_not_collected() -> None:
    observation = _observation()
    hypothesis = Hypothesis(
        hypothesis_id=uuid4(),
        cause="Judge Worker 停止消费",
        confidence=0.8,
        supporting_evidence_ids=[uuid4()],
    )

    with pytest.raises(ValidationError, match="未收集的 Evidence ID"):
        InvestigationReport(
            incident_id=_incident().incident_id,
            status="diagnosed",
            observations=[observation],
            hypotheses=[hypothesis],
            primary_hypothesis_id=hypothesis.hypothesis_id,
            conclusion="Worker 无法消费 Redis Stream。",
        )


def test_budget_rejects_write_tools_duplicate_calls_and_exhausted_budget() -> None:
    budget = InvestigationBudget(
        readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"}),
        max_rounds=3,
        max_tool_calls=1,
    )
    call = ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "python"})

    budget.authorize(call, completed_calls=[], investigation_round=1)

    with pytest.raises(InvestigationPolicyError, match="只读 Tool 白名单"):
        budget.authorize(
            ToolCall(name="docker_restart_service", arguments={"action_id": "example"}),
            completed_calls=[],
            investigation_round=1,
        )

    with pytest.raises(InvestigationPolicyError, match="重复"):
        budget.authorize(call, completed_calls=[call], investigation_round=1)

    with pytest.raises(InvestigationPolicyError, match="调用次数预算"):
        budget.authorize(
            ToolCall(name="aoi_judge_get_stream_summary", arguments={"language": "sql"}),
            completed_calls=[call],
            investigation_round=1,
        )

    with pytest.raises(InvestigationPolicyError, match="轮次预算"):
        budget.authorize(call, completed_calls=[], investigation_round=4)
