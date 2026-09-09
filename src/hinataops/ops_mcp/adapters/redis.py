"""Redis 只读访问适配器。"""

from __future__ import annotations

import shlex

from hinataops.ops_mcp.adapters.ssh import SshRunner
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class RedisReadonlyAdapter:
    """仅执行内部预定义 Redis 读取命令的适配器。"""

    def __init__(self, runner: SshRunner, instance: InfrastructureInstanceConfig) -> None:
        if instance.kind != "redis" or not instance.container:
            raise ValueError("RedisReadonlyAdapter requires a configured redis container")
        self._runner = runner
        self._instance = instance

    async def xinfo_groups(self, stream_key: str) -> str:
        """读取一个已配置 Stream 的 Consumer Group 元数据。"""
        return await self._execute("XINFO", "GROUPS", stream_key)

    async def xpending(self, stream_key: str, consumer_group: str) -> str:
        """读取一个已配置 Group 的 pending 摘要。"""
        return await self._execute("XPENDING", stream_key, consumer_group)

    async def xlen(self, stream_key: str) -> str:
        """读取一个已配置 Stream 的历史长度。"""
        return await self._execute("XLEN", stream_key)

    async def _execute(self, *arguments: str) -> str:
        """以安全转义的固定 redis-cli 形式执行内部读取命令。"""
        command = " ".join(
            [
                "docker",
                "exec",
                self._instance.container or "",
                "redis-cli",
                "--json",
                *(shlex.quote(argument) for argument in arguments),
            ]
        )
        return await self._runner.run(
            command,
            max_output_bytes=self._instance.policy.max_output_bytes,
            timeout_seconds=self._instance.policy.timeout_seconds,
        )
