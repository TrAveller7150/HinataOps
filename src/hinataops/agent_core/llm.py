"""OpenAI-compatible LLM Planner Adapter；不向模型暴露 MCP Client 或写操作能力。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal, Protocol

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

    name: str = Field(description="工具名称（`name`）；必须从本轮允许的只读工具清单中选择")
    arguments_json: str = Field(
        description="工具参数 JSON 对象（`arguments_json`）；无参数时必须为 '{}'"
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
    finish_reason_code: Literal["evidence_sufficient", "no_further_readonly_check"] | None = Field(
        ...,
        description=(
            "调查结束码（`finish_reason_code`）；仅可为 evidence_sufficient 或 "
            "no_further_readonly_check，选择工具时必须为 null"
        ),
    )

    @model_validator(mode="after")
    def validate_decision(self) -> "ModelPlanningDecision":
        """与内部决策保持相同的结束互斥语义。"""
        if not self.tool_calls and self.finish_reason_code is None:
            raise ValueError("未选择 Tool 时必须提供 finish_reason_code")
        if self.tool_calls and self.finish_reason_code is not None:
            raise ValueError("选择 Tool 的调查轮次不能同时结束")
        return self

    def to_planning_decision(self) -> PlanningDecision:
        """完成参数解析后，再进入既有领域决策契约。"""
        return PlanningDecision(
            tool_calls=[call.to_tool_call() for call in self.tool_calls],
            finish_reason=(
                {
                    "evidence_sufficient": "模型认为现有证据已足够，结束调查。",
                    "no_further_readonly_check": "模型未找到适用的后续只读检查，结束调查。",
                }.get(self.finish_reason_code)
                if self.finish_reason_code is not None
                else None
            ),
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
        json_example: str,
    ) -> dict[str, object]: ...


class OpenAICompatibleStructuredOutputClient:
    """适配 Chat Completions 的结构化输出，支持严格 Schema 与 JSON Object 模式。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        response_format_mode: Literal["json_schema", "json_object"] = "json_schema",
        max_tokens: int = 2_000,
        timeout_seconds: float = 45,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens 必须大于 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._response_format_mode = response_format_mode
        self._max_tokens = max_tokens

    async def create_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
    ) -> dict[str, object]:
        """请求 JSON 输出；严格 Schema 不可用时仍由 Core 执行 Pydantic 校验。"""
        response_format: dict[str, object]
        formatted_system_prompt = system_prompt
        if self._response_format_mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
            formatted_system_prompt += (
                "\n必须只返回 JSON 对象，不要 Markdown 或额外文字。"
                f"输出格式示例：{json_example}"
                "\n输出必须符合以下 JSON Schema："
                f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
            )
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": formatted_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
                temperature=0,
                max_tokens=self._max_tokens,
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

    _system_prompt = """你是只读运维调查规划器。只依据已提供的观测证据选择下一步检查；证据充分时结束调查。
你没有调用工具、重启服务、运行命令或修改系统的权限。只能从本轮提供的允许只读工具清单中选择工具名称。
结束时仅返回受限的 `finish_reason_code`，不要在规划结果中输出根因判断、推测或人工建议。
工具参数字段 `arguments_json` 必须是 JSON 对象字符串；无参数时使用 '{}'."""

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
                json_example='{"tool_calls":[],"finish_reason_code":"evidence_sufficient"}',
            )
            decision = ModelPlanningDecision.model_validate(raw_decision).to_planning_decision()
        except ValidationError as error:
            raise PlannerError(
                f"LLM 结构化输出不符合调查决策契约: {_validation_error_summary(error)}"
            ) from error
        except TypeError as error:
            raise PlannerError("LLM 结构化输出不符合调查决策契约: 类型不匹配") from error
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


def _validation_error_summary(error: ValidationError) -> str:
    """仅保留字段位置和校验类别，避免调试信息回显完整模型输出。"""
    details = []
    for item in error.errors(include_url=False):
        location = ".".join(str(part) for part in item["loc"]) or "根对象"
        details.append(f"{location}: {item['type']}")
    return "；".join(details)


