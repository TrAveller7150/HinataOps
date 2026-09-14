"""面向人工调查的 HinataOps 单窗口命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from time import monotonic
from typing import Sequence

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hinataops.agent_core.events import InvestigationEvent
from hinataops.agent_core.llm import OpenAICompatibleStructuredOutputClient
from hinataops.agent_core.models import Hypothesis, InvestigationReport, Observation
from hinataops.agent_core.models import PlanningTrace
from hinataops.agent_core.gateway import StreamableHttpToolGateway
from hinataops.evaluation_runner import (
    DEFAULT_LLM_CONFIG_PATH,
    DEFAULT_MCP_URL,
    _configure_utf8_output,
    _require_llm_connection,
)
from hinataops.investigation_service import render_investigation_archive, run_readonly_investigation
from hinataops.ops_mcp.toolsets.aoi_learn_judge.investigation import (
    PYTHON_JUDGE_TASK_NO_RESULT,
)
from hinataops.ops_mcp.toolsets.aoi_learn_judge.presentation import (
    AoiLearnJudgeEvidencePresenter,
)

_DEFAULT_RUN_DIRECTORY = Path(".hinataops/runs")
_LOCAL_MCP_START_TIMEOUT_SECONDS = 12.0
_LOCAL_MCP_POLL_INTERVAL_SECONDS = 0.2

_TOOL_LABELS = {
    "aoi_judge_get_container_runtime": "Docker 判题运行时",
    "aoi_judge_get_runtime": "Prometheus 判题运行指标",
    "aoi_judge_get_stream_summary": "Redis 判题队列摘要",
    "aoi_judge_get_pipeline_summary": "MySQL 判题流水线摘要",
}


class LocalMcpServer(AbstractContextManager["LocalMcpServer"]):
    """托管本次 CLI 调查专属的本地 MCP 子进程，不接管用户显式指定的远端服务。"""

    def __init__(self, mcp_url: str = DEFAULT_MCP_URL) -> None:
        self._mcp_url = mcp_url
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def mcp_url(self) -> str:
        """返回子进程发布的固定本地 MCP 地址。"""
        return self._mcp_url

    def __enter__(self) -> "LocalMcpServer":
        """启动 MCP Server；就绪检查放在异步调查流程中以复用实际 Client 契约。"""
        self._process = subprocess.Popen(
            [sys.executable, "-m", "hinataops.ops_mcp.server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """无论调查成功、失败或被中断，都回收本次启动的 MCP 子进程。"""
        if self._process is None or self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)

    async def wait_until_ready(self) -> None:
        """以 MCP list_tools 作为就绪标准，避免端口已监听但 Toolset 尚未可用。"""
        deadline = monotonic() + _LOCAL_MCP_START_TIMEOUT_SECONDS
        while monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(f"本地 MCP Server 启动失败，退出码: {self._process.returncode}")
            try:
                await asyncio.wait_for(
                    StreamableHttpToolGateway(self._mcp_url).list_tools(), timeout=1.0
                )
            except Exception:
                await asyncio.sleep(_LOCAL_MCP_POLL_INTERVAL_SECONDS)
            else:
                return
        raise RuntimeError("本地 MCP Server 未在 12 秒内就绪；请检查 HINATAOPS_CONFIG 配置。")


class InvestigationConsole:
    """将工作流事件渲染为简洁中文进度和最终报告，不打印原始 MCP JSON。"""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._presenter = AoiLearnJudgeEvidencePresenter()

    def event_listener(self, event: InvestigationEvent) -> None:
        """显示可由人工理解的调查阶段，不泄露完整原始观测内容。"""
        if event.kind == "catalog_loaded":
            self._console.print(f"[dim]MCP 已就绪：{event.detail}。[/dim]")
        elif event.kind == "tools_planned":
            if event.detail is not None:
                self._console.print(f"[cyan]模型决策：{event.detail}[/cyan]")
            labels = "、".join(self._tool_label(call.name) for call in event.calls)
            self._console.print(f"[cyan]计划采证：{labels}[/cyan]")
        elif event.kind == "tools_started":
            self._console.print("[cyan]正在采集基础设施证据…[/cyan]")
        elif event.kind == "tool_completed" and event.observation is not None:
            observation = event.observation
            color = "green" if observation.reliability == "complete" else "yellow"
            self._console.print(f"[{color}]完成 {self._tool_label(observation.tool_name)}[/{color}]")
            for line in self._presenter.render(observation):
                self._console.print(f"  [dim]└─ {line}[/dim]")
        elif event.kind == "diagnosis_started":
            self._console.print("[cyan]正在根据证据生成诊断报告…[/cyan]")
        elif event.kind == "planning_finished" and event.detail is not None:
            self._console.print(f"[cyan]模型结束调查：{event.detail}[/cyan]")
        elif event.kind == "planning_failed" and event.detail is not None:
            self._console.print(
                f"[yellow]模型规划输出不可用：{event.detail}；将根据已采集证据生成报告。[/yellow]"
            )
        elif event.kind == "diagnosis_failed" and event.detail is not None:
            self._console.print(
                f"[yellow]模型诊断输出不可用：{event.detail}；已降级为事实性不确定报告。[/yellow]"
            )

    def info(self, message: str) -> None:
        """显示不包含基础设施原始数据的 CLI 状态提示。"""
        self._console.print(f"[dim]{message}[/dim]")

    def render_report(self, *, run_file: Path, run) -> None:
        """展示最终人类报告；完整可审查 JSON 保留在运行记录文件中。"""
        report = run.report
        status = "已形成诊断" if report.status == "diagnosed" else "未能确认根因"
        lines = [
            self._report_line("调查状态", status),
            self._report_line("结束方式", self._stop_reason(run.stop_reason)),
        ]
        if run.diagnosis_error is not None:
            lines.append(self._report_line("诊断降级原因", run.diagnosis_error))
        lines.append(self._report_line("结论", report.conclusion))
        if report.recommended_action is not None:
            lines.append(self._report_line("建议操作", report.recommended_action))
        lines.append(self._report_line("完整记录", str(run_file)))
        self._console.print(
            Panel(Group(*lines), title="HinataOps 调查报告", border_style="green")
        )
        self._render_planning_trace(run.planning_traces)
        self._render_hypotheses(report)

    def _render_planning_trace(self, traces: list[PlanningTrace]) -> None:
        """输出持久化的决策轨迹，让人工能区分模型决策、采证事实与降级原因。"""
        if not traces:
            return
        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("轮次", width=6)
        table.add_column("状态", width=12)
        table.add_column("可审计决策摘要", ratio=3, overflow="fold")
        table.add_column("选择的检查", ratio=2, overflow="fold")
        for trace in traces:
            calls = "、".join(self._tool_label(call.name) for call in trace.tool_calls) or "无"
            table.add_row(
                str(trace.investigation_round),
                self._trace_status(trace.status),
                self._trace_summary(trace),
                calls,
            )
        self._console.print(Panel(table, title="调查决策轨迹", border_style="cyan"))

    def _render_hypotheses(self, report: InvestigationReport) -> None:
        """将模型候选假设及其证据角色展示出来，不把不确定结论伪装为无推理。"""
        if not report.hypotheses:
            self._console.print("[yellow]模型未形成候选根因；当前只能根据已采证事实给出不确定结论。[/yellow]")
            return
        observations = {item.evidence_id: item for item in report.observations}
        table = Table(show_header=True, header_style="bold", expand=True)
        table.add_column("角色", width=16)
        table.add_column("候选根因", ratio=2, overflow="fold")
        table.add_column("证据与待补充信息", ratio=3, overflow="fold")
        for hypothesis in report.hypotheses:
            table.add_row(
                self._hypothesis_role(hypothesis, report),
                f"{hypothesis.cause}\n置信度：{hypothesis.confidence:.0%}",
                self._hypothesis_evidence(hypothesis, observations),
            )
        self._console.print(Panel(table, title="模型假设评估", border_style="cyan"))

    def _hypothesis_evidence(
        self, hypothesis: Hypothesis, observations: dict, 
    ) -> str:
        """用 Tool 名称定位支持和反驳来源，避免在报告区重复粘贴完整指标。"""
        supporting = self._evidence_labels(hypothesis.supporting_evidence_ids, observations)
        contradicting = self._evidence_labels(hypothesis.contradicting_evidence_ids, observations)
        missing = "；".join(hypothesis.missing_evidence) or "无"
        return f"支持：{supporting}\n反驳：{contradicting}\n待补充：{missing}"

    def _evidence_labels(self, evidence_ids: list, observations: dict) -> str:
        """将 Evidence ID 映射为已展示过的领域 Tool 名称。"""
        labels = [
            self._tool_label(observations[evidence_id].tool_name)
            for evidence_id in evidence_ids
            if evidence_id in observations
        ]
        return "、".join(labels) if labels else "无"

    @staticmethod
    def _hypothesis_role(hypothesis: Hypothesis, report: InvestigationReport) -> str:
        """区分主根因、普通候选与仅被当前证据反驳的候选。"""
        if hypothesis.hypothesis_id == report.primary_hypothesis_id:
            return "主根因"
        if not hypothesis.supporting_evidence_ids and hypothesis.contradicting_evidence_ids:
            return "被证据反驳"
        return "候选根因"

    @staticmethod
    def _stop_reason(reason: str) -> str:
        """把内部停止码翻译为调查完成度，避免预算截断被误读为模型结论。"""
        if "Tool 调用次数预算" in reason or "调查轮次预算" in reason:
            return f"预算受限结束：{reason}"
        if reason.startswith("模型认为") or reason.startswith("模型未找到"):
            return f"模型主动结束：{reason}"
        return reason

    @staticmethod
    def _trace_status(status: str) -> str:
        """将内部轨迹状态转成面向人工的中文阶段。"""
        return {"planned": "已规划", "finished": "主动结束", "failed": "规划失败"}[status]

    @staticmethod
    def _report_line(label: str, value: str) -> Text:
        """报告区使用可折行文本，避免长路径或结论压缩整张二维表。"""
        return Text.assemble((f"{label} ", "bold"), value)

    @staticmethod
    def _trace_summary(trace: PlanningTrace) -> str:
        """保留可兼容字段的审计痕迹，提示人工这是格式恢复而非模型新结论。"""
        return (
            trace.summary
            if trace.compatibility_note is None
            else f"{trace.summary}\n格式兼容：{trace.compatibility_note}"
        )

    @staticmethod
    def _tool_label(tool_name: str) -> str:
        return _TOOL_LABELS.get(tool_name, tool_name)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析人工调查命令；当前只开放已经具有真实评测基线的一个受控场景。"""
    parser = argparse.ArgumentParser(description="HinataOps 人工调查 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    investigate = subparsers.add_parser("investigate", help="启动一次只读故障调查")
    investigate.add_argument(
        "scenario",
        choices=("python-judge-task-no-result",),
        help="当前支持的事故画像，不包含预期根因",
    )
    investigate.add_argument(
        "--mcp-url",
        help="使用已启动的 MCP Server；指定后 CLI 不会启动或停止它",
    )
    investigate.add_argument(
        "--llm-config",
        type=Path,
        default=DEFAULT_LLM_CONFIG_PATH,
        help="被 Git 忽略的本机 DeepSeek 配置文件路径",
    )
    investigate.add_argument(
        "--output-file",
        type=Path,
        help="完整 JSON 运行记录路径；省略时自动写入 .hinataops/runs",
    )
    return parser.parse_args(argv)


def _default_run_file() -> Path:
    """生成可排序且不覆盖历史调查的默认本地运行记录路径。"""
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return _DEFAULT_RUN_DIRECTORY / f"python-judge-task-no-result-{timestamp}.json"


async def _investigate(args: argparse.Namespace, console: InvestigationConsole) -> Path:
    """运行人工事故画像，并根据是否指定 MCP URL 决定 Server 生命周期归属。"""
    run_file = args.output_file or _default_run_file()
    connection = _require_llm_connection(args.llm_config)
    profile = PYTHON_JUDGE_TASK_NO_RESULT
    client = OpenAICompatibleStructuredOutputClient(
        model=connection.model,
        api_key=connection.api_key,
        base_url=connection.base_url,
        response_format_mode=connection.response_format_mode,
        max_tokens=connection.max_tokens,
    )
    if args.mcp_url is not None:
        console.info(f"使用外部 MCP Server：{args.mcp_url}")
        run = await run_readonly_investigation(
            mcp_url=args.mcp_url,
            client=client,
            incident=profile.new_incident(),
            readonly_tool_names=profile.readonly_tool_names,
            max_tool_calls=profile.max_tool_calls,
            max_rounds=profile.max_rounds,
            event_listener=console.event_listener,
        )
    else:
        if await _is_mcp_available(DEFAULT_MCP_URL):
            raise RuntimeError(
                "本地 MCP 地址已被已有 Server 占用；如需复用它，请显式传入 --mcp-url"
            )
        console.info("正在启动本地 MCP Server…")
        with LocalMcpServer() as server:
            await server.wait_until_ready()
            run = await run_readonly_investigation(
                mcp_url=server.mcp_url,
                client=client,
                incident=profile.new_incident(),
                readonly_tool_names=profile.readonly_tool_names,
                max_tool_calls=profile.max_tool_calls,
                max_rounds=profile.max_rounds,
                event_listener=console.event_listener,
            )
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(
        f"{render_investigation_archive(model=connection.model, profile_id=profile.profile_id, run=run)}\n",
        encoding="utf-8",
    )
    console.render_report(run_file=run_file, run=run)
    return run_file


async def _is_mcp_available(mcp_url: str) -> bool:
    """检测已有 MCP 服务，避免自动模式误连到遗留进程或回收错误的生命周期。"""
    try:
        await asyncio.wait_for(StreamableHttpToolGateway(mcp_url).list_tools(), timeout=1.0)
    except Exception:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> None:
    """执行面向人工的调查命令，并将启动失败转换为简明终端错误。"""
    _configure_utf8_output()
    args = _parse_args(argv)
    console = InvestigationConsole()
    try:
        asyncio.run(_investigate(args, console))
    except (RuntimeError, ValueError) as error:
        raise SystemExit(f"无法完成调查: {error}") from error


if __name__ == "__main__":
    main()
