from __future__ import annotations

import asyncio

from hinataops.ops_mcp.config import EnvironmentDetails


class SshCommandError(RuntimeError):
    """有界 SSH 观测命令无法完成时抛出的异常。"""


class SshRunner:
    """在一个已配置主机上执行预先定义的只读命令。

    该类只提供传输边界。调用方必须定义固定且经过审核的远端命令；它绝不能接收
    来自 MCP Client 或 LLM 的命令文本。
    """

    def __init__(self, environment: EnvironmentDetails) -> None:
        self._environment = environment

    async def run(
        self, remote_command: str, *, max_output_bytes: int, timeout_seconds: int
    ) -> str:
        """执行一条经过预审核的远端命令，并限制其返回内容。"""
        # 使用 argv 列表而不是本地 Shell，避免本地 Shell 插值，并显式固定私钥、主机与连接策略。
        command = [
            "ssh",
            "-i",
            str(self._environment.identity_file),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",  # 绝不让 Agent 运行因等待密码输入而阻塞。
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={self._environment.connect_timeout_seconds}",
            f"{self._environment.user}@{self._environment.host}",
            remote_command,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            # 环境超时限制网络传输，实例超时限制单个数据源查询；取更小者，防止某个
            # 通用实例在未来使用较宽松的配置突破目标环境的总连接预算。
            timeout = min(self._environment.command_timeout_seconds, timeout_seconds)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            # 超时的观测不能遗留仍占用目标环境连接的 SSH 子进程。
            process.kill()
            await process.wait()
            raise SshCommandError("SSH command timed out") from None

        if process.returncode != 0:
            message = stderr.decode().strip() or "SSH command failed"
            raise SshCommandError(message)
        if len(stdout) > max_output_bytes:
            raise SshCommandError("SSH command exceeded configured output budget")
        return stdout.decode()
