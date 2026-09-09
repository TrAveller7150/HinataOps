import asyncio

from hinataops.ops_mcp.adapters.docker import DockerContainerRecord
from hinataops.ops_mcp.config import ServiceConfig
from hinataops.ops_mcp.toolsets.aoi_learn_judge.container_runtime import DockerInspector


class FakeDockerAdapter:
    """为领域分类测试提供与真实 Docker 适配器一致的最小记录。"""

    async def list_containers(self) -> list[DockerContainerRecord]:
        return [
            DockerContainerRecord(name="aoi-learn-server-1", image="aoi-learn-server", state="running", status="Up 2 minutes"),
            DockerContainerRecord(name="pool-a", image="aoilearn/sandbox:prod", state="running", status="Up 2 minutes"),
            DockerContainerRecord(name="pool-old", image="aoilearn/sandbox:prod", state="exited", status="Exited (137)"),
            DockerContainerRecord(name="bondgumi-postgres-dev", image="postgres:17-alpine", state="running", status="Up 4 days"),
            DockerContainerRecord(name="old-container", image="legacy", state="exited", status="Exited (1)"),
        ]


def test_snapshot_separates_service_sandbox_and_unmanaged_containers() -> None:
    snapshot = asyncio.run(
        DockerInspector(
            "test",
            FakeDockerAdapter(),
            [
                ServiceConfig(
                    name="server",
                    container="aoi-learn-server-1",
                    depends_on=["mysql"],
                    role="application",
                )
            ],
        ).snapshot()
    )

    assert [(item.service, item.state) for item in snapshot.services] == [
        ("server", "running")
    ]
    assert snapshot.sandbox_pool_running == 1
    assert snapshot.sandbox_pool_exited == 1
    assert [item.name for item in snapshot.unmanaged_running] == [
        "bondgumi-postgres-dev"
    ]
    assert snapshot.unmanaged_exited_count == 1
    assert [item.name for item in snapshot.recent_unmanaged_exited] == [
        "old-container"
    ]


def test_snapshot_marks_absent_configured_service_as_missing() -> None:
    snapshot = asyncio.run(
        DockerInspector(
            "test",
            FakeDockerAdapter(),
            [
                ServiceConfig(
                    name="redis",
                    container="aoi-learn-redis-1",
                    depends_on=[],
                    role="stream",
                )
            ],
        ).snapshot()
    )

    assert snapshot.services[0].state == "missing"
    assert snapshot.services[0].status == "Container not found"
