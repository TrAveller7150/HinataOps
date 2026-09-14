from io import StringIO

from rich.console import Console

from hinataops.agent_core.models import Hypothesis, IncidentRequest, InvestigationReport
from hinataops.agent_core.workflow import InvestigationRun
from hinataops.cli import InvestigationConsole, _default_run_file, _parse_args
from hinataops.investigation_service import render_investigation_archive


def test_investigate_command_defaults_to_managed_local_mcp() -> None:
    """未指定地址时，人工 CLI 应托管本地 MCP，而非要求第二个终端。"""
    args = _parse_args(["investigate", "python-judge-task-no-result"])

    assert args.command == "investigate"
    assert args.mcp_url is None
    assert args.output_file is None


def test_investigate_command_accepts_external_mcp_url() -> None:
    """显式地址保留给开发调试或未来远端 MCP 部署使用。"""
    args = _parse_args(
        [
            "investigate",
            "python-judge-task-no-result",
            "--mcp-url",
            "http://127.0.0.1:9000/mcp",
        ]
    )

    assert args.mcp_url == "http://127.0.0.1:9000/mcp"


def test_default_run_file_uses_ignored_run_directory() -> None:
    """默认归档不污染控制台，也不覆盖此前的调查记录。"""
    run_file = _default_run_file()

    assert run_file.parent.as_posix() == ".hinataops/runs"
    assert run_file.name.startswith("python-judge-task-no-result-")
    assert run_file.suffix == ".json"


def test_human_investigation_archive_has_no_evaluation_ground_truth() -> None:
    """人工调查记录只保存实际 Incident 与报告，不能混入评测期望根因。"""
    incident = IncidentRequest(query="Python 判题无结果", target_environment="aoi-local")
    report = InvestigationReport(
        incident_id=incident.incident_id,
        status="inconclusive",
        observations=[],
        hypotheses=[],
        conclusion="证据不足。",
    )
    run = InvestigationRun(
        incident=incident,
        observations=[],
        completed_calls=[],
        investigation_rounds=0,
        stop_reason="模型未找到适用的后续只读检查，结束调查。",
        report=report,
    )

    archive = render_investigation_archive(
        model="test-model", profile_id="python_judge_task_no_result", run=run
    )

    assert '"profile_id": "python_judge_task_no_result"' in archive
    assert "evaluation" not in archive
    assert "expected_primary_cause_code" not in archive


def test_console_renders_inconclusive_candidate_hypothesis() -> None:
    """不确定报告也应展示模型已提出且被证据反驳的候选根因。"""
    output: list[str] = []
    console = Console(record=True, width=160)
    incident = IncidentRequest(query="Python 判题无结果", target_environment="aoi-local")
    hypothesis = Hypothesis(cause="Python Worker 不可用", confidence=0.15)
    report = InvestigationReport(
        incident_id=incident.incident_id,
        status="inconclusive",
        observations=[],
        hypotheses=[hypothesis],
        conclusion="尚无法确认。",
    )
    run = InvestigationRun(
        incident=incident,
        observations=[],
        completed_calls=[],
        investigation_rounds=0,
        stop_reason="已达到 Tool 调用次数预算",
        report=report,
    )

    InvestigationConsole(console).render_report(run_file=_default_run_file(), run=run)
    output.append(console.export_text())

    assert "模型假设评估" in output[0]
    assert "Python Worker 不可用" in output[0]
    assert "预算受限结束" in output[0]


def test_console_folds_long_report_content_instead_of_ellipsis() -> None:
    """窄终端应折行保留完整报告文字，而不是以省略号隐藏结论尾部。"""
    output_buffer = StringIO()
    console = Console(file=output_buffer, width=80, force_terminal=False)
    incident = IncidentRequest(query="Python 判题无结果", target_environment="aoi-local")
    report = InvestigationReport(
        incident_id=incident.incident_id,
        status="inconclusive",
        observations=[],
        hypotheses=[],
        conclusion="这是很长的结论文本，用于验证终端表格会自动折行且保留完整文本末尾标记。",
    )
    run = InvestigationRun(
        incident=incident,
        observations=[],
        completed_calls=[],
        investigation_rounds=0,
        stop_reason="证据不足",
        report=report,
    )

    InvestigationConsole(console).render_report(run_file=_default_run_file(), run=run)

    output = output_buffer.getvalue()
    assert "完整文本末尾标记" in output
    assert "…" not in output
