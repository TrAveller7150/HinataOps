import asyncio

from hinataops.config import ServiceConfig
from hinataops.docker import DockerInspector


class FakeRunner:
    async def run(self, remote_command: str) -> str:
        assert remote_command == "docker ps -a --format '{{json .}}'"
        return "\n".join(
            [
                '{"Names":"aoi-learn-server-1","Image":"aoi-learn-server","State":"running","Status":"Up 2 minutes"}',
                '{"Names":"pool-a","Image":"aoilearn/sandbox:prod","State":"running","Status":"Up 2 minutes"}',
                '{"Names":"pool-old","Image":"aoilearn/sandbox:prod","State":"exited","Status":"Exited (137)"}',
                '{"Names":"bondgumi-postgres-dev","Image":"postgres:17-alpine","State":"running","Status":"Up 4 days"}',
                '{"Names":"old-container","Image":"legacy","State":"exited","Status":"Exited (1)"}',
            ]
        )


def test_snapshot_separates_service_sandbox_and_unmanaged_containers() -> None:
    snapshot = asyncio.run(
        DockerInspector(
            FakeRunner(),
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
            FakeRunner(),
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
