"""调查 Agent 在 LLM、MCP Client 与报告层之间共享的稳定领域契约。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator


Reliability = Literal["complete", "partial", "failed"]
InvestigationStatus = Literal["investigating", "diagnosed", "inconclusive"]


class IncidentRequest(BaseModel):
    """一次调查的不可变入口，未包含任何基础设施凭证或自由执行命令。"""

    incident_id: UUID = Field(default_factory=uuid4)
    query: str = Field(min_length=1, max_length=4_000)
    target_environment: str = Field(min_length=1, max_length=128)


class ToolCall(BaseModel):
    """Planner 建议的一次 MCP Tool 调用，执行前仍须经过确定性预算策略。"""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,127}$")
    arguments: dict[str, object] = Field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """以稳定 JSON 表示一次调用，供策略层阻止同参重复采集。"""
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True, separators=(',', ':'))}"


class Observation(BaseModel):
    """一次完成的 MCP 调用产生的原始事实与可读摘要；不在此处推断根因。"""

    evidence_id: UUID = Field(default_factory=uuid4)
    source: str = Field(min_length=1, max_length=64)
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object]
    observed_at: datetime
    value: dict[str, object]
    summary: str = Field(min_length=1, max_length=2_000)
    reliability: Reliability


class RetryAttempt(BaseModel):
    """一次受预算的瞬时采证重试，保留原因、次数与退避时间供事后审查。"""

    call: ToolCall
    attempt: int = Field(ge=2, le=2)
    reason: str = Field(min_length=1, max_length=128)
    backoff_seconds: float = Field(gt=0, le=10)


class Hypothesis(BaseModel):
    """一个候选根因及其支持、反驳和待补充证据，置信度不是统计概率。"""

    hypothesis_id: UUID = Field(default_factory=uuid4)
    cause_code: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{1,127}$"
    )
    cause: str = Field(min_length=1, max_length=1_000)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[UUID] = Field(default_factory=list)
    contradicting_evidence_ids: list[UUID] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_roles(self) -> "Hypothesis":
        """同一证据不能同时被标为支持和反驳，避免报告自相矛盾。"""
        overlap = set(self.supporting_evidence_ids) & set(self.contradicting_evidence_ids)
        if overlap:
            raise ValueError("同一 Evidence ID 不能同时作为支持和反驳证据")
        return self


class InvestigationReport(BaseModel):
    """调查结束后的可审查报告；所有根因结论必须可追溯到已采集证据。"""

    incident_id: UUID
    status: InvestigationStatus
    observations: list[Observation]
    hypotheses: list[Hypothesis] = Field(max_length=3)
    primary_hypothesis_id: UUID | None = None
    conclusion: str = Field(min_length=1, max_length=4_000)
    recommended_action: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> "InvestigationReport":
        """报告只能引用本次调查实际采集到的 Evidence，不能由模型虚构引用。"""
        evidence_ids = {observation.evidence_id for observation in self.observations}
        hypothesis_ids = {hypothesis.hypothesis_id for hypothesis in self.hypotheses}
        if len(hypothesis_ids) != len(self.hypotheses):
            raise ValueError("Hypothesis ID 不能重复")
        if self.status == "diagnosed" and self.primary_hypothesis_id is None:
            raise ValueError("诊断结论必须指定 primary_hypothesis_id")
        if self.primary_hypothesis_id is not None and self.primary_hypothesis_id not in hypothesis_ids:
            raise ValueError("primary_hypothesis_id 必须属于报告中的 Hypothesis")
        for hypothesis in self.hypotheses:
            referenced = set(hypothesis.supporting_evidence_ids) | set(
                hypothesis.contradicting_evidence_ids
            )
            if missing := referenced - evidence_ids:
                raise ValueError(f"Hypothesis 引用了未收集的 Evidence ID: {sorted(map(str, missing))}")
        return self
