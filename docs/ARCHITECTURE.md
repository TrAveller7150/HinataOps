# HinataOps 当前实现架构 V2

> 本文是 V2 当前实现快照，不再作为后续目标架构。新的架构决策见
> [V3 目标架构](ARCHITECTURE_V3.md)，实施顺序见 [V3 迁移清单](V3_MIGRATION_CHECKLIST.md)。

## 1. 定位

HinataOps 的最终产品目标是一个面向生产环境的通用 SRE 调查平台。它应能接收来自不同系统的事故，连接
指标、日志、追踪、容器、云平台、数据库和代码变更等数据源，完成可审计的自主调查，并在人工监督下执行
受控处置。整体分层与调查思想参考 HolmesGPT 等成熟 SRE Agent，但不要求复制其具体实现。

当前 V2 只是该目标的首个可运行纵向切片，仍处于学习与验证阶段，不等于已经达到生产可用。通用调查引擎
负责“如何调查”，领域 Plugin 负责“调查哪个系统、有哪些拓扑与运行知识、如何评测”。AoiLearn 判题流水线
是首个深度参考集成、真实 MCP 联调环境和可恢复故障验收场；它既不是 Core 的内置业务领域，也不是产品边界。

当前实现用于验证一条最小可信闭环：

```text
事故描述 → 受控证据采集 → 根因假设与报告 → Ground Truth 评测 → 人工监督的建议
```

## 2. 已确认的架构决策

| 决策 | 选择 | 原因 |
| --- | --- | --- |
| 产品形态 | 可扩展调查框架 + 参考集成 | 兼顾真实接入与可迁移性 |
| 调查编排 | 单一 LangGraph 调查图 | 状态、预算与停止路径可审查 |
| 基础设施边界 | 独立 Ops MCP Server | Core 不持有 SSH、Docker、数据库凭证 |
| 领域扩展 | Python package Plugin / entry point | 新业务语义不修改 Core |
| LLM 接口 | 供应商能力配置 + 受控结构化输出通道 | 原生 Schema、JSON Object 与纯提示 JSON 共享解析、校验和有限修复边界 |
| 首个交付界面 | 单窗口 CLI | 自动托管本地 MCP，展示证据、决策轨迹与报告 |
| 演进依据 | Ground Truth 评测 | 不用“看起来聪明”代替可量化质量 |

API 与事件流、持久化调查、上下文压缩、通用数据源 Toolset、Runbook 知识、审批后执行和平台自身可观测性，
属于目标平台需要覆盖的能力，但不代表必须在同一里程碑一次实现。React、多 Agent 和具体存储方案属于实现
选择，只有在需求与评测证明其价值后采用。已确认的目标设计和实施顺序分别记录在 V3 架构与迁移清单中；
本文继续描述当前 V2 的真实状态。

## 3. 组件与依赖边界

```text
┌──────────────────────────────────────────────────────────────┐
│ Application Shell（当前：CLI；后续可接 Web）                   │
│  创建事故、选择 Investigation Profile、展示证据/轨迹/报告      │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ Agent Core                                                     │
│  调查图、预算策略、证据、报告、LLM Adapter、通用评测机制        │
│  不了解 AoiLearn、judge-python、表名、Redis Key 或指标名        │
└──────────────────────────┬───────────────────────────────────┘
                           │ MCP Client Gateway
┌──────────────────────────▼───────────────────────────────────┐
│ Ops MCP Runtime                                                │
│  MCP 生命周期、环境配置、实例策略、SSH 传输、Plugin 发现        │
│  不产生根因结论                                                │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────┐
│ Domain Plugin                                                  │
│  领域 Tool、拓扑、故障场景、Ground Truth、领域提示词补充       │
│  AoiLearn Judge Plugin 是当前实现                              │
└──────────────────────────────────────────────────────────────┘
```

