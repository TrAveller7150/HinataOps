"""运行真实 LLM 故障评测的显式命令行入口，不负责启动 MCP Server。"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Sequence

from hinataops.agent_core.evaluation import EvaluationResult, InvestigationEvaluator
from hinataops.agent_core.events import InvestigationEventListener
from hinataops.agent_core.llm import OpenAICompatibleStructuredOutputClient
from hinataops.agent_core.workflow import InvestigationRun
from hinataops.investigation_service import run_readonly_investigation
from hinataops.llm_config import LlmConnectionConfig, load_llm_connection
from hinataops.ops_mcp.toolsets.aoi_learn_judge.evaluation import (
    PYTHON_JUDGE_WORKER_UNAVAILABLE,
)

DEEPSEEK_API_KEY_ENV = "HINATAOPS_DEEPSEEK_API_KEY"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_LLM_CONFIG_PATH = Path("config/environments/llm.local.toml")


def _load_api_key_from_file(config_path: Path) -> str:
    """保留旧测试入口；通用配置解析逻辑已迁至 llm_config。"""
    return load_llm_connection(config_path).api_key


def _require_api_key(config_path: Path) -> str:
    """保留旧调用方入口；新代码应使用完整连接配置。"""
    return _require_llm_connection(config_path).api_key


def _require_llm_connection(config_path: Path) -> LlmConnectionConfig:
    """读取模型、地址、能力模式与密钥；密钥不会写入归档。"""
    return load_llm_connection(config_path)


async def run_python_judge_worker_baseline(
    *, mcp_url: str,
    connection: LlmConnectionConfig,
    event_listener: InvestigationEventListener | None = None,
) -> tuple[InvestigationRun, EvaluationResult]:
    """以真实 MCP 证据运行 Python 判题 Worker 不可用场景，不触发任何写操作。"""
    scenario = PYTHON_JUDGE_WORKER_UNAVAILABLE
    client = OpenAICompatibleStructuredOutputClient(
        model=connection.model,
        api_key=connection.api_key,
        base_url=connection.base_url,
        response_format_mode=connection.response_format_mode,
        max_tokens=connection.max_tokens,
    )
    run = await run_readonly_investigation(
        mcp_url=mcp_url,
        client=client,
        incident=scenario.incident,
        readonly_tool_names=scenario.allowed_tool_names,
        max_tool_calls=scenario.max_tool_calls,
        event_listener=event_listener,
        allowed_cause_codes=frozenset({scenario.expected_primary_cause_code}),
    )
    return run, InvestigationEvaluator().evaluate(scenario, run)


def _render_result(run: InvestigationRun, result: EvaluationResult, *, model: str) -> str:
    """输出可归档 JSON；不输出 API Key、MCP 配置或模型请求头。"""
    payload = {
        "model": model,
        "scenario_id": result.scenario_id,
        "stop_reason": run.stop_reason,
        "completed_tool_calls": [call.model_dump(mode="json") for call in run.completed_calls],
        "retry_attempts": [item.model_dump(mode="json") for item in run.retry_attempts],
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
        connection = _require_llm_connection(args.llm_config)
        run, result = asyncio.run(
            run_python_judge_worker_baseline(
                mcp_url=args.mcp_url,
                connection=connection,
            )
        )
    except ValueError as error:
        raise SystemExit(f"无法启动真实 LLM 评测: {error}") from error
    _write_result(_render_result(run, result, model=connection.model), args.output_file)


if __name__ == "__main__":
    main()
