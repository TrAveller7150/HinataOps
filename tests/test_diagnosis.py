import asyncio
import json
from datetime import UTC, datetime

import pytest

from hinataops.agent_core.diagnosis import DiagnosisContext, DiagnosisError
from hinataops.agent_core.llm import (
    LlmInvestigationDiagnostician,
    ValidatedStructuredOutput,
)
from hinataops.agent_core.models import IncidentRequest, Observation, ToolCall
from hinataops.agent_core.planner import InvestigationPlanner, PlanningContext, PlanningDecision
from hinataops.agent_core.policy import InvestigationBudget
from hinataops.tooling.contracts import ToolDefinition, ToolResult
from hinataops.agent_core.workflow import InvestigationWorkflow


class FakeStructuredOutputClient:
    """以预设 JSON 模拟模型，并保留诊断 Prompt 与 Schema 供断言。"""

    def __init__(self, response: dict[str, object]) -> None:
        self._response = response
        self.system_prompt = ""
        self.user_prompt = ""
        self.schema_name = ""
        self.schema: dict[str, object] = {}

    async def create_validated_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
        validate,
    ) -> ValidatedStructuredOutput:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.schema_name = schema_name
        self.schema = schema
        return ValidatedStructuredOutput(value=validate(self._response), attempts=1)


class EvidenceAwareDiagnosisClient:
    """从真实工作流传入的 Prompt 读取 Evidence ID，模拟受约束模型引用证据。"""

    async def create_validated_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
        validate,
    ) -> ValidatedStructuredOutput:
        evidence_id = json.loads(user_prompt)["observations"][0]["evidence_id"]
        response = {
            "status": "diagnosed",
            "hypotheses": [
                {
                    "cause_code": "judge_worker_unavailable",
                    "cause": "Python Judge Worker 不可用",
                    "confidence": 0.9,
                    "supporting_evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": [],
                }
            ],
            "primary_hypothesis_index": 0,
            "conclusion": "Python Judge Worker 不可用导致任务无法消费。",
            "recommended_action": "请人工确认后重启 judge-python，并复查 Stream lag。",
        }
        return ValidatedStructuredOutput(value=validate(response), attempts=1)


class OneCheckPlanner(InvestigationPlanner):
    """先采集一次证据，第二轮结束，模拟完成的受预算调查。"""

    def __init__(self) -> None:
        self._round = 0

    async def decide(self, context: PlanningContext) -> PlanningDecision:
        self._round += 1
        if self._round == 1:
            return PlanningDecision(
                tool_calls=[ToolCall(name="aoi_judge_get_stream_summary")]
            )
        return PlanningDecision(finish_reason="已取得关键证据")


class OneEvidenceProvider:
    """模拟一个真实 Tool 的结构化返回。"""

    async def list_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="aoi_judge_get_stream_summary",
                description="Stream 摘要",
                input_schema={"type": "object"},
            )
        ]

    async def invoke(self, call: ToolCall) -> ToolResult:
        return ToolResult(
            status="success",
            environment="aoi-local",
            source="redis",
            observed_at=datetime(2026, 9, 10, tzinfo=UTC),
            data={"lag": 42},
        )


class BrokenDiagnostician:
    """模拟模型或报告校验失败，验证工作流降级而非丢失调查结果。"""

    async def diagnose(self, context: DiagnosisContext):
        raise DiagnosisError("模型输出不可用")


def _context() -> DiagnosisContext:
    incident = IncidentRequest(query="Python 判题任务没有结果", target_environment="aoi-local")
    observation = Observation(
        source="redis",
        tool_name="aoi_judge_get_stream_summary",
        arguments={"language": "python"},
        observed_at=datetime(2026, 9, 10, tzinfo=UTC),
        value={"lag": 42},
        summary="Python Stream 存在积压。",
        reliability="complete",
    )
    return DiagnosisContext(
        incident=incident,
        observations=[observation],
        completed_calls=[
            ToolCall(
                name="aoi_judge_get_stream_summary", arguments={"language": "python"}
            )
        ],
        stop_reason="关键证据齐全",
    )


