from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from hinataops.ops_mcp.adapters.redis import RedisReadonlyAdapter
from hinataops.ops_mcp.contracts import ObservationMetadata, new_metadata
from hinataops.ops_mcp.toolsets.aoi_learn_judge.config import JudgeStreamConfig

JudgeLanguage = Literal["python", "sql"]


class ConsumerGroupSnapshot(BaseModel):
    """一个 Redis Consumer Group 的消费进度与未确认消息状态。"""

    name: str = Field(description="Consumer Group 名称")
    consumers: int = Field(description="当前登记的消费者数量")
    lag: int | None = Field(description="尚未投递给该 Group 的消息数；未知时为 null")
    pending: int = Field(description="已投递但尚未 ACK 的消息数")
    last_delivered_id: str | None = Field(description="该 Group 最近投递的 Stream 消息 ID")
    oldest_pending_id: str | None = Field(description="最早未确认消息 ID；无 pending 时为 null")


class RedisStreamSummary(BaseModel):
    """一个受审核判题 Stream 的有界运行状态证据。"""

    metadata: ObservationMetadata
    language: JudgeLanguage = Field(description="判题语言")
    stream_key: str = Field(description="受审核的 Redis Stream 键名")
    history_length: int = Field(description="Stream 保留的历史消息数，不等同于积压")
    consumer_group: ConsumerGroupSnapshot | None = Field(
        description="目标 Consumer Group 状态；Group 不存在时为 null"
    )


class RedisStreamInspector:
    """通过固定 Redis CLI 查询采集判题 Stream 的消费证据。

    Stream 和 Group 均来自本地配置。调用方只能选择 Python 或 SQL，不能把该类变成
    对任意 Redis 键执行命令的入口。
    """

    def __init__(
        self,
        environment_name: str,
        redis: RedisReadonlyAdapter,
        streams: list[JudgeStreamConfig],
    ) -> None:
        self._environment_name = environment_name
        self._redis = redis
        self._streams = {stream.language: stream for stream in streams}

    async def summary(self, language: JudgeLanguage) -> RedisStreamSummary:
        """采集一种语言的历史长度、Group lag 与 pending 状态。"""
        stream = self._stream(language)
        groups_raw = await self._redis.xinfo_groups(stream.stream_key)
        length_raw = await self._redis.xlen(stream.stream_key)

        groups = json.loads(groups_raw)
        group = next(
            (item for item in groups if item.get("name") == stream.consumer_group), None
        )
        warnings: list[str] = []
        consumer_group = None
        if group is None:
            # Group 缺失是目标系统的重要状态，不应被伪装成 Tool 的解析失败。
            warnings.append(f"未找到 Consumer Group: {stream.consumer_group}")
        else:
            # Redis 在 Group 不存在时会对 XPENDING 返回 NOGROUP。必须先判定 Group，
            # 才能把这一目标系统状态以完整观测结果而不是 Tool 失败返回给 Agent。
            pending_raw = await self._redis.xpending(stream.stream_key, stream.consumer_group)
            pending = json.loads(pending_raw)
            consumer_group = ConsumerGroupSnapshot(
                name=stream.consumer_group,
                consumers=int(group.get("consumers", 0)),
                lag=self._optional_int(group.get("lag")),
                pending=int(pending[0]),
                last_delivered_id=group.get("last-delivered-id"),
                oldest_pending_id=pending[1],
            )

        return RedisStreamSummary(
            metadata=new_metadata(
                self._environment_name, "redis", warnings=warnings
            ),
            language=language,
            stream_key=stream.stream_key,
            history_length=int(length_raw.strip()),
            consumer_group=consumer_group,
        )

    def _stream(self, language: JudgeLanguage) -> JudgeStreamConfig:
        """读取已配置语言映射，防止未配置语言进入远端命令。"""
        try:
            return self._streams[language]
        except KeyError as error:
            raise ValueError(f"Judge stream for language '{language}' is not configured") from error

    @staticmethod
    def _optional_int(value: object) -> int | None:
        """将 Redis 的 null 或数字值转换为稳定的 Python 表示。"""
        return int(value) if value is not None else None
