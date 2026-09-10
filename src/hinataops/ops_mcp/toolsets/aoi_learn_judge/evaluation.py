"""AoiLearn 判题领域的故障场景与 Ground Truth。"""

from hinataops.agent_core.evaluation import EvaluationScenario
from hinataops.agent_core.models import IncidentRequest


PYTHON_JUDGE_WORKER_UNAVAILABLE = EvaluationScenario(
    scenario_id="python_judge_worker_unavailable",
    incident=IncidentRequest(
        query="Python 判题任务长时间没有结果",
        target_environment="aoi-local",
    ),
    expected_primary_cause_code="judge_worker_unavailable",
    allowed_tool_names=frozenset(
        {
            "aoi_judge_get_container_runtime",
            "aoi_judge_get_runtime",
            "aoi_judge_get_stream_summary",
        }
    ),
    required_tool_names=frozenset(
        {"aoi_judge_get_container_runtime", "aoi_judge_get_runtime"}
    ),
)
