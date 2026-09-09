"""动作审批与执行状态的 SQLite 持久化。"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from hinataops.actions.models import ActionPlan, ActionStatus, ApprovalRecord, VerificationResult


class ActionNotFoundError(ValueError):
    """指定动作不存在时抛出，避免执行端猜测或创建新动作。"""


class InvalidActionStateError(ValueError):
    """动作状态不能进行当前转换时抛出。"""


class ActionRepository:
    """以原子状态转换保存动作计划、审批决定和执行验证结果。"""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(self, plan: ActionPlan) -> ActionPlan:
        """保存一条尚待审批的不可变动作计划。"""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO actions (
                    action_id, plan_json, fingerprint, status, created_at
                ) VALUES (?, ?, ?, 'pending_approval', ?)
                """,
                (
                    str(plan.action_id),
                    plan.model_dump_json(),
                    plan.fingerprint,
                    plan.created_at.isoformat(),
                ),
            )
        return plan

    def get(self, action_id: UUID) -> tuple[ActionPlan, ActionStatus]:
        """读取计划及当前状态；计划内容永远来自持久化快照。"""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json, status FROM actions WHERE action_id = ?", (str(action_id),)
            ).fetchone()
        if row is None:
            raise ActionNotFoundError(f"Action '{action_id}' does not exist")
        return ActionPlan.model_validate_json(row["plan_json"]), row["status"]

    def decide(self, record: ApprovalRecord) -> ActionStatus:
        """原子记录人工决定；审批必须匹配人类看到的计划指纹。"""
        target_status: ActionStatus = record.decision
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fingerprint FROM actions WHERE action_id = ?", (str(record.action_id),)
            ).fetchone()
            if row is None:
                raise ActionNotFoundError(f"Action '{record.action_id}' does not exist")
            if row["fingerprint"] != record.expected_fingerprint:
                raise InvalidActionStateError("Approval fingerprint does not match action plan")
            updated = connection.execute(
                """
                UPDATE actions
                SET status = ?, approver = ?, approval_comment = ?, decided_at = ?
                WHERE action_id = ? AND status = 'pending_approval'
                """,
                (
                    target_status,
                    record.approver,
                    record.comment,
                    record.decided_at.isoformat(),
                    str(record.action_id),
                ),
            ).rowcount
        if updated != 1:
            raise InvalidActionStateError("Only a pending action can be decided")
        return target_status

    def claim_approved(self, action_id: UUID) -> ActionPlan:
        """一次性领取已批准动作，防止并发 MCP 调用重复执行重启。"""
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE actions
                SET status = 'executing', execution_started_at = ?
                WHERE action_id = ? AND status = 'approved'
                """,
                (datetime.now(UTC).isoformat(), str(action_id)),
            ).rowcount
            if updated != 1:
                raise InvalidActionStateError("Action must be approved and unclaimed before execution")
            row = connection.execute(
                "SELECT plan_json FROM actions WHERE action_id = ?", (str(action_id),)
            ).fetchone()
        if row is None:
            raise ActionNotFoundError(f"Action '{action_id}' does not exist")
        return ActionPlan.model_validate_json(row["plan_json"])

    def record_execution(self, action_id: UUID, result: dict, succeeded: bool) -> ActionStatus:
        """保存受控执行结果；失败不会伪装成已经恢复。"""
        target_status: ActionStatus = "execution_failed" if not succeeded else "executing"
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE actions
                SET status = ?, execution_result_json = ?, execution_finished_at = ?
                WHERE action_id = ? AND status = 'executing'
                """,
                (target_status, json.dumps(result), datetime.now(UTC).isoformat(), str(action_id)),
            ).rowcount
        if updated != 1:
            raise InvalidActionStateError("Only a claimed action can record execution")
        return target_status

    def record_verification(self, action_id: UUID, result: VerificationResult) -> ActionStatus:
        """保存恢复验证，并将成功和失败明确区分为终态。"""
        target_status: ActionStatus = "verified" if result.passed else "verification_failed"
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE actions
                SET status = ?, verification_result_json = ?
                WHERE action_id = ? AND status = 'executing'
                """,
                (target_status, result.model_dump_json(), str(action_id)),
            ).rowcount
        if updated != 1:
            raise InvalidActionStateError("Only a successfully executing action can be verified")
        return target_status

    def _initialize(self) -> None:
        """初始化最小审计表；SQLite 是当前 Demo 的共享授权事实来源。"""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    plan_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approver TEXT,
                    approval_comment TEXT,
                    decided_at TEXT,
                    execution_started_at TEXT,
                    execution_finished_at TEXT,
                    execution_result_json TEXT,
                    verification_result_json TEXT
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        """创建支持事务语义与字段名访问的短生命周期连接。"""
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection
