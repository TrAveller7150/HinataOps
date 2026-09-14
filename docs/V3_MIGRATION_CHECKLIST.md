# HinataOps V3 迁移清单

本文把[V3 目标架构](ARCHITECTURE_V3.md)映射到当前代码。它是实施和验收清单，不是要求一次完成的大爆炸重写。
每个阶段都必须保持可运行、可测试和可回退；只有阶段改变真实调查行为时，才重新执行相应负例与受控正例。

基线：提交 `06828eb`，81 项测试通过。V2 仍可通过 `uv run hinataops investigate ...` 完成只读调查。

## 1. 迁移原则

- 先建立稳定契约，再替换内部实现；不同时重写 Toolset、Agent Loop、API 和动作链路。
- 新旧路径短期并存时必须由显式 Feature Flag 选择，不根据异常静默回退。
- 每个阶段先写契约/回放测试，再迁移真实 AoiLearn 调用。
- AoiLearn 代码只能依赖通用 Core 与基础设施能力，Core 不反向导入 AoiLearn。
- 不以新增抽象数量作为完成标准；没有第二个实现或明确边界价值的接口暂不创建。
- 不删除 V2 路径，直到 V3 在同一场景上达到或超过基线。
- 文档中的“目标文件”是推荐落点；实施时如现有文件可保持单一职责，可以少建文件。

## 2. 当前资产处置

### 2.1 保留并演进

| 当前资产 | 文件 | V3 去向 |
| --- | --- | --- |
| Evidence ID 与引用校验 | `agent_core/models.py` | 演进为 Artifact/Evidence 模型，保留不可伪造引用校验 |
| MCP 传输隔离 | `agent_core/gateway.py` | 成为首个 `McpToolProvider` |
| Python entry point 发现 | `ops_mcp/plugins.py`、`pyproject.toml` | 演进为带 Manifest、状态和前置条件的 Toolset 发现 |
| 受限基础设施 Adapter | `ops_mcp/adapters/*` | 作为通用 Toolset 的执行依赖，继续保持超时和输出预算 |
| 环境实例注册表 | `ops_mcp/config.py`、`ops_mcp/policy/*` | 演进为环境资源目录和 Toolset 配置，不允许调用方提供任意地址 |
| 动作状态机与审批指纹 | `actions/models.py`、`actions/repository.py` | 接入外层工作流、API/SSE 和执行后验证 |
| Ground Truth 评测 | `agent_core/evaluation.py` | 扩展为回放、LLM 和真实注入三种 Runner |
| 调查事件 | `agent_core/events.py` | 演进为版本化、可持久化事件信封 |
| CLI 自动托管本地 MCP | `cli.py` | 保留为开发体验，CLI 改为 Application Service 的薄适配层 |

### 2.2 必须重构

| 当前问题 | 文件证据 | 目标变化 |
| --- | --- | --- |
| `ToolGateway` 文档和实现均绑定 MCP | `agent_core/gateway.py` | Core 改依赖 Tool Provider；MCP 作为实现 |
| 每轮 Planner 使用私有 JSON 决策 | `agent_core/planner.py`、`agent_core/llm.py` | 替换为模型原生 Tool Calling |
| LangGraph 同时承担微观循环和业务工作流 | `agent_core/workflow.py` | 图上移为外层生命周期；内层改显式循环 |
| Observation 同时保存完整结果和上下文摘要 | `agent_core/models.py`、`evidence.py` | 拆分 Artifact、Evidence、Context Projection |
| Toolset Plugin 直接注册到 FastMCP | `ops_mcp/plugins.py` | Manifest/能力声明与 MCP 发布适配分离 |
| AoiLearn 一个 Plugin 聚合所有数据源 | `toolsets/aoi_learn_judge/plugin.py` | 通用数据源 Toolset + AoiLearn Environment Pack |
| CLI 直接创建 AoiLearn Presenter | `cli.py` | Presenter/Transformer 随 Toolset 元数据发现 |
| 同步回调事件不可回放 | `agent_core/events.py` | Event Store + CLI/SSE Subscriber |
| JSON 文件是主要运行归档 | `investigation_service.py`、`cli.py` | Run Repository 为事实源，JSON 仅为导出格式 |
| 固定 70% 置信门槛 | `agent_core/llm.py` | 改为可评测校准的确认策略，不把分数当统计概率 |

