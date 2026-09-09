from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from hinataops.ops_mcp.adapters.docker import DockerReadonlyAdapter
from hinataops.ops_mcp.config import ServiceConfig
from hinataops.ops_mcp.contracts import ObservationMetadata, new_metadata


class ContainerSnapshot(BaseModel):
    """保留给配置拓扑外容器的最小 Docker 状态。"""

    name: str
    image: str
    state: str
    status: str


class ServiceSnapshot(BaseModel):
    """HinataOps 拓扑中已声明服务的 Docker 观测状态。"""

    service: str
    container: str
    # 容器缺失时为 null，与 state="missing" 共同表达配置与运行时不一致。
    image: str | None
    state: str
    status: str


class DockerSnapshot(BaseModel):
    """只读容器观测 Tool 返回的有界 Docker 证据。"""

    metadata: ObservationMetadata
    services: list[ServiceSnapshot]
    sandbox_pool_running: int
    sandbox_pool_exited: int
    unmanaged_running: list[ContainerSnapshot]
    unmanaged_exited_count: int
    # 仅保留固定上限的样本，避免历史容器耗尽 Agent 上下文。
    recent_unmanaged_exited: list[ContainerSnapshot]


class DockerInspector:
    """将固定 Docker 列表转换为带拓扑语义的运维证据。

    它会刻意分离 AoiLearn 服务、判题沙箱容器和无关容器。这三类对象的诊断含义不同，
    不能压缩为一个容易误导的健康数量。
    """

    # ``-a`` 必不可少：已停止的配置服务本身就是证据；``docker ps`` 会遗漏它，导致其
    # 无法与配置错误区分。
    # 共享开发虚拟机中的历史容器可能很多。保留总数与少量样本即可，不能耗尽 Agent 上下文。
    _MAX_RECENT_UNMANAGED_EXITED = 10

    def __init__(
        self,
        environment_name: str,
        docker: DockerReadonlyAdapter,
        services: Sequence[ServiceConfig],
    ) -> None:
        self._environment_name = environment_name
        self._docker = docker
        self._services = list(services)
        self._configured_container_names = {service.container for service in services}

    async def snapshot(self) -> DockerSnapshot:
        """使用固定只读查询采集并分类当前 Docker 状态。"""
        # Docker JSON 解析属于通用适配器；这里仅保留 AoiLearn 的拓扑与沙箱诊断语义。
        containers = [ContainerSnapshot.model_validate(item.model_dump()) for item in await self._docker.list_containers()]
        containers_by_name = {item.name: item for item in containers}
        # 遍历配置服务而不是观测到的容器，使缺失的关键容器能明确标为 ``missing``，而非悄然消失。
        known_services = [
            ServiceSnapshot(
                service=service.name,
                container=service.container,
                image=container.image if container else None,
                state=container.state if container else "missing",
                status=container.status if container else "Container not found",
            )
            for service in self._services
            for container in [containers_by_name.get(service.container)]
        ]
        sandbox_pool = [
            item for item in containers if item.image.startswith("aoilearn/sandbox:")
        ]
        # 沙箱有意设计为短生命周期且没有 Compose 标签。它是独立的容量信号，不是未管理的
        # 应用容器；已停止的池成员必须与当前可复用成员分别统计。
        unmanaged = [
            item
            for item in containers
            if item.name not in self._configured_container_names
            and item not in sandbox_pool
        ]
        return DockerSnapshot(
            metadata=new_metadata(self._environment_name, "docker"),
            services=known_services,
            sandbox_pool_running=sum(item.state == "running" for item in sandbox_pool),
            sandbox_pool_exited=sum(item.state != "running" for item in sandbox_pool),
            unmanaged_running=[item for item in unmanaged if item.state == "running"],
            unmanaged_exited_count=sum(item.state != "running" for item in unmanaged),
            recent_unmanaged_exited=[
                item for item in unmanaged if item.state != "running"
            ][: self._MAX_RECENT_UNMANAGED_EXITED],
        )
