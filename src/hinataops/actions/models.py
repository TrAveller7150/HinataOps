"""受控运维动作的稳定领域模型。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, computed_field

ActionType = Literal["docker_restart_service"]
ActionStatus = Literal[
    "pending_approval",
    "approved",
    "rejected",
    "executing",
    "execution_failed",
    "verification_failed",
    "verified",
]


class VerificationCheck(BaseModel):
    """一个必须在审批前确定的恢复检查项。"""

    name: str = Field(description="恢复检查的稳定名称")
    description: str = Field(description="人类审批时可见的预期恢复条件")


class ActionPlan(BaseModel):
    """不可变的单次运维动作计划，作为人工审批的实际对象。"""

    action_id: UUID = Field(default_factory=uuid4)
    action_type: ActionType
    environment: str
    service: str
    reason: str = Field(min_length=1, max_length=2_000)
    risk: str = Field(min_length=1, max_length=2_000)
    rollback: str = Field(min_length=1, max_length=2_000)
    verification_checks: list[VerificationCheck] = Field(min_length=1, max_length=10)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def fingerprint(self) -> str:
        """计算审批快照指纹，防止批准后替换目标、理由或验证条件。"""
        # ID 和创建时间只用于追踪，不能影响“同一动作内容”的指纹；其余字段使用排序 JSON
        # 固化，以保证 UI、审批服务和 MCP 执行端可以对同一计划得到相同结果。
        payload = {
            "action_type": self.action_type,
            "environment": self.environment,
            "service": self.service,
            "reason": self.reason,
            "risk": self.risk,
            "rollback": self.rollback,
            "verification_checks": [item.model_dump() for item in self.verification_checks],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ApprovalRecord(BaseModel):
    """一次人工审批或拒绝的不可变审计记录。"""

    action_id: UUID
    decision: Literal["approved", "rejected"]
    approver: str = Field(min_length=1, max_length=128)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    comment: str = Field(default="", max_length=2_000)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VerificationResult(BaseModel):
    """动作执行后针对计划检查项得出的确定性验证结果。"""

    passed: bool
    checks: dict[str, bool]
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ActionExecutionReport(BaseModel):
    """MCP 写 Tool 返回的执行与恢复验证摘要，不包含底层命令细节。"""

    action_id: UUID
    service: str
    status: ActionStatus
    execution: dict | None = None
    verification: VerificationResult | None = None
    error: str | None = None
