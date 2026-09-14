# HinataOps V3 目标架构

状态：已确认的目标架构。本文描述最终职责与稳定边界，不表示所有模块均已实现。当前代码状态见
[V2 架构](ARCHITECTURE.md)，具体实施顺序见 [V3 迁移清单](V3_MIGRATION_CHECKLIST.md)。

## 1. 项目定义

HinataOps 是一个**面向生产级通用 SRE 调查平台的最小可信实现**。AoiLearn 是首个参考接入和真实验证场，
而不是 Agent Core 的内置领域。

项目通过有限但完整的纵向切片，重点证明以下核心架构能力：

- 可插拔的多数据源 Toolset；
- 证据驱动的自主调查；
- 原始数据、证据和模型上下文分离的上下文治理；
- 人工审批后的受控动作与执行后验证；
- 由 Ground Truth、正例和负例推动的可评测演进。

项目参考 HolmesGPT 等成熟 SRE Agent 的设计思想，但不追求复刻其生产环境覆盖范围，也不宣称当前版本已经
具备完整生产可用性。“生产级”约束的是架构职责、故障边界和验收方式；“最小可信”约束的是当前交付范围。

## 2. 术语与边界

为避免再次把数据源、业务系统和故障场景混在一起，V3 使用以下术语：

| 术语 | 含义 | 示例 |
| --- | --- | --- |
| Incident | 一次待调查事件的标准化入口 | “Python 判题任务无结果” |
| Tool | 一个有 Schema、风险等级和结构化返回的原子能力 | 查询时间序列、读取容器状态 |
| Toolset | 同一数据源或能力域的一组 Tool | Prometheus、Docker、Redis、MySQL |
| Tool Provider | Toolset 的发现与调用传输方式 | MCP、进程内、HTTP、云 SDK |
| Environment Pack | 某个被运维系统的拓扑、选择器、约束与运行知识 | AoiLearn Judge |
| Investigation Profile | 某类调查的入口默认值和允许能力 | 判题任务无结果调查 |
| Scenario | 具有 Ground Truth 和注入/Fixture 的评测案例 | Worker 停止正例 |
| Artifact | Tool 返回的完整原始结果或其持久化引用 | 原始日志、Prometheus 响应 |
| Evidence | 从 Artifact 中提取、可被报告引用的事实 | Worker `up=0` |
| Context Projection | 本轮发送给模型的有界视图 | 最近关键指标及 Evidence ID |

“通用 Toolset”不等于“任意命令执行”。通用性来自统一契约、受限参数和可替换 Provider，而不是向模型暴露
无限制 Shell、SQL、PromQL 或网络访问。

## 3. 参考架构与 HinataOps 取舍

HolmesGPT 展示了值得借鉴的成熟分层：原生 Tool Calling 调查循环、可配置 Toolset、前置条件检查、并行 Tool
执行、结构化 Tool 结果、上下文限制、SSE 过程事件和人工审批暂停/恢复。参考材料：

