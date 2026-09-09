"""Docker 受控重启动作执行器。"""

from __future__ import annotations

import shlex

from pydantic import BaseModel

from hinataops.ops_mcp.adapters.ssh import SshRunner
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class RestartExecutionResult(BaseModel):
    """一次 Docker 重启命令和其后容器状态检查的最小执行证据。"""

    container: str
    state: str
    # Docker 未返回启动时间时保留为 null，而不是伪造时间。
    started_at: str | None = None


class DockerRestartExecutor:
    """仅对已由审批计划绑定的配置容器执行 Docker restart。"""

    def __init__(self, runner: SshRunner, instance: InfrastructureInstanceConfig) -> None:
        if instance.kind != "docker":
            raise ValueError("DockerRestartExecutor requires a docker instance")
        self._runner = runner
        self._instance = instance

    async def restart(self, container: str) -> RestartExecutionResult:
        """重启单个受信任容器，并立即验证 Docker 报告其正在运行。"""
        # ``container`` 只从持久化审批计划取得，而计划只能由配置中的服务名创建；此处仍
        # 转义以防配置错误变成远端 Shell 注入。
        safe_container = shlex.quote(container)
        budget = self._instance.policy
        await self._runner.run(
            f"docker restart {safe_container}",
            max_output_bytes=budget.max_output_bytes,
            timeout_seconds=budget.timeout_seconds,
        )
        output = await self._runner.run(
            f"docker inspect --format '{{{{.State.Status}}}}\\t{{{{.State.StartedAt}}}}' {safe_container}",
            max_output_bytes=4_096,
            timeout_seconds=budget.timeout_seconds,
        )
        state, separator, started_at = output.strip().partition("\t")
        if not state:
            raise ValueError("Docker did not return container state after restart")
        return RestartExecutionResult(
            container=container,
            state=state,
            started_at=started_at if separator else None,
        )
