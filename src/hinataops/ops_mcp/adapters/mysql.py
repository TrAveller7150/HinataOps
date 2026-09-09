"""MySQL 只读访问适配器。"""

from __future__ import annotations

from hinataops.ops_mcp.adapters.ssh import SshRunner
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class MysqlReadonlyAdapter:
    """执行领域模块内置聚合 SQL 的 MySQL 访问边界。

    本阶段不接受 MCP 调用方提供的 SQL；未来通用 SQL Tool 必须在此边界之前加入
    AST 级只读校验与审计，不能复用本方法开放任意语句。
    """

    def __init__(self, runner: SshRunner, instance: InfrastructureInstanceConfig) -> None:
        if instance.kind != "mysql" or not instance.container:
            raise ValueError("MysqlReadonlyAdapter requires a configured mysql container")
        self._runner = runner
        self._instance = instance

    async def execute_trusted_tsv(self, statement: str) -> str:
        """执行调用方代码内固定的无结果敏感字段聚合 SQL。"""
        command = (
            f"docker exec {self._instance.container} sh -lc "
            "'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" --batch --raw "
            f"--skip-column-names --execute \"{statement}\" \"$MYSQL_DATABASE\"'"
        )
        return await self._runner.run(
            command,
            max_output_bytes=self._instance.policy.max_output_bytes,
            timeout_seconds=self._instance.policy.timeout_seconds,
        )
