"""Prometheus 只读访问适配器。"""

from __future__ import annotations

from urllib.parse import quote

from hinataops.ops_mcp.adapters.ssh import SshRunner
from hinataops.ops_mcp.policy import InfrastructureInstanceConfig


class PrometheusReadonlyAdapter:
    """在固定访问容器内批量执行内部定义的瞬时 PromQL 查询。"""

    def __init__(self, runner: SshRunner, instance: InfrastructureInstanceConfig) -> None:
        if instance.kind != "prometheus" or not instance.access_container:
            raise ValueError("PrometheusReadonlyAdapter requires an access container")
        self._runner = runner
        self._instance = instance

    # 七条固定查询在同一 SSH 预算内串行执行。单条请求必须有更小的硬上限，
    # 避免某次内部 HTTP 卡顿独占整个 Tool 的 15 秒预算。
    _CURL_CONNECT_TIMEOUT_SECONDS = 1
    _CURL_MAX_TIME_SECONDS = 1.5

    async def query_many_fixed(self, queries: tuple[str, ...]) -> str:
        """用单条 SSH 会话批量查询固定 PromQL，避免并发连接耗尽预算。"""
        commands = []
        for query in queries:
            url = f"http://prometheus:9090/api/v1/query?query={quote(query, safe='')}"
            commands.append(
                "curl -fsS "
                f"--connect-timeout {self._CURL_CONNECT_TIMEOUT_SECONDS} "
                f"--max-time {self._CURL_MAX_TIME_SECONDS} "
                f'"{url}"; printf "\\n"'
            )
        command = f"docker exec {self._instance.access_container} sh -lc '{'; '.join(commands)}'"
        return await self._runner.run(
            command,
            max_output_bytes=self._instance.policy.max_output_bytes,
            timeout_seconds=self._instance.policy.timeout_seconds,
        )
