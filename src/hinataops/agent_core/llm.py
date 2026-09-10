"""OpenAI-compatible LLM Planner Adapter；不向模型暴露 MCP Client 或写操作能力。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hinataops.agent_core.diagnosis import (
    DiagnosisContext,
    DiagnosisError,
    InvestigationDiagnostician,
)
from hinataops.agent_core.models import Hypothesis, InvestigationReport, ToolCall
from hinataops.agent_core.planner import (
    InvestigationPlanner,
    PlannerError,
    PlanningContext,
    PlanningDecision,
)


class ModelToolCall(BaseModel):
    """LLM 输出的一次 Tool 选择；参数先以 JSON 字符串传输以满足严格 Schema。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="必须从本轮允许 Tool 清单中选择的 Tool 名称")
    arguments_json: str = Field(
        description="该 Tool 的参数 JSON 对象；无参数时必须为 '{}'"
    )

    def to_tool_call(self) -> ToolCall:
        """将模型字符串参数解析为 Core 使用的稳定 ToolCall。"""
        try:
            arguments = json.loads(self.arguments_json)
        except json.JSONDecodeError as error:
            raise PlannerError("Tool arguments_json 不是合法 JSON") from error
        if not isinstance(arguments, dict):
            raise PlannerError("Tool arguments_json 必须是 JSON 对象")
        return ToolCall(name=self.name, arguments=arguments)


class ModelPlanningDecision(BaseModel):
    """模型 API 的严格输出契约，与内部 PlanningDecision 分离。"""

    model_config = ConfigDict(extra="forbid")

    tool_calls: list[ModelToolCall] = Field(
        ...,
        max_length=4,
        description="本轮需要执行的零到四个只读检查",
    )
    finish_reason: str | None = Field(
        ...,
        min_length=1,
        max_length=1_000,
        description="不再需要检查时的结束依据；选择 Tool 时必须为 null",
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ModelPlanningDecision":
        """与内部决策保持相同的结束互斥语义。"""
        if not self.tool_calls and self.finish_reason is None:
            raise ValueError("未选择 Tool 时必须提供 finish_reason")
        if self.tool_calls and self.finish_reason is not None:
            raise ValueError("选择 Tool 的调查轮次不能同时结束")
        return self

    def to_planning_decision(self) -> PlanningDecision:
        """完成参数解析后，再进入既有领域决策契约。"""
        return PlanningDecision(
            tool_calls=[call.to_tool_call() for call in self.tool_calls],
            finish_reason=self.finish_reason,
        )


class StructuredOutputClient(Protocol):
    """LLM 传输端口；替换供应商时不影响调查图和 Planner 逻辑。"""

    async def create_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]: ...


class OpenAICompatibleStructuredOutputClient:
    """使用 Chat Completions JSON Schema 输出的 OpenAI-compatible 传输实现。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def create_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """请求严格 JSON Schema；网络和模型错误统一转换为 PlannerError。"""
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
                temperature=0,
            )
        except Exception as error:
            raise PlannerError(f"LLM 请求失败: {type(error).__name__}") from error
        if not response.choices:
            raise PlannerError("LLM 未返回候选结果")
        message = response.choices[0].message
        if message.refusal:
            raise PlannerError("LLM 拒绝生成调查决策")
        if message.content is None:
            raise PlannerError("LLM 未返回结构化内容")
        try:
            value = json.loads(message.content)
        except json.JSONDecodeError as error:
            raise PlannerError("LLM 返回内容不是 JSON") from error
        if not isinstance(value, dict):
            raise PlannerError("LLM 返回内容必须是 JSON 对象")
        return value


class LlmInvestigationPlanner(InvestigationPlanner):
    """仅向模型提供当前事故、已采集证据和经筛选的只读 Tool Catalog。"""

    _system_prompt = """你是只读运维调查的 Planner。只根据提供的证据选择下一步检查，或在证据充分时结束。
