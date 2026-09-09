"""Docker 只读访问适配器。"""

from __future__ import annotations

import json

from pydantic import BaseModel

from hinataops.ops_mcp.adapters.ssh import SshRunner
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class DockerContainerRecord(BaseModel):
    """Docker 列表返回的一条最小容器记录。"""

    name: str
    image: str
    state: str
    status: str


class DockerReadonlyAdapter:
    """从固定 Docker 实例读取全量容器状态，不承担任何领域分类。"""

    _LIST_CONTAINERS_COMMAND = "docker ps -a --format '{{json .}}'"

    def __init__(self, runner: SshRunner, instance: InfrastructureInstanceConfig) -> None:
        if instance.kind != "docker":
            raise ValueError("DockerReadonlyAdapter requires a docker instance")
        self._runner = runner
        self._instance = instance

    async def list_containers(self) -> list[DockerContainerRecord]:
        """执行固定的容器列表命令，并受实例输出预算约束。"""
        output = await self._runner.run(
            self._LIST_CONTAINERS_COMMAND,
            max_output_bytes=self._instance.policy.max_output_bytes,
            timeout_seconds=self._instance.policy.timeout_seconds,
        )
        records = [
            DockerContainerRecord(
                name=item["Names"], image=item["Image"], state=item["State"], status=item["Status"]
            )
            for line in output.splitlines()
            if line.strip()
            for item in [json.loads(line)]
        ]
        return records[: self._instance.policy.max_items]