### 2.3 最终移除

- `ModelPlanningDecision`、`LlmInvestigationPlanner` 与逐轮 Planner JSON 修复协议；
- 为 `max_rounds` 增加的零 Tool 收尾规划特例；
- Core/CLI 对 `AoiLearnJudgeEvidencePresenter` 的直接引用；
- 将所有原始 Tool `value` 重复放入每轮 Prompt 的路径；
- 把 `aoi_learn_judge` 当作单一通用 Toolset 的注册结构；
- 仅存在于内存、进程结束即丢失的事件监听作为唯一过程记录。

移除必须发生在替代路径通过回归之后，不能提前删除。

### 2.4 明确延后

- 多 Agent 调度；
- 完整 React 运维控制台；
- Kubernetes、CloudWatch、Sentry、Cloudflare 等大规模集成；
- 多租户、企业 RBAC、高可用和分布式任务队列；
- 任意 Shell/SQL/PromQL；
- 未经评测证明必要的 Runbook 向量 RAG。

## 3. 阶段 M0：确认迁移起点

目标：保留足够的回归保护后停止投入即将替换的 V2 调查协议，不为旧路线建设新的评测基础设施。

- [x] 确认提交 `06828eb` 对应的 81 项既有测试全部通过。
- [x] 保留 P3.5 真实正例、健康负例、Planner 格式失败与瞬时 Tool 超时的历史文档。
- [x] 完成 V3 目标架构和迁移清单，明确保留、重构与删除边界。
- [x] 提交 V3 架构文档，形成后续迁移的 Git 起点。

M0 不再重新调用模型、注入故障、制作 V2 Planner Fixture 或统计旧协议性能。V2 值得保留的 MCP、Evidence、
策略、动作和评测行为由现有测试保护；真正的调查质量、Token、延迟和稳定性基线从 M3 原生 Tool Calling Loop
开始记录。

验收：文档提交完成、工作区干净，即可进入 M1。

## 4. 阶段 M1：建立传输无关的 Tool 契约

目标：不改变调查行为，先让 Core 不再把 Tool 等同于 MCP。

状态：已完成。首版契约和 Provider 已落地，V2 调查路径保持可运行；后续增强项按范围转移登记表执行。

### 实施

- [x] 新建共享 `tooling` 契约，供 Agent Core 与 MCP Integration 共同依赖，避免任一方反向依赖另一方。
- [x] 定义首版 `ToolDefinition`：名称、描述和输入 JSON Schema。
- [x] 继续使用当前只含名称与参数的 `ToolCall`；移除其 Planner/MCP 专属注释，但本阶段不增加运行审计字段。
- [x] 定义首版 `ToolResult`：Schema 版本、`success/no_data/partial/error`、环境、来源、观测时间、数据、警告和
      结构化错误。
- [x] 为 `ToolResult` 增加状态不变量：成功/无数据不能携带错误，错误必须携带 `ToolError`，部分结果必须明确
      警告或错误。
- [x] 将 Core 端口直接重命名为 `ToolProvider`，保持 `list_tools/invoke` 两个最小方法。
- [x] 将 MCP SDK、Streamable HTTP 与协议解析移动到 `integrations/mcp_provider.py`，实现 `McpToolProvider`。
- [x] 直接迁移仓库内调用方，不保留未发布内部 API 的 Gateway 兼容别名。
- [x] 让现有只读 MCP Tool 返回/映射到首版 `ToolResult`，明确区分无数据、部分数据和无可用结果。
- [x] 让 `EvidenceCollector` 只消费 `ToolResult`，不再读取 MCP `structuredContent` 或约定的裸 `metadata` 字典。
- [x] `Observation` 显式保留 `result_status` 与结构化 Tool 错误；重试策略读取该字段，不再反向解析原始结果。
- [x] 为状态组合、Schema 不兼容、未知 Tool、MCP 协议错误、无数据、部分结果、失败和瞬时超时补契约测试。

