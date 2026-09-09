import asyncio
import json
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import JudgeStreamConfig
from hinataops.ops_mcp.toolsets.aoi_learn_judge.runtime_summary import PrometheusJudgeRuntimeInspector


class FakePrometheusAdapter:
    """按固定 PromQL 返回两个 Judge 实例的模拟指标。"""

    async def query_many_fixed(self, queries: tuple[str, ...]) -> str:
        responses = []
        for query in queries:
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
            "test",
            FakePrometheusAdapter(),
            [
                JudgeStreamConfig(
                    language="python",
                    stream_key="judge-tasks-python",
                    consumer_group="judge-python-workers",
                    judge_service="judge-python",
                    redis_instance_id="aoi-redis",
                    prometheus_target="judge-python:8001",
                )
            ],
        ).summary()
    )

    runtime = result.runtimes[0]
    assert runtime.up is True
    assert runtime.pool_available == 4
    assert runtime.expand_paused is False
