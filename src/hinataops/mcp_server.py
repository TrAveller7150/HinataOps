from __future__ import annotations

import logging
from typing import Literal

from hinataops.config import load_environment_config
from hinataops.docker import DockerInspector
from hinataops.mysql_observer import MysqlJudgePipelineInspector, MysqlJudgePipelineSummary
from hinataops.observation import ObservationError, new_metadata
from hinataops.prometheus_observer import (
    PrometheusJudgeRuntimeInspector,
    PrometheusJudgeRuntimeSummary,
)
from hinataops.redis_observer import JudgeLanguage, RedisStreamInspector, RedisStreamSummary
from hinataops.ssh import SshRunner
from hinataops.ssh import SshCommandError

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# 此服务器是基础设施信任边界。Agent Core 只能通过这些 Tool 请求事实，不能直接获得 SSH、
# Docker、数据库或 Redis 凭据。
mcp = FastMCP("HinataOps Ops")


def _config():
    """每次 Tool 调用加载当前本地配置，但不将配置暴露为调用输入。"""
    return load_environment_config()


def _observation_error(source: str, error: Exception) -> ObservationError:
    """将底层异常转换为不会泄露基础设施细节的结构化观测错误。"""
    # 原始异常只写入 MCP Server 本地日志，便于开发者排障；返回给 Agent 的内容必须脱敏，
    # 不能包含 SSH 命令、容器环境变量或基础设施拓扑细节。
    logger.warning("%s 观测失败: %s", source, error)
    if isinstance(error, SshCommandError):
        return ObservationError(
            kind="target_unavailable",
            message=f"无法完成 {source} 观测；请检查目标服务和 SSH 连通性",
            retryable=True,
        )
    return ObservationError(
        kind="parse_error",
        message=f"{source} 返回了无法识别的数据格式",
        retryable=False,
    )


@mcp.tool()
async def topology_get_service(service: str) -> dict:
    """返回一个 AoiLearn 服务的已配置拓扑事实。

    ``service`` 仅在经过审核的本地拓扑中解析，不会被解释为 Docker 容器名，以维持
    MCP Tool 的最小权限边界。
    """
    config = _config()
    target = config.service(service)
    return {
        "environment": config.environment.name,
        "service": target.name,
        "container": target.container,
        "role": target.role,
        "depends_on": target.depends_on,
    }


@mcp.tool()
async def docker_list_services() -> dict:
    """返回配置服务与沙箱池的有界只读 Docker 证据。

    Tool 不提供命令、容器名或过滤条件参数。固定命令和拓扑分类既能防止 Agent 将其作为
    远程 Shell 使用，又可暴露已停止服务和判题池容量信号。
    """
    config = _config()
    snapshot = await DockerInspector(
        SshRunner(config.environment), config.services
    ).snapshot()
    return snapshot.model_dump()


@mcp.tool()
async def redis_get_judge_stream_summary(language: JudgeLanguage) -> dict:
    """返回一种语言判题 Stream 的历史长度、Group lag 与 pending 证据。

    ``language`` 只能是 Python 或 SQL；实际 Stream 与 Consumer Group 从受审核环境配置中
    读取，避免调用方访问任意 Redis 键。
    """
    config = _config()
    stream = config.judge_stream(language)
    try:
        result = await RedisStreamInspector(
            SshRunner(config.environment),
            config.environment.name,
            config.service("redis").container,
            config.judge_streams,
        ).summary(language)
    except Exception as error:
        result = RedisStreamSummary(
            metadata=new_metadata(
                config.environment.name,
                "redis",
                complete=False,
                error=_observation_error("Redis Stream", error),
            ),
            language=language,
            stream_key=stream.stream_key,
            history_length=0,
            consumer_group=None,
        )
    return result.model_dump(mode="json")


@mcp.tool()
async def mysql_get_judge_pipeline_summary(
    window_minutes: Literal[5, 15, 60],
) -> dict:
    """返回受限时间窗口内判题任务与 Outbox 的状态、年龄和重试证据。

    仅允许 5、15 或 60 分钟窗口；查询固定为聚合 SQL，不会返回学生代码或接受任意 SQL。
    """
    config = _config()
    try:
        result = await MysqlJudgePipelineInspector(
            SshRunner(config.environment),
            config.environment.name,
            config.service("mysql").container,
        ).summary(window_minutes)
    except Exception as error:
        result = MysqlJudgePipelineSummary(
            metadata=new_metadata(
                config.environment.name,
                "mysql",
                complete=False,
                error=_observation_error("MySQL 判题流水线", error),
            ),
            window_minutes=window_minutes,
            task_statuses=[],
            outbox_statuses=[],
        )
    return result.model_dump(mode="json")


@mcp.tool()
async def prometheus_get_judge_runtime() -> dict:
    """返回两个 Judge Worker 的可达性、沙箱池容量和主机资源即时证据。

    PromQL 指标集固定在服务器内部，不暴露查询表达式参数，避免 MCP Tool 变成任意监控
    数据读取接口。
    """
    config = _config()
    try:
        result = await PrometheusJudgeRuntimeInspector(
            SshRunner(config.environment),
            config.environment.name,
            config.service("server").container,
            config.judge_streams,
        ).summary()
    except Exception as error:
        result = PrometheusJudgeRuntimeSummary(
            metadata=new_metadata(
                config.environment.name,
                "prometheus",
                complete=False,
                error=_observation_error("Prometheus", error),
            ),
            runtimes=[],
        )
    return result.model_dump(mode="json")


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
