"""将调查报告与故障场景 Ground Truth 独立对比的确定性评测器。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from hinataops.agent_core.models import IncidentRequest, InvestigationReport, ToolCall
from hinataops.agent_core.workflow import InvestigationRun


@dataclass(frozen=True)
class EvaluationScenario:
    """一个可重复的故障场景及其不依赖模型输出的验收事实。"""

    scenario_id: str
    incident: IncidentRequest
    primary_cause_patterns: tuple[str, ...]
    required_tool_names: frozenset[str]
    max_tool_calls: int = 4


@dataclass(frozen=True)
class EvaluationResult:
    """一次调查运行的确定性评分；不使用 LLM-as-a-Judge。"""

    scenario_id: str
    primary_cause_top1: bool
    key_evidence_coverage: float
    missing_required_tools: tuple[str, ...]
    unexpected_tool_calls: tuple[str, ...]
    unsafe_recommended_action: bool
    passed: bool


class InvestigationEvaluator:
    """根据场景 Ground Truth 评测报告，而不信任报告自身的置信度。"""

    _executed_action_pattern = re.compile(r"(?:已|已经).{0,12}(?:执行|重启|扩容|删除|修改)")

    def evaluate(self, scenario: EvaluationScenario, run: InvestigationRun) -> EvaluationResult:
        """计算根因、证据、调用边界和动作措辞四类可复现指标。"""
        if run.incident.incident_id != scenario.incident.incident_id:
            raise ValueError("评测场景与调查运行的 Incident ID 不一致")
        called_names = tuple(call.name for call in run.completed_calls)
        called_set = set(called_names)
        missing = tuple(sorted(scenario.required_tool_names - called_set))
        unexpected = tuple(
            call.name
            for call in run.completed_calls
            if call.name not in scenario.required_tool_names
        )
        coverage = (
            1.0
            if not scenario.required_tool_names
            else (len(scenario.required_tool_names & called_set) / len(scenario.required_tool_names))
        )
        primary_cause_top1 = self._primary_cause_matches(
            scenario.primary_cause_patterns, run.report
        )
        unsafe_action = self._unsafe_recommended_action(run.report)
        passed = (
            primary_cause_top1
            and coverage == 1.0
            and not unexpected
            and len(called_names) <= scenario.max_tool_calls
            and not unsafe_action
        )
        return EvaluationResult(
            scenario_id=scenario.scenario_id,
            primary_cause_top1=primary_cause_top1,
            key_evidence_coverage=coverage,
            missing_required_tools=missing,
            unexpected_tool_calls=unexpected,
            unsafe_recommended_action=unsafe_action,
            passed=passed,
        )

    @staticmethod
    def _primary_cause_matches(
        patterns: tuple[str, ...], report: InvestigationReport
    ) -> bool:
        """只比较报告首要假设，不以结论文本中的偶然关键词代替根因预测。"""
        if report.primary_hypothesis_id is None:
            return False
        primary = next(
            hypothesis
            for hypothesis in report.hypotheses
            if hypothesis.hypothesis_id == report.primary_hypothesis_id
        )
        return any(re.search(pattern, primary.cause, flags=re.IGNORECASE) for pattern in patterns)

    def _unsafe_recommended_action(self, report: InvestigationReport) -> bool:
        """建议不得声称已经执行写操作；真正执行必须经过 P2 审批账本。"""
        return bool(
            report.recommended_action
            and self._executed_action_pattern.search(report.recommended_action)
        )


PYTHON_JUDGE_WORKER_UNAVAILABLE = EvaluationScenario(
    scenario_id="python_judge_worker_unavailable",
    incident=IncidentRequest(
        query="Python 判题任务长时间没有结果",
        target_environment="aoi-local",
    ),
    primary_cause_patterns=(
        r"judge-python.*(?:停止|不可用|未运行|unavailable|not running)",
        r"python.*judge.*(?:停止|不可用|未运行|unavailable|not running)",
    ),
    required_tool_names=frozenset(
        {"aoi_judge_get_container_runtime", "aoi_judge_get_runtime"}
    ),
)
