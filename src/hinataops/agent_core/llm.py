"""OpenAI-compatible LLM Planner Adapter；不向模型暴露 MCP Client 或写操作能力。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, TypeVar
from uuid import UUID

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
            raise ValueError("Tool arguments_json 不是合法 JSON") from error
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments_json 必须是 JSON 对象")
        return ToolCall(name=self.name, arguments=arguments)


class ModelPlanningDecision(BaseModel):
    """模型 API 的严格输出契约，与内部 PlanningDecision 分离。"""

    model_config = ConfigDict(extra="forbid")

    tool_calls: list[ModelToolCall] = Field(
        ...,
        max_length=2,
        description="本轮需要执行的零到两个只读检查",
    )
    finish_reason_code: Literal["evidence_sufficient", "no_further_readonly_check"] | None = Field(
        ...,
        description=(
            "调查结束码（`finish_reason_code`）；仅可为 evidence_sufficient 或 "
            "no_further_readonly_check，选择工具时必须为 null"
        ),
    )
    decision_summary: str = Field(
        min_length=1,
        max_length=1_000,
        description="面向人工审计的简短决策摘要；说明已知事实、剩余不确定性及本轮选择，不输出内部思维链",
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
            decision_summary=self.decision_summary,
        )


OutputValue = TypeVar("OutputValue")


@dataclass(frozen=True)
class ValidatedStructuredOutput[OutputValue]:
    """一次受控结构化生成的结果与格式恢复痕迹。"""

    value: OutputValue
    attempts: int
    recovery_note: str | None = None


class StructuredOutputClient(Protocol):
    """LLM 结构化输出端口；供应商替换不影响调查图或领域契约。"""

    async def create_validated_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
        validate: Callable[[dict[str, object]], OutputValue],
    ) -> ValidatedStructuredOutput[OutputValue]: ...


class OpenAICompatibleStructuredOutputClient:
    """OpenAI Chat Completions 适配器，统一处理供应商能力差异与有限修复。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        response_format_mode: Literal["json_schema", "json_object", "prompted_json"] = "json_schema",
        max_tokens: int = 2_000,
        timeout_seconds: float = 45,
        max_output_attempts: int = 2,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens 必须大于 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if not 1 <= max_output_attempts <= 3:
            raise ValueError("max_output_attempts 必须在 1 到 3 之间")
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        self._response_format_mode = response_format_mode
        self._max_tokens = max_tokens
        self._max_output_attempts = max_output_attempts

    async def create_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
    ) -> dict[str, object]:
        """兼容旧调用方的 JSON 请求；新代码应优先提供语义校验函数。"""
        output = await self.create_validated_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_name=schema_name,
            schema=schema,
            json_example=json_example,
            validate=lambda value: value,
        )
        return output.value

    async def create_validated_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
        validate: Callable[[dict[str, object]], OutputValue],
    ) -> ValidatedStructuredOutput[OutputValue]:
        """在一个有界回路内完成请求、宽容解析与严格契约校验。"""
        formatted_system_prompt, response_format = self._request_contract(
            system_prompt=system_prompt,
            schema_name=schema_name,
            schema=schema,
            json_example=json_example,
        )
        required_top_level_fields = self._required_top_level_fields(schema)
        repair_issue: str | None = None
        recovery_notes: list[str] = []
        attempt_issues: list[str] = []
        for attempt in range(1, self._max_output_attempts + 1):
            prompt = formatted_system_prompt
            if repair_issue is not None:
                prompt += (
                    "\n上一响应未通过结构化输出契约校验，原因："
                    f"{repair_issue}。请基于原任务重新输出完整、合法的 JSON 对象，不要解释。"
                )
                if required_top_level_fields:
                    prompt += (
                        "顶层 JSON 对象必须包含以下字段："
                        f"{', '.join(required_top_level_fields)}。"
                        "不要只输出某个嵌套对象、候选根因或字段片段。"
                    )
            try:
                raw_value, parse_note = await self._request_json_once(
                    system_prompt=prompt,
                    user_prompt=user_prompt,
                    response_format=response_format,
                )
                value = validate(raw_value)
            except _StructuredOutputIssue as error:
                repair_issue = error.summary
            except (ValidationError, TypeError, ValueError) as error:
                repair_issue = _contract_error_summary(error)
            else:
                if parse_note is not None:
                    recovery_notes.append(parse_note)
                if attempt > 1:
                    recovery_notes.append(f"第 {attempt} 次输出通过结构化契约校验。")
                return ValidatedStructuredOutput(
                    value=value,
                    attempts=attempt,
                    recovery_note="；".join(recovery_notes) or None,
                )
            attempt_issues.append(f"第 {attempt} 次：{repair_issue}")
        raise PlannerError(
            "LLM 结构化输出在 "
            f"{self._max_output_attempts} 次尝试后仍不符合契约：{'；'.join(attempt_issues)}"
        )

    def _request_contract(
        self,
        *,
        system_prompt: str,
        schema_name: str,
        schema: dict[str, object],
        json_example: str,
    ) -> tuple[str, dict[str, object] | None]:
        """按供应商能力决定 API response_format 与提示词约束，业务 Schema 不变。"""
        formatted_system_prompt = system_prompt
        if self._response_format_mode == "json_schema":
            return formatted_system_prompt, {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }
        formatted_system_prompt += (
            "\n必须只返回 JSON 对象，不要 Markdown 或额外文字。"
            f"输出格式示例：{json_example}"
            "\n输出必须符合以下 JSON Schema："
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        return (
            formatted_system_prompt,
            {"type": "json_object"}
            if self._response_format_mode == "json_object"
            else None,
        )

    @staticmethod
    def _required_top_level_fields(schema: dict[str, object]) -> list[str]:
        """从本次 Schema 提取顶层必填字段，用于修复时防止模型只返回嵌套对象。"""
        required = schema.get("required")
        return (
            [field for field in required if isinstance(field, str)]
            if isinstance(required, list)
            else []
        )

    async def _request_json_once(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_format: dict[str, object] | None,
    ) -> tuple[dict[str, object], str | None]:
        """发起一次供应商调用，并以确定性规则解析单个 JSON 对象。"""
        request: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": self._max_tokens,
        }
        if response_format is not None:
            request["response_format"] = response_format
        try:
            response = await self._client.chat.completions.create(**request)
        except Exception as error:
            raise PlannerError(f"LLM 请求失败: {type(error).__name__}") from error
        if not response.choices:
            raise PlannerError("LLM 未返回候选结果")
        choice = response.choices[0]
        message = choice.message
        metadata = self._response_metadata(choice, message)
        if message.refusal:
            raise PlannerError("LLM 拒绝生成结构化输出")
        if message.content is None:
            raise _StructuredOutputIssue(f"未返回结构化内容（{metadata}）")
        if not message.content.strip():
            raise _StructuredOutputIssue(f"返回空内容（{metadata}）")
        try:
            return self._parse_json_object(message.content)
        except _StructuredOutputIssue as error:
            raise _StructuredOutputIssue(f"{error.summary}；{metadata}") from error

    @staticmethod
    def _response_metadata(choice: object, message: object) -> str:
        """提取供应商响应的非敏感诊断元数据，不记录模型正文或推理内容。"""
        finish_reason = getattr(choice, "finish_reason", None)
        safe_finish_reason = (
            finish_reason
            if finish_reason in {"stop", "length", "content_filter", "tool_calls"}
            else "unknown"
        )
        reasoning_content = getattr(message, "reasoning_content", None)
        reasoning_state = "present" if isinstance(reasoning_content, str) and reasoning_content else "absent"
        return f"finish_reason={safe_finish_reason}, reasoning_content={reasoning_state}"

    @staticmethod
    def _parse_json_object(content: str) -> tuple[dict[str, object], str | None]:
        """接受纯 JSON、单层围栏或正文中唯一 JSON 对象；绝不猜测多个对象。"""
        stripped = content.strip()
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1] == "```":
            stripped = "\n".join(lines[1:-1]).strip()
            note = "已移除供应商附加的 Markdown JSON 围栏。"
        else:
            note = None
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            candidates: list[object] = []
            position = 0
            while (start := stripped.find("{", position)) >= 0:
                try:
                    candidate, length = decoder.raw_decode(stripped[start:])
                except json.JSONDecodeError:
                    position = start + 1
                    continue
                candidates.append(candidate)
                position = start + length
            if len(candidates) != 1:
                raise _StructuredOutputIssue(
                    f"无法唯一提取 JSON 对象（响应字符数={len(content)}）"
                )
            value = candidates[0]
            note = "已从供应商附加说明中提取唯一 JSON 对象。"
        if not isinstance(value, dict):
            raise _StructuredOutputIssue("返回内容不是 JSON 对象")
        return value, note


