from datetime import UTC, datetime

from hinataops.agent_core.models import Observation
from hinataops.ops_mcp.toolsets.aoi_learn_judge.presentation import (
    AoiLearnJudgeEvidencePresenter,
)


def _observation(tool_name: str, value: dict[str, object]) -> Observation:
    """构造已完成观测，专门验证终端展示不依赖真实 MCP 或 LLM。"""
    return Observation(
        source="prometheus",
        tool_name=tool_name,
        arguments={},
        observed_at=datetime(2026, 9, 14, tzinfo=UTC),
        value=value,
        summary="通用摘要不应成为人工 CLI 的唯一信息。",
        reliability="complete",
    )


def test_presenter_renders_runtime_metrics_instead_of_generic_complete_text() -> None:
    lines = AoiLearnJudgeEvidencePresenter().render(
        _observation(
            "aoi_judge_get_runtime",
            {
                "runtimes": [
                    {
                        "language": "python",
                        "instance": "judge-python:8001",
                        "up": True,
                        "pool_active": 4.0,
                        "pool_available": 3.0,
                        "pool_creating": 1.0,
                        "host_cpu_percent": 12.5,
                        "host_memory_percent": 56.08,
                    }
                ]
            },
        )
    )

    assert lines == (
        "python Worker（judge-python:8001）：up=正常，空闲沙箱=3，总沙箱=4，创建中=1，CPU=12.5%，内存=56.08%",
    )


def test_presenter_renders_stream_lag_and_pending() -> None:
    lines = AoiLearnJudgeEvidencePresenter().render(
        _observation(
            "aoi_judge_get_stream_summary",
            {
                "language": "python",
                "stream_key": "judge-tasks-python",
                "history_length": 26,
                "consumer_group": {"consumers": 4, "lag": 0, "pending": 2},
            },
        )
    )

    assert lines == ("python 队列 judge-tasks-python：消费者=4，lag=0，pending=2，历史消息=26",)
