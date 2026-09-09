import asyncio
from pathlib import Path

import pytest

from hinataops.actions.models import ApprovalRecord, VerificationResult
from hinataops.actions.repository import ActionRepository, InvalidActionStateError
from hinataops.ops_mcp.config import ActionSettings, load_environment_config
from hinataops.ops_mcp.server import create_server
from hinataops.ops_mcp.toolsets.aoi_learn_judge.action_service import AoiJudgeActionService
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import AoiJudgeToolsetSettings


def _service(tmp_path: Path) -> tuple[AoiJudgeActionService, ActionRepository]:
    """构造仅允许重启 Python Judge 的临时审批环境。"""
    config = load_environment_config(Path("config/environments/aoi-local.example.toml"))
    enabled_config = config.model_copy(
        update={"actions": ActionSettings(enabled=True, allowed_services=["judge-python"])}
    )
    repository = ActionRepository(tmp_path / "state.sqlite3")
    settings = AoiJudgeToolsetSettings.model_validate(
        enabled_config.toolset_config("aoi_learn_judge")
    )
    return AoiJudgeActionService(enabled_config, settings, repository), repository


def test_approval_binds_the_exact_action_plan_and_allows_one_execution_claim(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    plan = service.propose_restart(
        "judge-python",
        reason="Redis Stream lag 持续增长且 Worker 不可达",
        risk="重启期间该语言的新判题会短暂等待",
        rollback="若验证失败，停止自动处置并保留容器与指标证据供人工检查",
    )

    with pytest.raises(InvalidActionStateError, match="fingerprint"):
        service.decide(
            ApprovalRecord(
                action_id=plan.action_id,
                decision="approved",
                approver="operator",
                expected_fingerprint="0" * 64,
            )
        )

    assert service.decide(
        ApprovalRecord(
            action_id=plan.action_id,
            decision="approved",
            approver="operator",
            expected_fingerprint=plan.fingerprint,
        )
    ) == "approved"
    assert repository.claim_approved(plan.action_id).fingerprint == plan.fingerprint
    with pytest.raises(InvalidActionStateError, match="approved"):
        repository.claim_approved(plan.action_id)


def test_verification_is_required_before_a_successful_action_reaches_terminal_success(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    plan = service.propose_restart("judge-python", "测试", "短暂停顿", "人工处理")
    service.decide(
        ApprovalRecord(
            action_id=plan.action_id,
            decision="approved",
            approver="operator",
            expected_fingerprint=plan.fingerprint,
        )
    )
    repository.claim_approved(plan.action_id)
    assert repository.record_execution(plan.action_id, {"state": "running"}, succeeded=True) == "executing"
    assert repository.record_verification(
        plan.action_id,
        VerificationResult(passed=True, checks={"container_running": True, "judge_reachable": True}),
    ) == "verified"
    assert repository.get(plan.action_id)[1] == "verified"


def test_action_service_rejects_disabled_or_unlisted_service(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError, match="not allowed"):
        service.propose_restart("mysql", "测试", "风险", "回滚")


def test_restart_tool_refuses_pending_action_without_touching_infrastructure(tmp_path: Path) -> None:
    """验证知道 action_id 不等于拥有执行权。"""
    service, repository = _service(tmp_path)
    plan = service.propose_restart("judge-python", "测试", "风险", "人工处理")
    config = load_environment_config(Path("config/environments/aoi-local.example.toml")).model_copy(
        update={"actions": ActionSettings(enabled=True, allowed_services=["judge-python"])}
    )
    server = create_server(lambda: config, lambda: repository)

    result = asyncio.run(
        server._tool_manager.call_tool(
            "docker_restart_service", {"action_id": str(plan.action_id)}
        )
    )

    assert result["status"] == "pending_approval"
    assert repository.get(plan.action_id)[1] == "pending_approval"
