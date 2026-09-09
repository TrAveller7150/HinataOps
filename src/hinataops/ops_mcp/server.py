"""HinataOps 受限运维 MCP Server 的注册入口。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from hinataops.actions.models import ActionExecutionReport, VerificationResult
from hinataops.actions.repository import ActionRepository, InvalidActionStateError
from hinataops.actions.runtime import action_repository
from hinataops.ops_mcp.adapters import SshCommandError, SshRunner
from hinataops.ops_mcp.actions import DockerRestartExecutor
from hinataops.ops_mcp.config import EnvironmentConfig, load_environment_config
from hinataops.ops_mcp.contracts import ObservationError, new_metadata
from hinataops.ops_mcp.toolsets.aoi_learn_judge import AoiLearnJudgeToolset
from hinataops.ops_mcp.toolsets.aoi_learn_judge.restart import AoiJudgeRestartVerifier
from hinataops.ops_mcp.toolsets.aoi_learn_judge.pipeline_summary import MysqlJudgePipelineSummary
from hinataops.ops_mcp.toolsets.aoi_learn_judge.runtime_summary import PrometheusJudgeRuntimeSummary
from hinataops.ops_mcp.toolsets.aoi_learn_judge.stream_summary import JudgeLanguage, RedisStreamSummary

logger = logging.getLogger(__name__)
ConfigProvider = Callable[[], EnvironmentConfig]
ActionRepositoryProvider = Callable[[], ActionRepository]


def _observation_error(source: str, error: Exception) -> ObservationError:
    """将底层异常转换为不会泄露基础设施细节的结构化观测错误。"""
    # 原始异常只写入 MCP Server 本地日志；MCP 返回值不能携带 SSH 命令、容器变量或密码。
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


def _register_topology_tool(mcp: FastMCP, config_provider: ConfigProvider) -> None:
    """注册仅返回静态受审核拓扑的基础查询 Tool。"""

    @mcp.tool(name="topology_get_service")
    async def topology_get_service(service: str) -> dict:
        """返回一个 AoiLearn 服务的已配置拓扑事实。"""
        config = config_provider()
        target = config.service(service)
        return {
            "environment": config.environment.name,
            "service": target.name,
            "container": target.container,
            "role": target.role,
            "depends_on": target.depends_on,
        }


def _register_aoi_judge_toolset(mcp: FastMCP, config_provider: ConfigProvider) -> None:
    """注册 AoiLearn 判题领域 Tool，并统一其失败语义。"""

    @mcp.tool(name="aoi_judge_get_container_runtime")
    async def aoi_judge_get_container_runtime() -> dict:
        """返回判题相关拓扑服务和沙箱池的有界 Docker 状态。"""
        config = config_provider()
        try:
            return (await AoiLearnJudgeToolset(config).get_container_runtime()).model_dump(mode="json")
        except Exception as error:
            return {
                "metadata": new_metadata(
                    config.environment.name,
                    "docker",
                    complete=False,
                    error=_observation_error("Docker 判题运行时", error),
                ).model_dump(mode="json"),
                "services": [],
                "sandbox_pool_running": 0,
                "sandbox_pool_exited": 0,
                "unmanaged_running": [],
                "unmanaged_exited_count": 0,
                "recent_unmanaged_exited": [],
            }

    @mcp.tool(name="aoi_judge_get_stream_summary")
    async def aoi_judge_get_stream_summary(language: JudgeLanguage) -> dict:
        """返回一种语言判题 Stream 的历史长度、lag 与 pending 证据。"""
        config = config_provider()
        stream = config.judge_stream(language)
        try:
            result = await AoiLearnJudgeToolset(config).get_stream_summary(language)
        except Exception as error:
            result = RedisStreamSummary(
                metadata=new_metadata(
                    config.environment.name,
                    "redis",
                    complete=False,
                    error=_observation_error("Redis 判题 Stream", error),
                ),
                language=language,
                stream_key=stream.stream_key,
                history_length=0,
                consumer_group=None,
            )
        return result.model_dump(mode="json")

    @mcp.tool(name="aoi_judge_get_pipeline_summary")
    async def aoi_judge_get_pipeline_summary(window_minutes: Literal[5, 15, 60]) -> dict:
        """返回受限时间窗口内判题任务与 Outbox 的聚合状态。"""
        config = config_provider()
        try:
            result = await AoiLearnJudgeToolset(config).get_pipeline_summary(window_minutes)
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

    @mcp.tool(name="aoi_judge_get_runtime")
    async def aoi_judge_get_runtime() -> dict:
        """返回 Judge Worker、沙箱池容量和资源指标的即时证据。"""
        config = config_provider()
        try:
            result = await AoiLearnJudgeToolset(config).get_runtime()
        except Exception as error:
            result = PrometheusJudgeRuntimeSummary(
                metadata=new_metadata(
                    config.environment.name,
                    "prometheus",
                    complete=False,
                    error=_observation_error("Prometheus 判题运行时", error),
                ),
                runtimes=[],
            )
        return result.model_dump(mode="json")


def _register_action_tools(
    mcp: FastMCP,
    config_provider: ConfigProvider,
    repository_provider: ActionRepositoryProvider,
) -> None:
    """注册只消费持久化审批计划的单个 Docker 写 Tool。"""

    @mcp.tool(name="docker_restart_service")
    async def docker_restart_service(action_id: UUID) -> dict:
        """执行已批准的服务重启，并记录重启后恢复验证结果。

        调用方只能提交审批系统生成的 ``action_id``。服务名、容器名、命令和验证条件均从
        持久化计划与环境配置读取，不能通过 MCP 参数绕过审批或扩大写入范围。
        """
        config = config_provider()
        repository = repository_provider()
        plan, status = repository.get(action_id)
        if (
            not config.actions.enabled
            or plan.action_type != "docker_restart_service"
            or plan.environment != config.environment.name
            or plan.service not in config.actions.allowed_services
        ):
            return ActionExecutionReport(
                action_id=action_id,
                service=plan.service,
                status=status,
                error="动作计划不属于当前允许执行的环境或服务",
            ).model_dump(mode="json")

        try:
            plan = repository.claim_approved(action_id)
        except InvalidActionStateError:
            _, current_status = repository.get(action_id)
            return ActionExecutionReport(
                action_id=action_id,
                service=plan.service,
                status=current_status,
                error="动作尚未获批，或已被其他执行器领取",
            ).model_dump(mode="json")

        docker_instance = config.instance(config.aoi_judge.docker_instance_id, "docker")
        try:
            execution = await DockerRestartExecutor(
                SshRunner(config.environment), docker_instance
            ).restart(config.service(plan.service).container)
        except Exception as error:
            repository.record_execution(action_id, {"message": type(error).__name__}, succeeded=False)
            return ActionExecutionReport(
                action_id=action_id,
                service=plan.service,
                status="execution_failed",
                error="容器重启命令未成功完成",
            ).model_dump(mode="json")

        repository.record_execution(action_id, execution.model_dump(mode="json"), succeeded=True)
        try:
            verification = await AoiJudgeRestartVerifier(config).verify(plan.service)
        except Exception as error:
            verification = VerificationResult(
                passed=False,
                checks={"verification_collection": False},
            )
            logger.warning("动作 %s 的恢复验证采集失败: %s", action_id, error)
        final_status = repository.record_verification(action_id, verification)
        return ActionExecutionReport(
            action_id=action_id,
            service=plan.service,
            status=final_status,
            execution=execution.model_dump(mode="json"),
            verification=verification,
        ).model_dump(mode="json")


def create_server(
    config_provider: ConfigProvider = load_environment_config,
    repository_provider: ActionRepositoryProvider = action_repository,
) -> FastMCP:
    """按配置构建 MCP Server，只注册被明确启用的领域 Toolset。"""
    mcp = FastMCP("HinataOps Ops")
    _register_topology_tool(mcp, config_provider)
    config = config_provider()
    if config.toolset_enabled("aoi_learn_judge"):
        _register_aoi_judge_toolset(mcp, config_provider)
    if config.actions.enabled:
        _register_action_tools(mcp, config_provider, repository_provider)
    return mcp


# 服务器是基础设施信任边界。通用 Toolset 仍处于配置与策略准备阶段，不暴露任意查询入口。
mcp = create_server()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