def test_diagnostician_builds_report_with_only_collected_evidence() -> None:
    context = _context()
    evidence_id = str(context.observations[0].evidence_id)
    client = FakeStructuredOutputClient(
        {
            "status": "diagnosed",
            "hypotheses": [
                {
                    "cause_code": "judge_worker_unavailable",
                    "cause": "Python Worker 停止消费",
                    "confidence": 0.9,
                    "supporting_evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": ["容器运行状态"],
                }
            ],
            "primary_hypothesis_index": 0,
            "conclusion": "Python Worker 停止消费，导致队列积压。",
            "recommended_action": "人工确认后检查并重启 judge-python。",
        }
    )
    diagnostician = LlmInvestigationDiagnostician(client)

    report = asyncio.run(diagnostician.diagnose(context))

    assert report.status == "diagnosed"
    assert report.primary_hypothesis_id == report.hypotheses[0].hypothesis_id
    assert report.hypotheses[0].supporting_evidence_ids == [context.observations[0].evidence_id]
    assert client.schema_name == "investigation_report"
    assert client.schema["additionalProperties"] is False
    assert evidence_id in client.user_prompt
    assert "观测证据（`Observation`）" in client.system_prompt
    assert "简体中文" in client.system_prompt


def test_diagnostician_rejects_cause_code_outside_evaluation_contract() -> None:
    context = _context()
    evidence_id = str(context.observations[0].evidence_id)
    client = FakeStructuredOutputClient(
        {
            "status": "diagnosed",
            "hypotheses": [
                {
                    "cause_code": "redis_connectivity_failure",
                    "cause": "Redis 连接失败",
                    "confidence": 0.9,
                    "supporting_evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": [],
                }
            ],
            "primary_hypothesis_index": 0,
            "conclusion": "Redis 连接失败。",
            "recommended_action": None,
        }
    )

    with pytest.raises(DiagnosisError, match="诊断"):
        asyncio.run(
            LlmInvestigationDiagnostician(
                client,
                allowed_cause_codes=frozenset({"judge_worker_unavailable"}),
            ).diagnose(context)
        )


def test_diagnostician_rejects_hallucinated_evidence_id() -> None:
    client = FakeStructuredOutputClient(
        {
            "status": "diagnosed",
            "hypotheses": [
                {
                    "cause_code": "judge_worker_unavailable",
                    "cause": "虚构根因",
                    "confidence": 0.9,
                    "supporting_evidence_ids": ["00000000-0000-0000-0000-000000000001"],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": [],
                }
            ],
            "primary_hypothesis_index": 0,
            "conclusion": "没有真实证据支持。",
            "recommended_action": None,
        }
    )

    with pytest.raises(DiagnosisError, match="诊断"):
        asyncio.run(LlmInvestigationDiagnostician(client).diagnose(_context()))


def test_diagnostician_downgrades_low_confidence_primary_to_inconclusive() -> None:
    context = _context()
    evidence_id = str(context.observations[0].evidence_id)
    client = FakeStructuredOutputClient(
        {
            "status": "diagnosed",
            "hypotheses": [
                {
                    "cause_code": "judge_worker_unavailable",
                    "cause": "Worker 可能不可用",
                    "confidence": 0.45,
                    "supporting_evidence_ids": [evidence_id],
                    "contradicting_evidence_ids": [],
                    "missing_evidence": ["任务级日志"],
                }
            ],
            "primary_hypothesis_index": 0,
            "conclusion": "仍需补充任务级证据。",
            "recommended_action": "人工核查任务日志。",
        }
    )

    report = asyncio.run(LlmInvestigationDiagnostician(client).diagnose(context))

    assert report.status == "inconclusive"
    assert report.primary_hypothesis_id is None
    assert report.hypotheses[0].confidence == 0.45
    assert "未达到 70% 的确认阈值" in report.conclusion
    assert "不低于 70%" in client.system_prompt


def test_workflow_generates_a_traceable_report_after_evidence_collection() -> None:
    workflow = InvestigationWorkflow(
        OneEvidenceProvider(),
        OneCheckPlanner(),
        InvestigationBudget(readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})),
        LlmInvestigationDiagnostician(EvidenceAwareDiagnosisClient()),
    )

    result = asyncio.run(
        workflow.run(IncidentRequest(query="Python 判题无结果", target_environment="aoi-local"))
    )

    assert result.report.status == "diagnosed"
    assert result.report.primary_hypothesis_id == result.report.hypotheses[0].hypothesis_id
    assert result.report.hypotheses[0].supporting_evidence_ids == [
        result.observations[0].evidence_id
    ]
    assert result.report.recommended_action is not None


def test_workflow_falls_back_to_inconclusive_report_when_diagnosis_fails() -> None:
    workflow = InvestigationWorkflow(
        OneEvidenceProvider(),
        OneCheckPlanner(),
        InvestigationBudget(readonly_tool_names=frozenset({"aoi_judge_get_stream_summary"})),
        BrokenDiagnostician(),
    )

    result = asyncio.run(
        workflow.run(IncidentRequest(query="Python 判题无结果", target_environment="aoi-local"))
    )

    assert result.report.status == "inconclusive"
    assert result.report.observations == result.observations
    assert "已取得关键证据" in result.report.conclusion
