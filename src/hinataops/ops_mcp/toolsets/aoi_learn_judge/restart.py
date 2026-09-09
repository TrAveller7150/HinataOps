"""AoiLearn Judge 重启后的确定性恢复验证。"""

from __future__ import annotations

from hinataops.actions.models import VerificationResult
from hinataops.ops_mcp.config import EnvironmentConfig
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import AoiJudgeToolsetSettings
from hinataops.ops_mcp.toolsets.aoi_learn_judge.toolset import AoiLearnJudgeToolset


class AoiJudgeRestartVerifier:
    """用容器状态和 Judge 指标验证重启动作，而不把命令成功误判为事故恢复。"""

    def __init__(
        self, environment: EnvironmentConfig, settings: AoiJudgeToolsetSettings
    ) -> None:
        self._environment = environment
        self._settings = settings

    async def verify(self, service: str) -> VerificationResult:
        """验证目标容器运行，并在 Judge 服务场景验证 Prometheus 可达性。"""
        toolset = AoiLearnJudgeToolset(self._environment, self._settings)
        container_runtime = await toolset.get_container_runtime()
        service_snapshot = next(
            (item for item in container_runtime.services if item.service == service), None
        )
        checks = {"container_running": service_snapshot is not None and service_snapshot.state == "running"}

        matching_stream = next(
            (item for item in self._settings.judge_streams if item.judge_service == service), None
        )
        if matching_stream is not None:
            runtime_summary = await toolset.get_runtime()
            runtime = next(
                (item for item in runtime_summary.runtimes if item.language == matching_stream.language),
                None,
            )
            checks["judge_reachable"] = runtime is not None and runtime.up is True

        return VerificationResult(passed=all(checks.values()), checks=checks)
