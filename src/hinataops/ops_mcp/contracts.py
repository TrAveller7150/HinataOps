from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field


ObservationSource = Literal["docker", "mysql", "redis", "prometheus", "topology"]
ObservationErrorKind = Literal[
    "configuration_error",
    "target_unavailable",
    "transport_error",
    "parse_error",
]


class ObservationError(BaseModel):
    """一次观测失败的结构化说明，不混入目标系统的业务结论。"""

    kind: ObservationErrorKind = Field(description="失败类别，用于决定是否适合重试")
    message: str = Field(description="面向操作者的脱敏错误说明")
    retryable: bool = Field(description="是否可能通过稍后重试恢复")


class ObservationMetadata(BaseModel):
    """每条基础设施证据共有的来源、时间与完整性元数据。"""

    environment: str = Field(description="被观测的目标环境名称")
    source: ObservationSource = Field(description="产生证据的数据源类别")
    observed_at: datetime = Field(description="HinataOps 完成采集的 UTC 时间")
    complete: bool = Field(description="结果是否完整反映本次预期查询")
    warnings: list[str] = Field(default_factory=list, description="不阻断结果的完整性提示")
    error: ObservationError | None = Field(default=None, description="采集失败时的结构化错误")


def new_metadata(
    environment: str,
    source: ObservationSource,
    *,
    complete: bool = True,
    warnings: list[str] | None = None,
    error: ObservationError | None = None,
) -> ObservationMetadata:
    """创建统一元数据，确保不同 Tool 的证据时间具有相同语义。"""
    return ObservationMetadata(
        environment=environment,
        source=source,
        observed_at=datetime.now(UTC),
        complete=complete,
        warnings=warnings or [],
        error=error,
    )