### 从原 M1 转移的工作

缩小 M1 只改变顺序，下列目标仍保留在后续阶段：

| 原 M1 内容 | 处理决定 | 承接阶段 |
| --- | --- | --- |
| Tool 稳定 ID、Toolset ID、风险等级、结果 Schema 声明 | 与 Manifest 和 Catalog 一起设计 | M2 |
| `call_id`、调用截止时间和执行上下文 | 由 Tool Executor 和原生调查循环创建 | M3 |
| Artifact 引用、查询时间范围和输出字节数 | 与 Artifact/Evidence 模型一起加入 `ToolResult` 新版本 | M4 |
| `run_id` 与完整调用关联 | Run Repository 和持久化事件建立后加入 | M6 |
| `approval_required` | 从调查 Tool 契约取消，由独立动作状态机表达 | M7 |
| Tool/模型耗时、Token 与 Span 维度 | M3 先记录执行耗时，M8 接入完整可观测性 | M3、M8 |

### 主要文件

- 删除：`src/hinataops/agent_core/gateway.py`
- 重构：`src/hinataops/agent_core/evidence.py`
- 新增：`src/hinataops/tooling/contracts.py`
- 新增：`src/hinataops/agent_core/tool_provider.py`
- 新增：`src/hinataops/integrations/mcp_provider.py`
- 重构：`src/hinataops/ops_mcp/server.py`
- 重构：`src/hinataops/ops_mcp/toolsets/aoi_learn_judge/plugin.py`

验收：现有 V2 Workflow 在不理解 MCP 类型的情况下完成原有测试；MCP 适配、V2 metadata 转换和结果状态契约
测试通过。Topology 与 AoiLearn 只读 Tool 已返回标准 envelope；V2 metadata 仅作为 MCP Provider 中的
保守兼容路径，待 M5 移除。

## 5. 阶段 M2：Toolset Manifest、Registry 与前置条件

目标：把“发现了若干 Tool”升级为“知道 Tool 属于哪个能力、当前为什么可用或不可用”。

### 实施

- [ ] 定义 `ToolsetManifest` 和版本规则。
- [ ] 为 Tool 定义稳定限定 ID、所属 Toolset、风险等级和结果 Schema 声明。
- [ ] 定义 Toolset 状态：`enabled/disabled/unavailable/misconfigured/degraded`。
- [ ] Plugin 暴露 Manifest、配置 Schema、Tool 定义和前置条件，不只接收 `FastMCP.register`。
- [ ] MCP Server Adapter 根据通用定义发布 Tool；传输层不拥有领域知识。
- [ ] Registry 处理 entry point 发现、重复 ID、Tool 名命名空间、配置覆盖和 Catalog 快照。
- [ ] 前置条件检查覆盖配置、目标连通性和必要权限，并提供有界超时与短期缓存。
- [ ] Catalog 向模型只暴露当前环境已启用且前置条件通过的 Tool。
- [ ] 运行归档保存本次 Catalog/Manifest 版本，保证回放知道当时有哪些能力。

### 主要文件

- 重构：`src/hinataops/ops_mcp/plugins.py`
- 简化：`src/hinataops/ops_mcp/server.py`
- 演进：`src/hinataops/ops_mcp/config.py`
- 新增或就地定义：Toolset Registry、Manifest、Prerequisite 契约

验收：禁用、缺配置、目标不可达和名称冲突均有确定性结果；新增测试 Toolset 无需修改 Server 分支逻辑。

## 6. 阶段 M3：Model Gateway 与原生 Tool Calling Loop

目标：移除反复出现格式漂移的 Planner 私有协议，同时保留严格报告契约。

### 实施

