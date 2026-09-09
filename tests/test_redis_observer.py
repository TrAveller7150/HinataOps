import asyncio

from hinataops.config import JudgeStreamConfig
from hinataops.redis_observer import RedisStreamInspector


class FakeRedisRunner:
    """为 Redis 观测测试提供固定的远端命令返回值。"""

    async def run(self, command: str) -> str:
        if "XINFO GROUPS" in command:
            return '[{"name":"judge-python-workers","consumers":2,"last-delivered-id":"1-0","lag":3}]'
        if "XPENDING" in command:
            return '[1,"1-0","1-0",[["worker-1","1"]]]'
        if "XLEN" in command:
            return "12\n"
        raise AssertionError(f"未预期的 Redis 命令: {command}")


def test_redis_stream_summary_uses_configured_stream_and_group() -> None:
    result = asyncio.run(
        RedisStreamInspector(
            FakeRedisRunner(),
            "test",
            "redis",
            [
                JudgeStreamConfig(
                    language="python",
                    stream_key="judge-tasks-python",
                    consumer_group="judge-python-workers",
                    judge_service="judge-python",
                    prometheus_instance="judge-python:8001",
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