class _StructuredOutputIssue(Exception):
    """可重试的词法或结构化输出问题；不携带模型原文。"""

    def __init__(self, summary: str) -> None:
        self.summary = summary
        super().__init__(summary)


class LlmInvestigationPlanner(InvestigationPlanner):
    """仅向模型提供当前事故、已采集证据和经筛选的只读 Tool Catalog。"""

    _system_prompt = """你是只读运维调查规划器。只依据已提供的观测证据选择下一步检查；证据充分时结束调查。
你没有调用工具、重启服务、运行命令或修改系统的权限。只能从本轮提供的允许只读工具清单中选择工具名称。
每轮最多选择两个 Tool，以便下一轮能根据新证据调整。必须提供 `decision_summary`：用一到三句中文说明已知事实、
仍缺少的证据，以及为何选择本轮 Tool 或结束；它是给人工审计的决策摘要，不要输出逐 token 思维链、根因判断或人工建议。
不得选择超过 `remaining_tool_calls` 的 Tool 数量；当其为 0 时必须结束调查。
`remaining_evidence_rounds` 为剩余可采证批次数；其为 0 时只能结束调查，不能继续选择 Tool。
结束时仅返回受限的 `finish_reason_code`。
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
        output = await self._client.create_validated_json(
            system_prompt=self._system_prompt,
            user_prompt=self._render_context(context, allowed_tools),
            schema_name="investigation_decision",
            schema=ModelPlanningDecision.model_json_schema(),
            json_example=(
                '{"tool_calls":[],"finish_reason_code":"evidence_sufficient",'
                '"decision_summary":"已完成必要采证，现有证据足以形成报告。"}'
            ),
            validate=lambda raw: self._validate_decision(raw, allowed_tools),
        )
        decision = output.value
        if output.recovery_note is not None:
            decision.compatibility_note = _merge_notes(
                decision.compatibility_note, output.recovery_note
            )
        return decision

    def _validate_decision(
        self,
        raw_decision: dict[str, object],
        allowed_tools: list[dict[str, object]],
    ) -> PlanningDecision:
        """严格验证模型决策；异常由结构化输出层作为受限修复依据。"""
        normalized, compatibility_note = self._normalize_planning_decision(raw_decision)
        decision = ModelPlanningDecision.model_validate(normalized).to_planning_decision()
        allowed_names = {tool["name"] for tool in allowed_tools}
        disallowed_names = {call.name for call in decision.tool_calls} - allowed_names
        if disallowed_names:
            raise ValueError("选择了未授权 Tool")
        decision.compatibility_note = compatibility_note
        return decision

    @staticmethod
    def _normalize_planning_decision(
        raw_decision: dict[str, object],
    ) -> tuple[dict[str, object], str | None]:
        """只兼容已知的非执行说明字段；Tool、参数和结束码仍由严格契约校验。"""
        normalized = dict(raw_decision)
        note = normalized.pop("decision_summary_note", None)
        if note is None:
            return normalized, None
        if "decision_summary" not in normalized and isinstance(note, str):
            normalized["decision_summary"] = note
        return normalized, "已忽略非执行兼容字段 decision_summary_note。"

    @staticmethod
    def _render_context(
        context: PlanningContext,
        allowed_tools: list[dict[str, object]],
    ) -> str:
        """使用确定性 JSON 组装最小上下文，避免混入凭证、完整日志或写 Tool。"""
        payload: Mapping[str, object] = {
            "incident": context.incident.model_dump(mode="json"),
            "investigation_round": context.investigation_round,
            "remaining_tool_calls": context.remaining_tool_calls,
            "remaining_evidence_rounds": context.remaining_evidence_rounds,
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


def _contract_error_summary(error: Exception) -> str:
    """为修复提示生成最小错误摘要，避免把模型原文写回下一轮上下文。"""
    if isinstance(error, ValidationError):
        return _validation_error_summary(error)
    if isinstance(error, ValueError):
        return str(error) or "语义约束不满足"
    return "类型不匹配"


def _merge_notes(*notes: str | None) -> str | None:
    """合并恢复痕迹，以便 CLI 和运行记录可审计但不影响执行。"""
    merged = [note for note in notes if note is not None]
    return "；".join(merged) or None


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
        minimum_confirmed_confidence: float = 0.7,
    ) -> None:
        if not 0 < minimum_confirmed_confidence <= 1:
            raise ValueError("minimum_confirmed_confidence 必须在 0 到 1 之间")
        self._client = client
        self._allowed_cause_codes = allowed_cause_codes
        self._minimum_confirmed_confidence = minimum_confirmed_confidence

    async def diagnose(self, context: DiagnosisContext) -> InvestigationReport:
        """请求严格模型输出，再由领域模型验证所有证据引用。"""
        try:
            output = await self._client.create_validated_json(
                system_prompt=self._render_system_prompt(),
                user_prompt=self._render_context(context),
                schema_name="investigation_report",
                schema=ModelDiagnosis.model_json_schema(),
                json_example=(
                    '{"status":"inconclusive","hypotheses":[],'
                    '"primary_hypothesis_index":null,"conclusion":"证据不足",'
                    '"recommended_action":null}'
                ),
                validate=self._validate_diagnosis,
            )
            diagnosis = output.value
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
            status, primary_hypothesis_id, conclusion = self._apply_confirmation_policy(
                diagnosis, hypotheses
            )
            return InvestigationReport(
                incident_id=context.incident.incident_id,
                status=status,
                observations=context.observations,
                hypotheses=hypotheses,
                primary_hypothesis_id=primary_hypothesis_id,
                conclusion=conclusion,
                recommended_action=diagnosis.recommended_action,
            )
        except PlannerError as error:
            raise DiagnosisError(f"LLM 诊断输出不可用：{error}") from error
        except (ValidationError, TypeError, ValueError) as error:
            raise DiagnosisError(f"LLM 诊断报告校验失败：{_contract_error_summary(error)}") from error

    def _validate_diagnosis(self, raw_diagnosis: dict[str, object]) -> ModelDiagnosis:
        """诊断语义和根因码同属模型输出契约，可在有限回路内修复。"""
        diagnosis = ModelDiagnosis.model_validate(raw_diagnosis)
        self._validate_cause_codes(diagnosis)
        return diagnosis

    def _render_system_prompt(self) -> str:
        """仅在评测或领域 Profile 给出码表时约束模型的稳定根因码。"""
        confirmation_rule = (
            "只有主假设置信度不低于 "
            f"{self._minimum_confirmed_confidence:.0%} 且证据能直接支持该根因时才能返回 `diagnosed`；"
            "否则必须返回 `inconclusive`，但仍可保留候选假设。"
        )
        if not self._allowed_cause_codes:
            return f"{self._system_prompt}\n{confirmation_rule}"
        codes = "、".join(sorted(self._allowed_cause_codes))
        return (
            f"{self._system_prompt}\n{confirmation_rule}"
            f"\n本次只允许使用以下 `cause_code`：{codes}。"
        )

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

    def _apply_confirmation_policy(
        self, diagnosis: ModelDiagnosis, hypotheses: list[Hypothesis]
    ) -> tuple[Literal["diagnosed", "inconclusive"], UUID | None, str]:
        """低置信候选保留给人工复核，但不能被渲染为已确认根因。"""
        if diagnosis.status != "diagnosed":
            return "inconclusive", None, diagnosis.conclusion
        assert diagnosis.primary_hypothesis_index is not None
        primary = hypotheses[diagnosis.primary_hypothesis_index]
        if primary.confidence >= self._minimum_confirmed_confidence:
            return "diagnosed", primary.hypothesis_id, diagnosis.conclusion
        return (
            "inconclusive",
            None,
            "主假设置信度 "
            f"{primary.confidence:.0%}，未达到 {self._minimum_confirmed_confidence:.0%} 的确认阈值；"
            f"当前仅保留为待人工核实的候选。{diagnosis.conclusion}",
        )

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
