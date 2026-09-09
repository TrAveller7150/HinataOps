"""AoiLearn 判题领域的 Toolset 组合根。"""

from __future__ import annotations

from typing import Literal

from hinataops.ops_mcp.adapters import (
    DockerReadonlyAdapter,
    MysqlReadonlyAdapter,
    PrometheusReadonlyAdapter,
    RedisReadonlyAdapter,
    SshRunner,
)
from hinataops.ops_mcp.config import EnvironmentConfig
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import AoiJudgeToolsetSettings
from hinataops.ops_mcp.toolsets.aoi_learn_judge.container_runtime import (
    DockerInspector,
    DockerSnapshot,
)
from hinataops.ops_mcp.toolsets.aoi_learn_judge.pipeline_summary import (
    MysqlJudgePipelineInspector,
    MysqlJudgePipelineSummary,
)
from hinataops.ops_mcp.toolsets.aoi_learn_judge.runtime_summary import (
    PrometheusJudgeRuntimeInspector,
    PrometheusJudgeRuntimeSummary,
)
from hinataops.ops_mcp.toolsets.aoi_learn_judge.stream_summary import (
    JudgeLanguage,
    RedisStreamInspector,
    RedisStreamSummary,
)


class AoiLearnJudgeToolset:
    """组合受审核实例与 AoiLearn 判题领域查询，不向外泄露底层实例选择。"""

    def __init__(
        self, environment: EnvironmentConfig, settings: AoiJudgeToolsetSettings
    ) -> None:
        self._environment = environment
        self._settings = settings
        runner = SshRunner(environment.environment)
        # Toolset 只从配置的实例注册表取访问路径。领域 Tool 不接收容器名、键名、SQL 或 PromQL，
        # 所以模型无法越过这层组合把 AoiLearn 观测扩展为任意基础设施执行入口。
        self._docker = DockerReadonlyAdapter(
            runner, environment.instance(settings.docker_instance_id, "docker")
        )
        self._mysql = MysqlReadonlyAdapter(
            runner, environment.instance(settings.mysql_instance_id, "mysql")
        )
        self._prometheus = PrometheusReadonlyAdapter(
            runner, environment.instance(settings.prometheus_instance_id, "prometheus")
        )
        redis_ids = {stream.redis_instance_id for stream in settings.judge_streams}
        if len(redis_ids) != 1:
            raise ValueError("AoiLearn judge toolset currently requires one Redis instance")
        self._redis = RedisReadonlyAdapter(
            runner, environment.instance(redis_ids.pop(), "redis")
        )

    async def get_container_runtime(self) -> DockerSnapshot:
        """返回拓扑服务与判题沙箱池的 Docker 状态。"""
        return await DockerInspector(
            self._environment.environment.name, self._docker, self._environment.services
        ).snapshot()

    async def get_stream_summary(self, language: JudgeLanguage) -> RedisStreamSummary:
        """返回一种语言判题 Stream 的消费进度。"""
        return await RedisStreamInspector(
            self._environment.environment.name, self._redis, self._settings.judge_streams
        ).summary(language)

    async def get_pipeline_summary(self, window_minutes: Literal[5, 15, 60]) -> MysqlJudgePipelineSummary:
        """返回判题任务与可靠入队 Outbox 的聚合状态。"""
        return await MysqlJudgePipelineInspector(
            self._environment.environment.name, self._mysql
        ).summary(window_minutes)

    async def get_runtime(self) -> PrometheusJudgeRuntimeSummary:
        """返回 Judge Worker 与沙箱池的指标快照。"""
        return await PrometheusJudgeRuntimeInspector(
            self._environment.environment.name, self._prometheus, self._settings.judge_streams
        ).summary()