- [ ] 将当前 `StructuredOutputClient` 拆成逻辑上独立的 Tool Calling 与结构化输出能力。
- [ ] `ModelGateway.complete_with_tools` 统一供应商 Tool Call、结束原因、用量和安全错误元数据。
- [ ] 为不支持严格 Tool Calling 的模型明确标记 capability unavailable，不静默退回文本 JSON Planner。
- [ ] 实现显式 `InvestigationLoop`：构建上下文、调用模型、授权、执行 Tool、追加结果、判断结束。
- [ ] Tool Executor 为每次调用创建 `call_id`，传递截止时间和受限执行上下文，并记录执行耗时。
- [ ] 定义统一 `RunLimits`，取代分散的 `max_rounds/max_tool_calls` 特例。
- [ ] 支持无依赖只读调用并行执行；相同资源或敏感调用按策略串行。
- [ ] 达到步骤预算后关闭 Tool，执行一次报告合成，不再调用收尾 Planner。
- [ ] 保留 `StructuredOutputClient` 的确定性解析、有限重试和 Pydantic 校验，仅用于最终报告等稳定契约。
- [ ] 记录每步公开 Decision Summary、Tool 选择、停止原因、Token 和耗时，不记录私有思维链。
- [ ] 用 OpenAI-compatible Fake、DeepSeek 配置和至少一个不同能力模式完成契约测试。

### 主要文件

- 重构/拆分：`src/hinataops/agent_core/llm.py`
- 新增：内层 Investigation Loop 模块
- 演进：`src/hinataops/agent_core/policy.py`
- 演进：`src/hinataops/agent_core/events.py`
- 迁移后删除：`src/hinataops/agent_core/planner.py`

验收：调查过程中不再存在 Planner JSON 解析路径；Tool 仍必须经过本地策略；报告引用校验保持有效；完成首轮
V3 健康负例与受控正例，并把结果作为后续阶段的 M3 调查质量基线。

回退条件：若目标模型的原生 Tool Calling 在契约测试中不稳定，保留 V2 Feature Flag 并先修 Model Adapter，
不能把供应商字段补丁重新写入调查循环。

## 7. 阶段 M4：Artifact、Evidence 与上下文治理

目标：模型上下文不再等于全部历史原始数据，并且任何压缩都不破坏证据追溯。

### 实施

- [ ] 定义 `ArtifactRef`、内容哈希、媒体类型、大小、来源和保留策略。
- [ ] 将 Artifact 引用、查询时间范围和输出字节数加入新版 `ToolResult`，保持旧 Schema 的显式迁移路径。
- [ ] 实现本地文件 Artifact Store；元数据由 Repository 保存，接口允许未来替换对象存储。
- [ ] 将 `Observation` 迁移为 `Evidence`；大字段只保留 Artifact 引用和有界摘要。
- [ ] 把 Aoi Presenter 的能力重构为随 Toolset 注册的 Result Transformer/Projector。
- [ ] Context Manager 根据 Token 预算选择系统约束、Incident、未解问题、关键 Evidence 与最近步骤。
- [ ] 实现确定性旧步骤压缩，并保存算法版本、压缩前后大小及保留的 Evidence ID。
- [ ] 提供受限 Artifact 片段读取 Tool，限制行数、字节数、时间范围和敏感字段。
- [ ] 报告引用校验从 Observation ID 迁移到 Evidence ID，并验证 Evidence 指向真实 Artifact/Tool Call。
- [ ] 增加超长日志、超大指标结果、多轮压缩、反驳证据保留和敏感字段脱敏测试。

### 主要文件

- 重构：`src/hinataops/agent_core/models.py`
- 重构：`src/hinataops/agent_core/evidence.py`
- 迁移：`src/hinataops/ops_mcp/toolsets/aoi_learn_judge/presentation.py`
- 新增：Artifact Store、Evidence Store、Context Manager

验收：固定超长 Fixture 不超模型上下文预算；归档可由 Evidence 定位原始 Artifact；压缩后根因与关键反证仍可
评测；Prompt/事件/日志不泄露原始敏感内容。

## 8. 阶段 M5：拆分通用数据源 Toolset 与 AoiLearn Environment Pack

目标：证明 AoiLearn 是组合和配置，而不是 Core 或所有 Tool 的命名空间。

### 实施

