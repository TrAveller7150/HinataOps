"""调查过程向展示层发布的轻量事件契约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from hinataops.agent_core.models import Observation, ToolCall


@dataclass(frozen=True)
class InvestigationEvent:
    """一次调查阶段变化；只提供展示信息，不授予外层干预工作流的能力。"""

    kind: str
    calls: tuple[ToolCall, ...] = ()
    observation: Observation | None = None
    detail: str | None = None


InvestigationEventListener = Callable[[InvestigationEvent], None]
