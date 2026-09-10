"""运行真实 LLM 故障评测的显式命令行入口，不负责启动 MCP Server。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Sequence
import tomllib

from hinataops.agent_core.evaluation import EvaluationResult, InvestigationEvaluator
from hinataops.agent_core.gateway import StreamableHttpToolGateway
from hinataops.agent_core.llm import (
    LlmInvestigationDiagnostician,
    LlmInvestigationPlanner,
    OpenAICompatibleStructuredOutputClient,
)
from hinataops.agent_core.policy import InvestigationBudget
from hinataops.agent_core.workflow import InvestigationRun, InvestigationWorkflow
from hinataops.ops_mcp.toolsets.aoi_learn_judge.evaluation import (
    PYTHON_JUDGE_WORKER_UNAVAILABLE,
)

DEEPSEEK_API_KEY_ENV = "HINATAOPS_DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_LLM_CONFIG_PATH = Path("config/environments/llm.local.toml")


def _load_api_key_from_file(config_path: Path) -> str:
    """读取被 Git 忽略的本机 LLM 配置；只接受预期的 DeepSeek 字段。"""
    try:
        with config_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise ValueError(f"未找到 LLM 本机配置文件: {config_path}") from error
    deepseek = data.get("deepseek")
    if not isinstance(deepseek, dict) or not isinstance(deepseek.get("api_key"), str):
        raise ValueError(f"{config_path} 缺少 [deepseek].api_key")
    api_key = deepseek["api_key"].strip()
    if not api_key:
        raise ValueError(f"{config_path} 的 [deepseek].api_key 不能为空")
    return api_key


def _require_api_key(config_path: Path) -> str:
    """环境变量可临时覆盖本机 TOML；两者都不进入报告或命令参数。"""
    return os.environ.get(DEEPSEEK_API_KEY_ENV) or _load_api_key_from_file(config_path)


async def run_python_judge_worker_baseline(
    *, mcp_url: str,
    api_key: str,
) -> tuple[InvestigationRun, EvaluationResult]:
    """以真实 MCP 证据运行 Python 判题 Worker 不可用场景，不触发任何写操作。"""
    scenario = PYTHON_JUDGE_WORKER_UNAVAILABLE
    client = OpenAICompatibleStructuredOutputClient(
        model=DEEPSEEK_MODEL,
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        response_format_mode="json_object",
    )
    workflow = InvestigationWorkflow(
        StreamableHttpToolGateway(mcp_url),
        LlmInvestigationPlanner(client, readonly_tool_names=scenario.allowed_tool_names),
        InvestigationBudget(
            readonly_tool_names=scenario.allowed_tool_names,
            max_tool_calls=scenario.max_tool_calls,
        ),
        LlmInvestigationDiagnostician(
            client,
            allowed_cause_codes=frozenset({scenario.expected_primary_cause_code}),
        ),
    )
    run = await workflow.run(scenario.incident)
    return run, InvestigationEvaluator().evaluate(scenario, run)


def _render_result(run: InvestigationRun, result: EvaluationResult) -> str:
    """输出可归档 JSON；不输出 API Key、MCP 配置或模型请求头。"""
    payload = {
        "model": DEEPSEEK_MODEL,
        "scenario_id": result.scenario_id,
        "stop_reason": run.stop_reason,
        "completed_tool_calls": [call.model_dump(mode="json") for call in run.completed_calls],
        "report": run.report.model_dump(mode="json"),
        "evaluation": asdict(result),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """只保留 MCP 地址这个运行时可变项，场景与模型固定以便基线可复现。"""
    parser = argparse.ArgumentParser(description="运行 HinataOps 的真实 DeepSeek 评测基线")
    parser.add_argument("--mcp-url", default=DEFAULT_MCP_URL, help="已启动 Ops MCP Server 的地址")
    parser.add_argument(
        "--llm-config",
        type=Path,
        default=DEFAULT_LLM_CONFIG_PATH,
        help="被 Git 忽略的本机 DeepSeek 配置文件路径",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="由 Python 以 UTF-8 写入的评测结果 JSON 文件路径",
    )
    return parser.parse_args(argv)


def _configure_utf8_output() -> None:
    """固定 CLI 的 UTF-8 编码，避免 Windows 本地代码页无法输出中文报告。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def _write_result(output: str, output_file: Path | None) -> None:
    """直接用 UTF-8 保存结果，避免 PowerShell 管道以本地代码页重编码。"""
    if output_file is not None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(f"{output}\n", encoding="utf-8")
    print(output)


def main(argv: Sequence[str] | None = None) -> None:
    """校验本机密钥后执行一次只读基线，并将结果打印到标准输出。"""
    _configure_utf8_output()
    args = _parse_args(argv)
    try:
        run, result = asyncio.run(
            run_python_judge_worker_baseline(
                mcp_url=args.mcp_url,
                api_key=_require_api_key(args.llm_config),
            )
        )
    except ValueError as error:
        raise SystemExit(f"无法启动真实 LLM 评测: {error}") from error
    _write_result(_render_result(run, result), args.output_file)


if __name__ == "__main__":
    main()
