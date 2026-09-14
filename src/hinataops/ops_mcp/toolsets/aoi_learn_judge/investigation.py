"""AoiLearn 判题领域面向人工调查的事故画像，不包含评测预期答案。"""

from __future__ import annotations

from dataclasses import dataclass

from hinataops.agent_core.models import IncidentRequest


@dataclass(frozen=True)
class InvestigationProfile:
    """一个人工可选事故入口；每次运行创建新 Incident ID 以隔离审计记录。"""

    profile_id: str
    query: str
    target_environment: str
    readonly_tool_names: frozenset[str]
    max_tool_calls: int
    max_rounds: int

    def new_incident(self) -> IncidentRequest:
        """创建本次调查专属的 Incident，避免复用评测样例中的固定 ID。"""
        return IncidentRequest(query=self.query, target_environment=self.target_environment)


PYTHON_JUDGE_TASK_NO_RESULT = InvestigationProfile(
    profile_id="python_judge_task_no_result",
    query="Python 判题任务长时间没有结果",
    target_environment="aoi-local",
    readonly_tool_names=frozenset(
        {
            "aoi_judge_get_container_runtime",
            "aoi_judge_get_runtime",
            "aoi_judge_get_stream_summary",
            "aoi_judge_get_pipeline_summary",
        }
    ),
    # 最多四批采证；采证完成后，工作流允许一次不执行 Tool 的收尾决策。
    max_tool_calls=7,
    max_rounds=4,
)
