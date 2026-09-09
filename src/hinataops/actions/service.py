"""动作计划与审批的应用服务。"""

from __future__ import annotations

from hinataops.actions.models import ActionPlan, ApprovalRecord, VerificationCheck
from hinataops.actions.repository import ActionRepository
from hinataops.ops_mcp.config import EnvironmentConfig


class ActionService:
    """将环境允许列表与持久化审批状态机组合为受控处置入口。"""

    def __init__(self, config: EnvironmentConfig, repository: ActionRepository) -> None:
        self._config = config
        self._repository = repository

    def propose_restart(self, service: str, reason: str, risk: str, rollback: str) -> ActionPlan:
        """为允许服务创建需人工审批的重启计划，不执行任何基础设施操作。"""
        if not self._config.actions.enabled:
            raise ValueError("Actions are disabled for this environment")
        if service not in self._config.actions.allowed_services:
            raise ValueError(f"Service '{service}' is not allowed for restart")
        self._config.service(service)
        verification_checks = [
            VerificationCheck(name="container_running", description="目标容器重启后处于 running 状态")
        ]
        if any(stream.judge_service == service for stream in self._config.judge_streams):
            verification_checks.append(
                VerificationCheck(
                    name="judge_reachable",
                    description="Judge 服务的 Prometheus up 指标恢复为 1",
                )
            )
        plan = ActionPlan(
            action_type="docker_restart_service",
            environment=self._config.environment.name,
            service=service,
            reason=reason,
            risk=risk,
            rollback=rollback,
            verification_checks=verification_checks,
        )
        return self._repository.create(plan)

    def decide(self, record: ApprovalRecord) -> str:
        """记录人工审批决定，执行端只能消费通过审批的计划。"""
        return self._repository.decide(record)
