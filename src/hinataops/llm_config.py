"""本机 LLM 连接配置；不让供应商参数渗入调查编排层。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Literal

ResponseFormatMode = Literal["json_schema", "json_object", "prompted_json"]

_VALID_RESPONSE_FORMAT_MODES = frozenset({"json_schema", "json_object", "prompted_json"})
_DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
_DEFAULT_MAX_TOKENS = 8_192


@dataclass(frozen=True)
class LlmConnectionConfig:
    """一个 OpenAI-compatible 供应商连接及其结构化输出能力。"""

    provider: str
    model: str
    base_url: str
    api_key: str
    response_format_mode: ResponseFormatMode
    max_tokens: int


def load_llm_connection(config_path: Path) -> LlmConnectionConfig:
    """读取 `[llm]` 通用配置，并兼容旧版仅含 `[deepseek]` 密钥的本机文件。"""
    try:
        with config_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as error:
        raise ValueError(f"未找到 LLM 本机配置文件: {config_path}") from error
    llm = data.get("llm")
    if isinstance(llm, dict):
        return _parse_generic_llm_config(config_path, llm)
    deepseek = data.get("deepseek")
    if not isinstance(deepseek, dict):
        raise ValueError(f"{config_path} 缺少 [llm] 或兼容的 [deepseek] 配置")
    api_key = _required_text(config_path, deepseek, "api_key")
    return LlmConnectionConfig(
        provider="deepseek",
        model=_DEFAULT_DEEPSEEK_MODEL,
        base_url=_DEFAULT_DEEPSEEK_BASE_URL,
        api_key=os.environ.get("HINATAOPS_LLM_API_KEY")
        or os.environ.get("HINATAOPS_DEEPSEEK_API_KEY")
        or api_key,
        response_format_mode="json_object",
        max_tokens=_DEFAULT_MAX_TOKENS,
    )


def _parse_generic_llm_config(
    config_path: Path, llm: dict[str, object]
) -> LlmConnectionConfig:
    """校验通用配置；密钥可由统一环境变量覆盖而不进入命令历史。"""
    mode = _required_text(config_path, llm, "response_format_mode")
    if mode not in _VALID_RESPONSE_FORMAT_MODES:
        raise ValueError(f"{config_path} 的 response_format_mode 不受支持: {mode}")
    return LlmConnectionConfig(
        provider=_required_text(config_path, llm, "provider"),
        model=_required_text(config_path, llm, "model"),
        base_url=_required_text(config_path, llm, "base_url"),
        api_key=os.environ.get("HINATAOPS_LLM_API_KEY")
        or _required_text(config_path, llm, "api_key"),
        response_format_mode=mode,  # type: ignore[arg-type]
        max_tokens=_optional_positive_int(config_path, llm, "max_tokens", _DEFAULT_MAX_TOKENS),
    )


def _required_text(config_path: Path, values: dict[str, object], field: str) -> str:
    """读取一个非空字符串字段，避免把不完整供应商配置延迟到运行期。"""
    value = values.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{config_path} 的 {field} 必须是非空字符串")
    return value.strip()


def _optional_positive_int(
    config_path: Path, values: dict[str, object], field: str, default: int
) -> int:
    """读取可选正整数，避免无效输出预算在真实请求后才暴露。"""
    value = values.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{config_path} 的 {field} 必须是正整数")
    return value