依赖只允许从上到下：Agent Core 通过 `ToolGateway` 调 MCP，不导入 SSH、Docker Adapter 或具体
Plugin；Plugin 可复用受策略约束的基础设施 Adapter，但不能绕过实例注册表。

## 4. 配置、Plugin 与 Core 的职责

| 变化 | 正确位置 | 示例 |
| --- | --- | --- |
| 同一能力接入新环境 | 环境配置 | SSH 主机、凭证引用、实例映射、启用的 Toolset |
| 新系统或新业务语义 | 新领域 Plugin | Bondgumi 的 Sentry / CloudWatch / Cloudflare 领域 Tool |
| 通用调查机制 | Agent Core | 预算、证据引用校验、报告 Schema、评测计算 |
| 领域验收事实 | 领域 Plugin | AoiLearn 的 Worker 停止场景、关键 Tool、Ground Truth |

当前代码已将 AoiLearn 场景与 Ground Truth 迁入 AoiLearn Judge Plugin；Core 只保留通用的
`EvaluationScenario` 与评分逻辑。人工 CLI 使用独立 `Investigation Profile`，其中只包含事故描述、
只读 Tool 白名单和预算，绝不携带评测预期根因。

## 5. 调查与报告流程

```text
load_catalog → plan（决策轨迹）→ authorize → execute_readonly_tools
                         ↑                         ↓
                         └──── 未达预算 ───────────┘
                                                    ↓
                                             diagnose → report
```

- Planner 只能建议下一批只读 Tool；Core 校验 Tool Catalog、白名单、同参重复、轮次和总调用预算。
- 每轮最多两项 Tool。Planner 必须输出简短的可审计决策摘要，说明已知事实、未解问题与选择理由；这不是
  不可复核的模型内部思维链。
- `max_rounds` 表示最多的**采证批次**，而不是所有 Planner 决策次数。采证批次用尽后，Core 仍允许一次
  `remaining_tool_calls=0` 的收尾决策，让模型解释性结束；任何继续请求 Tool 的行为仍会被策略层拒绝。
- MCP 返回被转换为带来源、时间、完整性和 Evidence ID 的 Observation。
- 诊断器只可引用本次 Observation 的 Evidence ID；不存在的引用会被领域模型拒绝。
- `diagnosed` 不只是模型自报状态：主假设置信度必须达到 70% 的确认阈值。低于阈值的候选仍保留在报告中，
  但 Core 强制将报告标为 `inconclusive` 并取消主根因标识。
- 模型不可用或报告不合法时，系统返回保留事实与停止原因的 `inconclusive` 报告。
- `recommended_action` 只是人工建议。真正的写操作仍需经过 P2 动作账本与审批指纹。

面向模型和用户的自然语言统一使用简体中文；Tool 名、JSON 字段、Evidence ID 与状态枚举保持英文
稳定标识，并在首次出现时以中文说明。

### 5.1 结构化输出兼容边界

不同模型的“JSON 模式”可靠性并不等价，因此不能把供应商格式漂移散落为 Planner 或 Diagnostician 的字段补丁。
Agent Core 的 `StructuredOutputClient` 按以下固定顺序处理每次模型输出：

```text
供应商能力模式 → 单次请求 → 确定性 JSON 解析 → Pydantic/业务契约校验
       ↑                                                   │
       └──── 脱敏错误摘要 + 有界重试（最多 2 次）───────────┘
```

- `json_schema`：供应商支持原生严格 JSON Schema 时优先使用；
  `json_object`：供应商仅提供 JSON 模式时在提示词中附带 Schema；
  `prompted_json`：供应商不接受 `response_format` 参数时完全依靠提示词，仍由本地契约拦截。
- 解析器只接受纯 JSON、单层 Markdown 围栏，或正文中**唯一**可解析的 JSON 对象；多个候选或无法解析一律失败，
  不猜测、不执行文本中的指令。
- 解析失败、字段缺失、错误枚举、非法参数、未授权 Tool、诊断证据引用错误都只返回字段级/类别级摘要给模型重做。
  响应原文不会写入 CLI、归档或修复提示。
