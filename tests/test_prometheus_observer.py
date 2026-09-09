import asyncio
import json
from urllib.parse import quote

from hinataops.config import JudgeStreamConfig
from hinataops.prometheus_observer import PrometheusJudgeRuntimeInspector


class FakePrometheusRunner:
    """按固定 PromQL 返回两个 Judge 实例的模拟指标。"""

    async def run(self, command: str) -> str:
        responses = []
        for query in PrometheusJudgeRuntimeInspector._QUERIES:
            assert quote(query, safe="") in command
            metric_name = "up" if query.startswith("up{") else query
            value = 1 if metric_name == "up" else 4
            responses.append(
                {
                    "status": "success",
                    "data": {
                        "result": [
                            {
                                "metric": {
                                    "__name__": metric_name,
                                    "instance": "judge-python:8001",
                                },
                                "value": [0, str(value)],
                            }
                        ]
                    },
                }
            )
        return "\n".join(json.dumps(response) for response in responses)


def test_prometheus_summary_groups_fixed_metrics_by_judge_instance() -> None:
    result = asyncio.run(
        PrometheusJudgeRuntimeInspector(
            FakePrometheusRunner(),
            "test",
            "server",
            [
                JudgeStreamConfig(
                    language="python",
                    stream_key="judge-tasks-python",
                    consumer_group="judge-python-workers",
                    judge_service="judge-python",
                    prometheus_instance="judge-python:8001",
                )
            ],
        ).summary()
    )

    runtime = result.runtimes[0]
    assert runtime.up is True
    assert runtime.pool_available == 4
    assert runtime.expand_paused is False