class ModelHypothesis(BaseModel):
    """LLM 输出的候选根因；Evidence ID 必须来自本次调查上下文。"""

    model_config = ConfigDict(extra="forbid")

    cause_code: str | None = Field(
        description="稳定根因码（`cause_code`）；由领域场景定义，未知时为 null"
    )
    cause: str = Field(
        min_length=1, max_length=1_000, description="候选根因的简明中文表述"
    )
    confidence: float = Field(ge=0, le=1, description="基于当前证据的置信度")
    supporting_evidence_ids: list[str] = Field(
        description="支持该根因的证据 ID（`supporting_evidence_ids`），必须来自已有观测证据"
    )
    contradicting_evidence_ids: list[str] = Field(
        description="反驳该根因的证据 ID（`contradicting_evidence_ids`），必须来自已有观测证据"
    )
    missing_evidence: list[str] = Field(description="仍需人工确认或补充的中文事实说明")


class ModelDiagnosis(BaseModel):
    """模型 API 的严格诊断输出契约；内部 Report 仍由 Core 构造。"""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="机器状态值，只能为 diagnosed 或 inconclusive")
    hypotheses: list[ModelHypothesis] = Field(
        max_length=3, description="最多三个候选根因"
    )
    primary_hypothesis_index: int | None = Field(
        description="主根因在假设列表（`hypotheses`）中的下标；inconclusive 时为 null"
    )
    conclusion: str = Field(
        min_length=1, max_length=4_000, description="面向人工的简体中文诊断结论"
    )
    recommended_action: str | None = Field(
        description="面向人工的简体中文建议；只给出建议，不形成或执行操作计划；无建议时为 null"
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

    _system_prompt = """你是运维调查诊断器。只能依据本次提供的观测证据（`Observation`）形成根因假设和诊断报告。
支持证据 ID（`supporting_evidence_ids`）与反驳证据 ID（`contradicting_evidence_ids`）必须逐字引用上下文中已有的证据 ID。
证据不足时必须返回 `inconclusive`；不得虚构根因、日志、指标，也不得执行任何操作。
根因、待补充事实、结论和人工建议必须使用简体中文。`cause_code` 是稳定英文下划线根因码；未知时为 null，不能翻译。
`recommended_action` 只能是人工建议，不能声称已执行重启、扩容或其他写操作。"""

    def __init__(
        self,
        client: StructuredOutputClient,
        *,
        allowed_cause_codes: frozenset[str] = frozenset(),
    ) -> None:
        self._client = client
        self._allowed_cause_codes = allowed_cause_codes

    async def diagnose(self, context: DiagnosisContext) -> InvestigationReport:
        """请求严格模型输出，再由领域模型验证所有证据引用。"""
        try:
            raw_diagnosis = await self._client.create_json(
                system_prompt=self._render_system_prompt(),
                user_prompt=self._render_context(context),
                schema_name="investigation_report",
                schema=ModelDiagnosis.model_json_schema(),
                json_example=(
                    '{"status":"inconclusive","hypotheses":[],'
                    '"primary_hypothesis_index":null,"conclusion":"证据不足",'
                    '"recommended_action":null}'
                ),
            )
            diagnosis = ModelDiagnosis.model_validate(raw_diagnosis)
            self._validate_cause_codes(diagnosis)
            hypotheses = [
                Hypothesis(
                    cause_code=hypothesis.cause_code,
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

    def _render_system_prompt(self) -> str:
        """仅在评测或领域 Profile 给出码表时约束模型的稳定根因码。"""
        if not self._allowed_cause_codes:
            return self._system_prompt
        codes = "、".join(sorted(self._allowed_cause_codes))
        return f"{self._system_prompt}\n本次只允许使用以下 `cause_code`：{codes}。"

    def _validate_cause_codes(self, diagnosis: ModelDiagnosis) -> None:
        """诊断结论必须有根因码；评测码表存在时拒绝模型自造的枚举值。"""
        if diagnosis.status != "diagnosed":
            return
        assert diagnosis.primary_hypothesis_index is not None
        primary = diagnosis.hypotheses[diagnosis.primary_hypothesis_index]
        if primary.cause_code is None:
            raise ValueError("diagnosed 报告的主根因必须提供 cause_code")
        if self._allowed_cause_codes and any(
            item.cause_code not in self._allowed_cause_codes
            for item in diagnosis.hypotheses
            if item.cause_code is not None
        ):
            raise ValueError("LLM 返回了未声明的 cause_code")

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