- [HolmesGPT 项目与数据源范围](https://github.com/HolmesGPT/holmesgpt/blob/master/README.md)
- [HolmesGPT 代码结构与 Toolset 约定](https://github.com/HolmesGPT/holmesgpt/blob/master/CLAUDE.md)
- [Tool Calling 调查循环](https://github.com/HolmesGPT/holmesgpt/blob/master/holmes/core/tool_calling_llm.py)
- [Toolset Manager](https://github.com/HolmesGPT/holmesgpt/blob/master/holmes/core/toolset_manager.py)
- [结构化 Tool 结果与执行边界](https://github.com/HolmesGPT/holmesgpt/blob/master/holmes/core/tools.py)
- [HTTP、SSE 与审批恢复](https://github.com/HolmesGPT/holmesgpt/blob/master/docs/reference/http-api.md)

HinataOps 采用其职责分离思想，但保留自己的重点：

| 方向 | V3 决策 |
| --- | --- |
| 调查选择 | 使用模型原生 Tool Calling，移除每轮私有 Planner JSON 协议 |
| 工作流 | 外层持久化生命周期 + 内层显式 Tool Calling Loop |
| Tool 接入 | MCP-first，但 Core 面向 Tool Provider 契约而非 MCP-only |
| 证据 | Artifact、Evidence、Context Projection 三层分离，报告引用 Evidence ID |
| 安全 | 读取也分风险等级；写操作使用独立审批状态机，不伪装成普通只读 Tool |
| 评测 | 场景 Ground Truth 独立于人工调查入口，保留健康负例和真实故障注入 |
| 范围 | 实现少量真实纵向切片，不复制所有 Kubernetes、云平台和 SaaS 集成 |

## 4. 系统上下文

```text
用户 / CLI / Web UI / Alert Webhook
                 │
                 ▼
        Application API + Event Stream
                 │
                 ▼
        Incident Service / Run Repository
                 │
                 ▼
       Durable Investigation Workflow
   intake → investigate → report → awaiting_approval
                 │                       │
                 ▼                       ▼
       Native Tool-Calling Loop     Action Workflow
          │          │              approve → execute
          │          │                    → verify
          ▼          ▼
    Model Gateway  Policy Engine
          │          │
          └────┬─────┘
               ▼
       Toolset Registry + Executor
       │ MCP │ Local │ HTTP │ SDK │
               │
               ▼
   Metrics / Logs / Traces / Containers / DB / Cloud
               │
               ▼
     Artifact Store → Evidence Store
               │             │
               └──────┬──────┘
                      ▼
              Context Manager

横切能力：审计、OpenTelemetry、密钥隔离、评测、取消与超时
```

## 5. 两层编排

### 5.1 外层：持久化调查生命周期

外层工作流只表达具有业务意义、需要恢复或人工介入的阶段：

```text
created
  → investigating
  → reporting
  → diagnosed | inconclusive | failed | cancelled
  → action_proposed
  → awaiting_approval
  → executing
  → verifying
  → verified | action_failed | verification_failed | rejected
```

LangGraph 可以继续承担这一层，但只有真正使用 checkpointer、暂停/恢复和人工审批时才体现价值。外层不再为
每一次 Tool 调用建立 `plan → authorize → execute` 图节点。

外层必须保存 `run_id`、当前状态、版本和最后事件序号。进程重启后可以恢复等待审批或未结束的调查；同一状态
转换必须可重放或具有幂等保护。

### 5.2 内层：原生 Tool Calling 调查循环

```text
构建最小上下文
    ↓
LLM 返回 tool_calls 或结束文本
    ↓
策略校验名称、参数、风险和剩余预算
    ↓
并行执行无冲突的只读 Tool
    ↓
保存 Artifact，抽取 Evidence，生成 Context Projection
    ↓
追加 Tool 结果并进入下一步
```

循环受统一 `RunLimits` 约束：最大步骤、Tool 调用数、墙钟时间、模型 Token、单次结果大小、累计 Artifact 大小
和失败重试次数。达到步骤上限时停止开放 Tool，并进入一次有界报告合成，而不是再增加特殊“收尾 Planner 轮”。

每步保留可审计的 `DecisionSummary`：当前已知事实、待解决问题和选择的检查。它是面向用户的决策说明，不请求、
保存或声称暴露模型私有思维链。

## 6. 核心组件职责

### 6.1 Incident Service

将 CLI 文本、API 请求和未来告警统一为 `Incident`：

```text
incident_id, title, description, environment_id,
started_at, observed_at, affected_resources,
labels, correlation_keys, requested_by
```

调查使用标准化时间锚点和资源标识，避免所有 Tool 各自猜测“最近多久”和“哪个服务”。

### 6.2 Model Gateway

Model Gateway 隔离供应商 SDK、鉴权、重试、超时、Token 统计和能力差异，并公开两类端口：

- `complete_with_tools`：调查循环的原生 Tool Calling；
- `create_validated_output`：最终报告等少量严格结构化输出。

供应商兼容由 Adapter 或 LiteLLM 一类网关解决。Agent Core 不解析厂商专属响应，也不为某个模型添加字段别名。
现有确定性 JSON 提取和 Pydantic 校验可保留给最终报告，但不再承担逐轮规划协议。

### 6.3 Toolset Registry

每个 Toolset 提供 `ToolsetManifest`：

```text
id, version, description, provider,
capabilities, risk_level, prerequisites,
configuration_schema, enabled_environments
```

Registry 负责：发现、配置校验、前置条件检查、健康状态、名称冲突、启停、版本和 Tool Catalog 快照。Toolset
不可用时应返回明确状态，不让模型把“未配置”“无数据”和“基础设施故障”混为一谈。

### 6.4 Tool Provider 与 Executor

Core 依赖统一 Provider 契约：

```python
class ToolProvider(Protocol):
    async def discover(self) -> list[ToolDefinition]: ...
    async def invoke(self, request: ToolRequest) -> ToolResult: ...
```

首个实现为 MCP Provider；后续只在确有价值时增加 Local、HTTP 或 Cloud SDK Provider。Executor 统一处理超时、
取消、并发限制、有限重试、结果大小和审计，不解释具体业务根因。

### 6.5 Policy Engine

策略检查不能只判断“Tool 是否在白名单”。至少包含：

- 调查画像允许的 Toolset、Tool 与环境；
- `READ`、`SENSITIVE_READ`、`MUTATE` 风险等级；
- 参数 Schema、时间窗口、资源选择器和结果上限；
- 同参去重、调用频率、并发和预算；
- 敏感字段脱敏；
- 动作是否需要人工批准，以及批准指纹是否匹配。

Tool 的自然语言说明帮助模型选择，但只有确定性策略可以授权执行。

### 6.6 Artifact、Evidence 与 Context Manager

三层数据承担不同职责：

| 层 | 保存内容 | 是否直接进入模型上下文 |
| --- | --- | --- |
| Artifact | Tool 完整原始结果、内容类型、哈希、大小、存储位置 | 默认否 |
| Evidence | 可引用事实、来源、时间范围、可靠性、Artifact 引用 | 是，使用有界摘要 |
| Context Projection | 当前步骤所需 Evidence、历史摘要和 Tool 结果片段 | 是 |

Context Manager 在每轮调用前计算预算。当内容接近上限时，优先移除已持久化的原始结果，再压缩旧步骤为带
Evidence ID 的摘要；系统提示、事故事实、未解问题和关键反驳证据不得被无提示丢弃。模型可以通过受限 Tool
按 `artifact_id` 获取片段，而不是要求一次载入全部日志。

压缩操作本身写入事件和指标，记录压缩前后 Token、保留的 Evidence ID 与算法版本。

### 6.7 Report Synthesizer

调查循环结束后，由独立报告阶段产生结构化 `InvestigationReport`：

- 现象与影响范围；
- 候选根因、支持证据、反驳证据和缺失证据；
- 主根因及确认状态；
- 推荐的只读检查或动作建议；
- 使用的 Tool、时间范围和调查限制。

报告只能引用本次 Run 的 Evidence ID。确认状态由证据条件和评测校准共同约束，模型自报置信度不能单独把结果
升级为已确认。对用户展示可以是 Markdown，机器状态与引用必须保持结构化。

### 6.8 Action Workflow

写操作采用独立命令链路：

```text
ActionProposal
  → deterministic policy check
  → immutable approval fingerprint
  → human approve/reject
  → idempotent claim and execute
  → post-condition verification
  → audit record
```

动作声明影响范围、风险、前置条件、验证方式和可用回退方案。审批只对完全相同的计划有效；参数变化后必须重新
审批。调查模型只能提出 Proposal，不能直接调用写 Tool 绕过账本。

### 6.9 Application API 与事件流

CLI、HTTP API 和未来 Web UI 共用 `InvestigationApplicationService`，不能各自组装 Agent。建议的最小接口：

```text
POST /incidents/{id}/runs       创建调查
GET  /runs/{run_id}             获取状态和报告
GET  /runs/{run_id}/events      SSE 事件流
POST /actions/{id}/decision     批准或拒绝
POST /runs/{run_id}/cancel      取消调查
```

事件采用带序号和版本的稳定信封，至少覆盖：Run 状态变化、模型决策摘要、Tool 开始/结束、Evidence 生成、上下文
压缩、报告完成、等待审批、动作结果和错误。CLI 只是该事件流的一个渲染器。

### 6.10 持久化、审计与自身可观测性

最小可信切片可以先使用 SQLite 和本地 Artifact 文件，但必须通过 Repository/Store 边界隔离。运行记录包括：

- Incident、Run 状态和状态版本；
- 事件流；
- Tool Catalog 快照与调用审计；
- Artifact 元数据、Evidence 和报告；
- Action Proposal、审批、执行和验证；
- 模型、提示词/Schema 版本、Token、耗时和失败类型。

平台使用 OpenTelemetry 语义记录模型步骤、Tool 调用、外部依赖、上下文压缩和端到端调查耗时。日志与事件必须
过滤密钥、SSH 参数和敏感 Tool 输出。

## 7. Toolset 与 Environment Pack 的组合

V3 不再把全部能力封装成一个巨大的 `aoi_learn_judge` Toolset：

```text
通用数据源 Toolset
├── prometheus
├── docker
├── redis_stream
├── mysql_readonly
└── application_logs（新增）

AoiLearn Environment Pack
├── 服务与依赖拓扑
├── 逻辑资源到真实实例的映射
├── 允许的指标、Stream、表和日志范围
├── 判题流水线运行知识
├── Investigation Profiles
└── Evaluation Scenarios
```

环境包可以通过受限查询模板或具名查询定义业务语义，但不得把 AoiLearn 名称写入 Core。确有高价值的跨数据源
聚合可以作为 AoiLearn 组合 Tool 存在；它应建立在通用 Adapter/Toolset 上，并明确其领域属性。

为了证明通用性，除 AoiLearn 外还需一个轻量第二接入或契约级 Fixture：不修改 Core 即可加载不同环境包和
Toolset。它用于验证扩展边界，不要求立即达到 AoiLearn 的接入深度。

## 8. 可靠性与失败语义

调查 Tool 的统一 `ToolResult` 最终至少包含：

```text
status: success | no_data | partial | error
data | artifact_ref
summary
source, observed_at, time_range
error: kind, retryable, safe_message
elapsed_ms, output_bytes
```

M1 先落地不含 Artifact 和运行审计字段的首版契约，后续由 M2～M6 按职责扩展。`approval_required` 不属于
调查 Tool 结果；需要审批的写操作进入独立 `ActionProposal → awaiting_approval → execute → verify` 状态机。

必须区分：

- 成功但没有匹配数据；
- 数据源只返回部分结果；
- 目标当前不可用；
- Tool 配置或前置条件不满足；
- 命令瞬时超时；
- 参数被策略拒绝；
- 动作等待人工审批（由 Action Workflow 表达，不伪装为 Tool 查询结果）。

重试只处理声明为瞬时、幂等且仍在预算内的失败。每次尝试独立留档，不能用最终成功覆盖先前异常。

## 9. 评测体系

评测分为四层：

| 层级 | 验证内容 |
| --- | --- |
| 契约测试 | Tool Schema、Provider、策略、Evidence 引用和状态机 |
| 回放测试 | 固定 Artifact/Tool 结果下的调查路径和报告 |
| LLM 评测 | 多模型下的 Tool 选择、证据覆盖、误诊和成本 |
| 真实注入 | 可恢复故障中的发现、诊断、审批、执行和恢复验证 |

每个 Scenario 定义预期根因、必要证据、允许/禁止动作、预算和恢复条件。核心指标至少包括 Top-1 根因、关键证据
覆盖、无依据断言、无效/重复 Tool 调用、调查完成率、平均步骤、Token、延迟、动作安全与恢复成功率。

健康负例必须与故障正例同等重要：系统在线且缺少任务标识时，正确结果可能是证据不足，而不是强行给出根因。

## 10. 最小可信切片验收

V3 达到以下条件，才可称为本项目定义下的“最小可信实现”：

- 同一个 Core 通过 Registry 使用至少四种只读数据源 Toolset；
- AoiLearn 领域名称不出现在 Agent Core、通用 Provider 或通用策略中；
- 原生 Tool Calling Loop 取代逐轮 Planner JSON，并通过至少两种模型配置的契约测试；
- 原始 Artifact 不默认进入模型上下文，Evidence 可追溯到 Artifact；
- 调查可通过 CLI 发起，并通过 HTTP/SSE 观察同一套版本化事件；
- 至少三类可重复场景覆盖健康负例、单组件故障和跨链路故障；
- 至少一种真实动作完成提议、人工审批、幂等执行和执行后验证；
- 进程重启后可以恢复等待审批的 Run；
- 第二个轻量环境包或契约 Fixture 在不修改 Core 的情况下接入；
- 每次运行记录 Tool、Evidence、压缩、Token、耗时、错误和评测结果；
- 全量单元/契约测试通过，真实注入能够自动恢复实验环境。

这些条件证明架构闭环，不代表已经获得多租户、高可用、海量集群或所有云平台的生产覆盖。

## 11. 明确延后的范围

- 完整复刻 HolmesGPT 的 Kubernetes、云平台、数据库和 SaaS 集成数量；
- 默认开放任意 Shell、SQL、PromQL 或网络请求；
- 无人监督的生产自动修复；
- 完整 React 管理后台、组织/租户管理和企业级 RBAC；
- 为展示复杂度而引入多 Agent；
- 未经评测证明必要的大型 Runbook RAG；
- 高可用集群、分布式任务队列和独立微服务拆分。

目标架构允许未来增加这些能力，但当前不为空接口或假实现支付复杂度成本。

## 12. 已确认架构决策

| ID | 决策 |
| --- | --- |
| V3-01 | 产品是面向生产级通用 SRE 调查平台的最小可信实现 |
| V3-02 | AoiLearn 是参考环境和验收场，不是 Core 领域 |
| V3-03 | 外层持久化工作流与内层原生 Tool Calling Loop 分离 |
| V3-04 | MCP-first，但 Core 面向 Tool Provider 契约 |
| V3-05 | 通用数据源 Toolset 与 Environment Pack 分离 |
| V3-06 | Artifact、Evidence、Context Projection 分离 |
| V3-07 | 只读调查与审批动作使用不同授权链路 |
| V3-08 | CLI/API/SSE 共用 Application Service 和事件契约 |
| V3-09 | Ground Truth 与真实故障注入驱动演进 |
| V3-10 | 多 Agent、Web UI 和大规模集成不是最小可信切片的前置条件 |