- [ ] 从 Aoi Plugin 中提取 Prometheus 受限查询/摘要 Toolset。
- [ ] 提取 Docker 只读运行时 Toolset。
- [ ] 提取 Redis Stream 只读 Toolset。
- [ ] 提取 MySQL 只读聚合 Toolset；仅允许具名查询或受审核模板，不接受任意 SQL。
- [ ] 新增应用日志 Toolset，优先读取 Docker 日志或现有日志源的受限时间窗口。
- [ ] 建立 AoiLearn Environment Pack：拓扑、资源映射、选择器、查询模板、调查画像和评测场景。
- [ ] 保留确有价值的判题流水线跨源聚合为 Aoi 组合 Tool，并明确它不是通用数据源 Tool。
- [ ] CLI 通过事件中的通用 Presentation 数据渲染，不导入 Aoi Presenter。
- [ ] 新增第二个轻量 Environment Pack 或契约 Fixture，验证不修改 Core 即可加载不同拓扑与 Toolset。
- [ ] 增加架构守卫测试：`agent_core` 和通用 Toolset 禁止导入 `aoi_learn_judge`。

### 当前文件迁移

| 当前路径 | 目标 |
| --- | --- |
| `aoi_learn_judge/runtime_summary.py` | Prometheus Toolset + Aoi 查询定义 |
| `aoi_learn_judge/container_runtime.py` | Docker Toolset + Aoi 资源选择器 |
| `aoi_learn_judge/stream_summary.py` | Redis Stream Toolset + Aoi Stream 映射 |
| `aoi_learn_judge/pipeline_summary.py` | MySQL Toolset + Aoi 具名聚合查询 |
| `aoi_learn_judge/investigation.py` | Aoi Investigation Profiles |
| `aoi_learn_judge/evaluation.py` | Aoi Evaluation Scenarios |
| `aoi_learn_judge/plugin.py` | Aoi Environment Pack 入口，删除大段 FastMCP 内嵌注册 |

验收：Aoi 原场景通过至少四种 Toolset 完成；第二环境接入不改 Core；任意地址、容器、Key 和 SQL 仍无法由模型
直接指定。

## 9. 阶段 M6：外层持久化工作流、Application Service 与 SSE

目标：让同一调查可以被 CLI、API 和未来 UI 使用，并能在进程边界恢复。

### 实施

- [ ] 定义持久化 Run 状态机和乐观版本字段。
- [ ] 将 LangGraph 重构为外层节点：调查、报告、等待审批、执行、验证和结束。
- [ ] 配置正式 Checkpointer；验证重启后恢复 `awaiting_approval`。
- [ ] 建立 Incident/Run/Event Repository，SQLite 为当前实现。
- [ ] 将同步 `InvestigationEventListener` 演进为追加式 Event Store + Subscriber。
- [ ] Event Envelope 包含 `event_id/run_id/sequence/type/schema_version/timestamp/payload`。
- [ ] 将 `run_id` 关联到每次 Tool 调用、Artifact、Evidence 与模型步骤，形成完整调用链。
- [ ] `InvestigationApplicationService` 成为 CLI 与 API 唯一用例入口。
- [ ] 增加最小 FastAPI：创建 Run、查询 Run、SSE 事件、取消调查。
- [ ] CLI 改为订阅同一事件协议；本地自动启动 MCP 仅是 CLI 开发便利设施。
- [ ] 支持 SSE 断线后按最后事件序号续传，慢客户端不得阻塞调查执行。
- [ ] JSON Run 文件降为导出产物，不再是唯一事实源。

### 主要文件

- 重构：`src/hinataops/agent_core/workflow.py`
- 重构：`src/hinataops/investigation_service.py`
- 重构：`src/hinataops/agent_core/events.py`
- 简化：`src/hinataops/cli.py`
- 新增：Application Service、Repository、HTTP API/SSE Adapter

验收：CLI 与 API 发起相同用例并获得同构事件；客户端断开不取消 Run；进程重启后等待审批状态和历史事件仍在；
相同幂等键不创建重复调查。

## 10. 阶段 M7：人工审批动作闭环

目标：把已经存在的动作账本接入真实调查，而不是作为孤立 P2 能力。

### 实施

- [ ] 报告只能产生 `ActionProposal`，不能直接调用写 Tool。
- [ ] 用 `awaiting_approval/approved/rejected` 等动作状态表达审批，不向调查 `ToolResult` 增加
      `approval_required`。