- 每次失败还会保留安全响应状态：尝试轮次、内容为空/缺失与否、标准化 `finish_reason`、推理字段是否存在；
  不保存推理正文、模型输出正文或完整 Prompt。它用于区分截断、供应商空响应和普通契约错误。
- 修复成功的格式恢复会留在 Planner 决策轨迹；无论模型输出多么“像正确答案”，只有通过严格领域契约的结果才可以
  进入授权和 MCP 调用环节。

当前实现仍是 OpenAI Chat Completions 协议的 Adapter。接入真正非兼容的供应商时，只需新增该供应商的
`StructuredOutputClient` Adapter 并实现同一端口，不能让其 SDK 或响应格式进入 Planner、Diagnostician 或工作流。

CLI 和评测从被 Git 忽略的 `config/environments/llm.local.toml` 读取连接；可复制
[`llm.example.toml`](../config/environments/llm.example.toml) 后填写本机密钥。现有仅含 `[deepseek].api_key`
的文件继续按 DeepSeek + `json_object` 运行，因而本轮改造不要求迁移已有密钥。新的供应商配置使用 `[llm]`
指定 `provider`、`model`、`base_url`、`api_key` 与 `response_format_mode`；统一环境变量
`HINATAOPS_LLM_API_KEY` 可覆盖 TOML 中的密钥。`max_tokens` 同样由该配置控制；当前默认值为 8192，
用于为带推理输出的诊断报告保留足够的最终 JSON 空间。

CLI 的最终报告采用可折行文本组，以保证长结论、建议和运行记录路径不会因表格最小宽度而被 `…` 截断；
决策轨迹与假设区域仍使用可折行列的表格展示关系数据。

## 6. 评测优先的演进方式

Agent 质量由独立 Ground Truth 评测，不由模型自评或置信度决定。每个领域场景定义：

- 事故入口；
- 预期首要根因的匹配模式；
- 必须获取的关键 Tool 证据；
- 最大 Tool 调用次数；
- 可接受的人工建议边界。

当前 P3.5 已实现并验证 `python_judge_worker_unavailable`：真实 AoiLearn 容器短暂停止、MCP
采证、报告生成、评分、容器恢复。真实 DeepSeek-V4.1-Flash 已完成健康负例与受控正例基线；每轮架构改变后
仍须重新运行，以避免把旧模型行为当作当前系统质量。

## 7. V2 原近期路线图

以下内容是 V2 阶段形成的实施顺序，保留用于解释当前代码来源，不再代表完整的产品路线图；后续由讨论确定的
V3 架构与迁移清单替代。

1. 用当前 CLI 与决策轨迹重新运行健康负例和 Worker 停止正例，形成可比较的回归基线。
2. 阅读 AoiLearn 的“提交 → Outbox → Redis → Worker → 结果回写”真实链路，定义两类可验证故障场景。
3. 增加场景的 Ground Truth、Fixture 或安全真实注入、评测记录；优先证明跨链路诊断能力，而不是堆叠 Tool。
4. 至少三类场景稳定后，将现有动作账本接入 CLI：人工查看证据、批准、受控执行、恢复验证和留档。
5. 仅当评测显示运行知识缺失时引入 Runbook RAG；Web、checkpoint 与多 Agent 仅在需求明确时实现。

## 8. 当前里程碑边界

- 当前不提供任意 Shell、SQL、Redis 命令或网络请求给模型；未来的通用查询 Toolset 仍须经过能力声明、
  参数约束与策略授权，不把“通用”解释为“无限制”。
- 当前不自动执行生产环境动作；目标平台只允许经策略校验与人工批准的动作，并要求执行后验证与审计。
- 不把 AoiLearn 的服务名、Redis Key、表名或指标名写入 Agent Core。
- 当前不为架构复杂度本身堆叠多 Agent、RAG、Web 前端或微服务；这些能力是否进入实施范围由实际职责决定。
