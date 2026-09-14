"""AoiLearn 判题领域 MCP Tool 的独立 Plugin 注册器。"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP

from hinataops.actions.models import ActionExecutionReport, VerificationResult
from hinataops.actions.repository import InvalidActionStateError
from hinataops.ops_mcp.adapters import SshCommandError, SshRunner
from hinataops.ops_mcp.actions import DockerRestartExecutor
from hinataops.ops_mcp.config import EnvironmentConfig
from hinataops.ops_mcp.contracts import ObservationError, new_metadata
from hinataops.ops_mcp.plugins import ToolsetContext
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import AoiJudgeToolsetSettings
from hinataops.ops_mcp.toolsets.aoi_learn_judge.pipeline_summary import MysqlJudgePipelineSummary
from hinataops.ops_mcp.toolsets.aoi_learn_judge.restart import AoiJudgeRestartVerifier
from hinataops.ops_mcp.toolsets.aoi_learn_judge.runtime_summary import PrometheusJudgeRuntimeSummary
from hinataops.ops_mcp.toolsets.aoi_learn_judge.stream_summary import JudgeLanguage, RedisStreamSummary
from hinataops.ops_mcp.toolsets.aoi_learn_judge.toolset import AoiLearnJudgeToolset

logger = logging.getLogger(__name__)


class AoiLearnJudgePlugin:
    """将 AoiLearn 判题领域能力安装到通用 MCP Server。"""

    name = "aoi_learn_judge"

    def register(self, mcp: FastMCP, context: ToolsetContext) -> None:
        """注册只读调查 Tool，并按环境开关选择性注册受控重启 Tool。"""
        self._settings(context.config_provider())
        self._register_observation_tools(mcp, context)
        if context.config_provider().actions.enabled:
            self._register_action_tools(mcp, context)

    def _settings(self, config: EnvironmentConfig) -> AoiJudgeToolsetSettings:
        """由 Plugin 独占 AoiLearn 专属配置的校验与解释。"""
        return AoiJudgeToolsetSettings.model_validate(config.toolset_config(self.name))

    def _toolset(self, config: EnvironmentConfig) -> AoiLearnJudgeToolset:
        return AoiLearnJudgeToolset(config, self._settings(config))

    @staticmethod
    def _observation_error(source: str, error: Exception) -> ObservationError:
        """将基础设施异常转换为领域 Tool 的脱敏错误契约。"""
        logger.warning("%s 观测失败: %s", source, error)
        if isinstance(error, SshCommandError):
            if error.kind == "ssh_command_timeout":
                return ObservationError(
                    kind="ssh_command_timeout",
                    message=f"{source} 命令在受限时间内未完成",
                    retryable=True,
                )
            if error.kind == "output_budget_exceeded":
                return ObservationError(
                    kind="output_budget_exceeded",
                    message=f"{source} 返回数据超过受审核输出上限",
                    retryable=False,
                )
            return ObservationError(
                kind="remote_command_failed",
                message=f"{source} 的远端只读命令执行失败",
                retryable=False,
            )
        return ObservationError(
            kind="parse_error",
            message=f"{source} 返回了无法识别的数据格式",
            retryable=False,
        )

    def _register_observation_tools(self, mcp: FastMCP, context: ToolsetContext) -> None:
        @mcp.tool(name="aoi_judge_get_container_runtime")
        async def aoi_judge_get_container_runtime() -> dict:
            """返回判题相关拓扑服务和沙箱池的有界 Docker 状态。"""
            config = context.config_provider()
            try:
                return (await self._toolset(config).get_container_runtime()).model_dump(mode="json")
            except Exception as error:
                return {
                    "metadata": new_metadata(
                        config.environment.name,
                        "docker",
                        complete=False,
                        error=self._observation_error("Docker 判题运行时", error),
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
            config = context.config_provider()
            settings = self._settings(config)
            stream = next(item for item in settings.judge_streams if item.language == language)
            try:
                result = await self._toolset(config).get_stream_summary(language)
            except Exception as error:
                result = RedisStreamSummary(
                    metadata=new_metadata(
                        config.environment.name,
                        "redis",
                        complete=False,
                        error=self._observation_error("Redis 判题 Stream", error),
                    ),
                    language=language,
                    stream_key=stream.stream_key,
                    history_length=0,
                    consumer_group=None,
                )
            return result.model_dump(mode="json")

        @mcp.tool(name="aoi_judge_get_pipeline_summary")
        async def aoi_judge_get_pipeline_summary(
            window_minutes: Literal[5, 15, 60]
        ) -> dict:
            """返回受限时间窗口内判题任务与 Outbox 的聚合状态。"""
            config = context.config_provider()
            try:
                result = await self._toolset(config).get_pipeline_summary(window_minutes)
            except Exception as error:
                result = MysqlJudgePipelineSummary(
                    metadata=new_metadata(
                        config.environment.name,
                        "mysql",
                        complete=False,
                        error=self._observation_error("MySQL 判题流水线", error),
                    ),
                    window_minutes=window_minutes,
                    task_statuses=[],
                    outbox_statuses=[],
                )
            return result.model_dump(mode="json")

        @mcp.tool(name="aoi_judge_get_runtime")
        async def aoi_judge_get_runtime() -> dict:
            """返回 Judge Worker、沙箱池容量和资源指标的即时证据。"""
            config = context.config_provider()
            try:
                result = await self._toolset(config).get_runtime()
            except Exception as error:
                result = PrometheusJudgeRuntimeSummary(
                    metadata=new_metadata(
                        config.environment.name,
                        "prometheus",
                        complete=False,
                        error=self._observation_error("Prometheus 判题运行时", error),
                    ),
                    runtimes=[],
                )
            return result.model_dump(mode="json")

    def _register_action_tools(self, mcp: FastMCP, context: ToolsetContext) -> None:
        @mcp.tool(name="docker_restart_service")
        async def docker_restart_service(action_id: UUID) -> dict:
            """执行已批准的 AoiLearn 服务重启，并验证其恢复状态。"""
            config = context.config_provider()
            repository = context.action_repository_provider()
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

            settings = self._settings(config)
            docker_instance = config.instance(settings.docker_instance_id, "docker")
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
                verification = await AoiJudgeRestartVerifier(config, settings).verify(plan.service)
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


def create_plugin() -> AoiLearnJudgePlugin:
    """供 Python package entry point 调用的无状态 Plugin 工厂。"""
    return AoiLearnJudgePlugin()
