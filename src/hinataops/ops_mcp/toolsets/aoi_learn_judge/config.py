"""AoiLearn 判题 Plugin 的专属配置模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class JudgeStreamConfig(BaseModel):
    """一种判题语言对应的受审核 Redis Stream 与 Worker 映射。"""

    # 该判题流水线处理的题目语言。
    language: Literal["python", "sql"]
    # 该语言判题任务所在的受审核 Redis Stream 键名。
    stream_key: str = Field(pattern=r"^[A-Za-z0-9:_-]+$")
    # 消费该 Stream 的 Judge Worker Consumer Group 名称。
    consumer_group: str = Field(pattern=r"^[A-Za-z0-9:_-]+$")
    # 拓扑中对应 Judge Worker 的逻辑服务名称。
    judge_service: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    # 承载该 Stream 的 Core 基础设施实例 ID。
    redis_instance_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    # Prometheus 中该 Judge Worker 的 instance 标签值。
    prometheus_target: str = Field(pattern=r"^[A-Za-z0-9:.-]+$")


class AoiJudgeToolsetSettings(BaseModel):
    """AoiLearn 判题 Toolset 所依赖的共享实例和领域映射。"""

    docker_instance_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    mysql_instance_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    prometheus_instance_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    # 按语言定义的判题 Stream、Worker 与监控目标映射。
    judge_streams: list[JudgeStreamConfig] = Field(min_length=1)