- [ ] Proposal 包含依据 Evidence、目标、参数、风险、前置条件、验证方式和回退说明。
- [ ] Policy Engine 确定性校验动作类型、环境、服务和审批要求。
- [ ] 复用现有审批指纹、原子 claim 和状态转换；增加操作者与审计上下文。
- [ ] CLI/API 展示计划并支持批准或拒绝；默认拒绝超时或计划已变化的审批。
- [ ] 执行器使用幂等键，区分“命令未执行”“执行结果未知”“执行失败”。
- [ ] 动作完成后调用独立验证 Tool，验证结果追加为新的 Evidence。
- [ ] 恢复验证失败时只提出回退建议；自动回退需单独策略和审批，不默认启用。
- [ ] 为拒绝、重复审批、并发 claim、重启恢复、执行超时和验证失败补测试。

### 主要文件

- 保留并扩展：`src/hinataops/actions/models.py`
- 保留并扩展：`src/hinataops/actions/repository.py`
- 演进：`src/hinataops/ops_mcp/actions/*`
- 迁移：`src/hinataops/ops_mcp/toolsets/aoi_learn_judge/restart.py`
- 接入：外层 Workflow、Application Service、CLI/API

验收：一次真实 AoiLearn Worker 故障能够完成调查、提议、人工批准、容器恢复、指标/队列复核和全链路审计；
未批准、指纹不匹配或超出白名单时不能执行。

## 11. 阶段 M8：评测、平台可观测性与第二接入

目标：用数据证明 V3 改造有效，并证明通用边界不是文档承诺。

### 实施

- [ ] 将 Tool 结果 Fixture 与模型响应 Fixture 分离，支持纯确定性回放。
- [ ] 至少建立三类 AoiLearn 场景：健康/证据不足负例、单组件不可用、跨链路任务无结果。
- [ ] 每个场景定义必要 Evidence、可选 Evidence、禁止 Tool/动作、预算和 Ground Truth。
- [ ] 建立模型矩阵，至少覆盖当前 DeepSeek 与一种不同 Tool Calling 能力配置。
- [ ] 输出 Top-1、Evidence Coverage、无依据断言、重复调用、完成率、Token、延迟和费用。
- [ ] 用 OpenTelemetry 记录 Run、模型步骤、Tool、外部依赖、压缩和动作 Span。
- [ ] 为第二 Environment Pack 运行至少一个只读调查，证明 Core 无改动。
- [ ] 以 M3 建立的首个原生 Tool Calling 基线为起点比较后续阶段，并把回归阈值接入测试或 CI。
- [ ] 更新演进日志，记录未解决问题和后续是否需要 Runbook RAG。

验收：V3 不再出现 Planner JSON 契约失败；关键证据覆盖不低于基线；没有新增未授权调用；第二接入通过；真实
动作闭环可重复；失败可以由事件、Span 和归档定位到模型、策略、Provider、Tool 或目标系统。

## 12. 推荐提交边界

每个提交只跨越一个可验证责任：

1. Tool 契约与 MCP 兼容实现；
2. Toolset Manifest/Registry；
3. Model Gateway 原生 Tool Calling；
4. Investigation Loop 与 V2 对照；
5. Artifact/Evidence；
6. Context Manager；
7. 通用 Toolset 与 Aoi Environment Pack；
8. Run/Event 持久化；
9. HTTP/SSE 与 CLI 适配；
10. 动作审批闭环；
11. 多场景评测和第二接入。

禁止把“删除 V2 + 引入新循环 + 拆 Toolset + 新增 API”合并为一个无法定位回归的大提交。

## 13. Definition of Done

每个迁移项只有同时满足以下条件才可勾选：

- 有失败用例或契约测试说明为什么修改；
- 新实现测试通过，并保留必要的旧行为回归；
- CLI 或 API 的用户可见变化已记录；
- 不新增 AoiLearn 到 Core 的反向依赖；
- 错误、Token、敏感信息和 Artifact 不被无界写入日志；
- 对应架构或演进文档已更新；
- `uv run pytest`、Python 编译和 `git diff --check` 通过；
- 如影响调查行为，已重新运行至少一个负例和一个受控正例。
