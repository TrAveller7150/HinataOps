import asyncio
import json

from hinataops.ops_mcp.adapters.prometheus import PrometheusReadonlyAdapter
from hinataops.ops_mcp.config import load_environment_config
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


def test_prometheus_adapter_bounds_each_fixed_curl_request() -> None:
    class RecordingRunner:
        """记录适配器下发的固定远端命令，不访问真实 SSH。"""

        def __init__(self) -> None:
            self.command = ""

        async def run(self, command: str, *, max_output_bytes: int, timeout_seconds: int) -> str:
            self.command = command
            return ""

    config = load_environment_config()
    runner = RecordingRunner()
    adapter = PrometheusReadonlyAdapter(runner, config.instance("aoi-prometheus", "prometheus"))

    asyncio.run(adapter.query_many_fixed(("up{job=\"judge\"}",)))

    assert "--connect-timeout 1" in runner.command
    assert "--max-time 1.5" in runner.command
