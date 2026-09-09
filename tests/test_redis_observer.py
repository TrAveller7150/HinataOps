import asyncio

from hinataops.ops_mcp.config import JudgeStreamConfig
from hinataops.ops_mcp.toolsets.aoi_learn_judge.stream_summary import RedisStreamInspector


class FakeRedisAdapter:
    """为 Redis 观测测试提供固定的远端命令返回值。"""

    async def xinfo_groups(self, stream_key: str) -> str:
        assert stream_key == "judge-tasks-python"
        return '[{"name":"judge-python-workers","consumers":2,"last-delivered-id":"1-0","lag":3}]'

    async def xpending(self, stream_key: str, consumer_group: str) -> str:
        assert (stream_key, consumer_group) == ("judge-tasks-python", "judge-python-workers")
        return '[1,"1-0","1-0",[["worker-1","1"]]]'

    async def xlen(self, stream_key: str) -> str:
        assert stream_key == "judge-tasks-python"
        return "12\n"


def test_redis_stream_summary_uses_configured_stream_and_group() -> None:
    result = asyncio.run(
        RedisStreamInspector(
            "test",
            FakeRedisAdapter(),
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
        ).summary("python")
    )

    assert result.history_length == 12
    assert result.consumer_group is not None
    assert result.consumer_group.lag == 3
    assert result.consumer_group.pending == 1
    assert result.consumer_group.oldest_pending_id == "1-0"
    assert result.metadata.complete is True
