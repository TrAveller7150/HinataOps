import asyncio

from hinataops.ops_mcp.actions.docker import DockerRestartExecutor
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class FakeRunner:
    """记录受控写命令，避免单元测试触发真实 Docker 重启。"""

    async def run(
        self, command: str, *, max_output_bytes: int, timeout_seconds: int
    ) -> str:
        assert max_output_bytes > 0
        assert timeout_seconds > 0
        if command == "docker restart aoi-learn-judge-python-1":
            return "aoi-learn-judge-python-1\n"
        if command.startswith("docker inspect --format"):
            return "running\t2026-09-09T10:00:00Z\n"
        raise AssertionError(f"未预期命令: {command}")


def test_restart_executor_rechecks_container_state() -> None:
    result = asyncio.run(
        DockerRestartExecutor(
            FakeRunner(),
            InfrastructureInstanceConfig(id="aoi-docker", kind="docker"),
        ).restart("aoi-learn-judge-python-1")
    )

    assert result.state == "running"
    assert result.started_at == "2026-09-09T10:00:00Z"
