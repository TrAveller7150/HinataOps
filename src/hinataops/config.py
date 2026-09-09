from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ServiceConfig(BaseModel):
    """HinataOps 被允许观测的单个服务的静态拓扑信息。

    容器名来自受审核的配置，而非 MCP Tool 参数，避免 Agent 将拓扑查询扩展为
    对任意 Docker 容器的检查请求。
    """

    name: str
    container: str
    depends_on: list[str] = Field(default_factory=list)
    role: str


class JudgeStreamConfig(BaseModel):
    """一种判题语言对应的受审核 Redis Stream 与 Worker 映射。

    将 Stream、Consumer Group 和 Prometheus 实例固定在环境配置中，避免 Tool 调用方
    传入任意 Redis 键名或监控目标。
    """

    language: Literal["python", "sql"]
    stream_key: str
    consumer_group: str
    judge_service: str
    prometheus_instance: str


class EnvironmentDetails(BaseModel):
    """一个目标环境受限 SSH 传输所需的连接信息。

    引用的私钥文件仅保留在本机，并由被 Git 忽略的环境配置提供，绝不能写入
    可提交的配置模板。
    """

    name: str
    host: str
    user: str
    identity_file: Path
    connect_timeout_seconds: int = Field(default=8, ge=1, le=60)
    command_timeout_seconds: int = Field(default=15, ge=1, le=60)


class EnvironmentConfig(BaseModel):
    """一个目标环境已校验的拓扑与连接配置。"""

    environment: EnvironmentDetails
    services: list[ServiceConfig]
    judge_streams: list[JudgeStreamConfig]

    def service(self, name: str) -> ServiceConfig:
        """按名称查找已配置服务；未找到时返回包含可选项的校验错误。"""
        for service in self.services:
            if service.name == name:
                return service
        available = ", ".join(item.name for item in self.services)
        raise ValueError(f"Unknown service '{name}'. Available services: {available}")

    def judge_stream(self, language: Literal["python", "sql"]) -> JudgeStreamConfig:
        """按语言查找已配置的判题 Stream；未配置时返回可行动的校验错误。"""
        for stream in self.judge_streams:
            if stream.language == language:
                return stream
        raise ValueError(f"Judge stream for language '{language}' is not configured")


def load_environment_config(path: Path | None = None) -> EnvironmentConfig:
    """加载显式指定或由 ``HINATAOPS_CONFIG`` 选定的本地环境配置。"""
    # 默认配置文件被刻意忽略，使同一份源码能保留脱敏模板，而每位开发者使用自己的私钥路径。
    configured_path = path or Path(
        os.environ.get("HINATAOPS_CONFIG", "config/environments/aoi-local.toml")
    )
    with configured_path.open("rb") as file:
        return EnvironmentConfig.model_validate(tomllib.load(file))