你没有执行 Tool、重启服务、运行命令或修改任何系统的权限。只能选择本轮允许 Tool 清单中的名称，
并严格遵循输出 Schema。arguments_json 必须是 JSON 对象字符串；无参数时使用 '{}'."""

    def __init__(
        self,
        client: StructuredOutputClient,
        *,
        readonly_tool_names: frozenset[str],
    ) -> None:
        self._client = client
        self._readonly_tool_names = readonly_tool_names

    async def decide(self, context: PlanningContext) -> PlanningDecision:
        """调用模型并在进入工作流前验证名称、JSON 参数和领域决策约束。"""
        allowed_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in context.catalog.tools
            if tool.name in self._readonly_tool_names
        ]
        try:
            raw_decision = await self._client.create_json(
                system_prompt=self._system_prompt,
                user_prompt=self._render_context(context, allowed_tools),
                schema_name="investigation_decision",
                schema=ModelPlanningDecision.model_json_schema(),
            )
            decision = ModelPlanningDecision.model_validate(raw_decision).to_planning_decision()
        except (ValidationError, TypeError) as error:
            raise PlannerError("LLM 结构化输出不符合调查决策契约") from error
        allowed_names = {tool["name"] for tool in allowed_tools}
        disallowed_names = {call.name for call in decision.tool_calls} - allowed_names
        if disallowed_names:
            raise PlannerError(f"LLM 选择了未授权 Tool: {sorted(disallowed_names)}")
        return decision

    @staticmethod
    def _render_context(
        context: PlanningContext,
        allowed_tools: list[dict[str, object]],
    ) -> str:
        """使用确定性 JSON 组装最小上下文，避免混入凭证、完整日志或写 Tool。"""
        payload: Mapping[str, object] = {
            "incident": context.incident.model_dump(mode="json"),
            "investigation_round": context.investigation_round,
            "completed_calls": [call.model_dump(mode="json") for call in context.completed_calls],
            "observations": [
                observation.model_dump(mode="json") for observation in context.observations
            ],
            "allowed_readonly_tools": allowed_tools,
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ModelHypothesis(BaseModel):
    """LLM 输出的候选根因；Evidence ID 必须来自本次调查上下文。"""

    model_config = ConfigDict(extra="forbid")

    cause: str = Field(min_length=1, max_length=1_000, description="候选根因的简明表述")
    confidence: float = Field(ge=0, le=1, description="基于当前证据的置信度")
    supporting_evidence_ids: list[str] = Field(
        description="支持该根因的已有 Evidence ID 字符串"
    )
    contradicting_evidence_ids: list[str] = Field(
        description="反驳该根因的已有 Evidence ID 字符串"
    )
    missing_evidence: list[str] = Field(description="仍需人工确认或补充的事实")


class ModelDiagnosis(BaseModel):
    """模型 API 的严格诊断输出契约；内部 Report 仍由 Core 构造。"""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="只能为 diagnosed 或 inconclusive")
    hypotheses: list[ModelHypothesis] = Field(
        max_length=3, description="最多三个候选根因"
    )
    primary_hypothesis_index: int | None = Field(
        description="主根因在 hypotheses 中的下标；inconclusive 时为 null"
    )
    conclusion: str = Field(min_length=1, max_length=4_000, description="面向人工的诊断结论")
    recommended_action: str | None = Field(
        description="只给出建议，不形成或执行操作计划；无建议时为 null"
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ModelDiagnosis":
        """固定诊断状态语义，防止模型把中间状态伪装为最终报告。"""
        if self.status not in {"diagnosed", "inconclusive"}:
            raise ValueError("status 只能为 diagnosed 或 inconclusive")
        if self.status == "diagnosed":
            if self.primary_hypothesis_index is None:
                raise ValueError("diagnosed 报告必须指定 primary_hypothesis_index")
            if not 0 <= self.primary_hypothesis_index < len(self.hypotheses):
                raise ValueError("primary_hypothesis_index 超出 hypotheses 范围")
        elif self.primary_hypothesis_index is not None:
            raise ValueError("inconclusive 报告不能指定 primary_hypothesis_index")
        return self


class LlmInvestigationDiagnostician(InvestigationDiagnostician):
    """基于已结束调查生成报告；模型不能调用 Tool 或绕过 Evidence ID 校验。"""

    _system_prompt = """你是运维调查的诊断器。只能依据提供的 Observation 形成根因假设和报告。
所有 supporting_evidence_ids 与 contradicting_evidence_ids 必须逐字引用上下文中已有的 Evidence ID。
证据不足时必须返回 inconclusive；不得虚构根因、日志、指标或执行任何操作。
recommended_action 只能是人工建议，不能声称已执行重启、扩容或其他写操作。"""

    def __init__(self, client: StructuredOutputClient) -> None:
        self._client = client

    async def diagnose(self, context: DiagnosisContext) -> InvestigationReport:
        """请求严格模型输出，再由领域模型验证所有证据引用。"""
        try:
            raw_diagnosis = await self._client.create_json(
                system_prompt=self._system_prompt,
                user_prompt=self._render_context(context),
                schema_name="investigation_report",
                schema=ModelDiagnosis.model_json_schema(),
            )
            diagnosis = ModelDiagnosis.model_validate(raw_diagnosis)
            hypotheses = [
                Hypothesis(
                    cause=hypothesis.cause,
                    confidence=hypothesis.confidence,
                    supporting_evidence_ids=hypothesis.supporting_evidence_ids,
                    contradicting_evidence_ids=hypothesis.contradicting_evidence_ids,
                    missing_evidence=hypothesis.missing_evidence,
                )
                for hypothesis in diagnosis.hypotheses
            ]
            primary_hypothesis_id = (
                hypotheses[diagnosis.primary_hypothesis_index].hypothesis_id
                if diagnosis.primary_hypothesis_index is not None
                else None
            )
            return InvestigationReport(
                incident_id=context.incident.incident_id,
                status=diagnosis.status,
                observations=context.observations,
                hypotheses=hypotheses,
                primary_hypothesis_id=primary_hypothesis_id,
                conclusion=diagnosis.conclusion,
                recommended_action=diagnosis.recommended_action,
            )
        except (PlannerError, ValidationError, TypeError, ValueError) as error:
            raise DiagnosisError("LLM 结构化输出不符合诊断报告契约") from error

    @staticmethod
    def _render_context(context: DiagnosisContext) -> str:
        """仅传入本次调查证据与停止原因，不传输 Catalog、密钥或执行入口。"""
        payload: Mapping[str, object] = {
            "incident": context.incident.model_dump(mode="json"),
            "stop_reason": context.stop_reason,
            "completed_calls": [call.model_dump(mode="json") for call in context.completed_calls],
            "observations": [
                observation.model_dump(mode="json") for observation in context.observations
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
