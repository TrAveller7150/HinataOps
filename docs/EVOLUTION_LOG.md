# HinataOps 演进日志

本文件记录后续架构和实现改进的背景、方法、验证结果与残余边界；它不同于单次运行的
`docs/evaluations/` 测试记录，关注“为什么改变系统”。

## E-001：Tool 超时可观测性与 Prometheus 子超时

### 原因

受控故障注入期间，Docker 与 Prometheus Tool 曾同时返回笼统的 `target_unavailable`。MCP Server 日志
显示真实原因是 `SSH command timed out`，但原始错误契约无法区分命令超时、远端命令失败和输出超限。
Prometheus Tool 在一条 SSH 会话中串行执行七个 `curl` 查询，单条请求没有独立超时，可能耗尽整体预算。

### 方法

- 将 SSH 失败细分为 `ssh_command_timeout`、`remote_command_failed` 与
  `output_budget_exceeded`，对外保持脱敏消息并明确可否重试。
- 为每条固定 Prometheus `curl` 增加 1 秒连接超时与 1.5 秒总时限，确保七条查询不会由单条卡顿占满
  15 秒 Tool 预算。
- 保留 Docker 全量快照：恢复后实测其耗时仅 0.388 秒、输出约 95 KB，当前缺少证据证明它是稳定瓶颈。

### 结果

Tool 错误现在能把“瞬时 SSH 命令超时”准确传递给 Agent 的受控重试机制；Prometheus 查询拥有明确的
子级超时边界。Docker 全量快照的裁剪暂不实施。

### 验证与边界

本轮以单元测试验证错误映射和 Prometheus 命令参数；真实受控故障场景尚需在后续运行中验证新的
`ssh_command_timeout` 分类与重试记录。单条 Prometheus 查询超时后，现有固定响应解析会把该 Tool 降级
为部分证据，而不会伪造缺失指标为健康状态。

已在当前 `aoi-local` 环境进行一次只读真实验证：新命令可在 `0.919s` 内完整解析两个 Judge Worker 的
7 条 Prometheus 响应。初次原始命令采样曾耗时约 5 秒，但未超过 15 秒总预算；仍需在后续多次采样中记录
延迟分布，而不能据此宣称瞬时超时已被消除。

## E-002：单窗口人工调查 CLI 与可读过程输出

### 原因

此前真实评测要求用户在一个终端手动启动 Streamable HTTP MCP Server，再在另一个终端运行
`evaluation_runner`；后者把完整 JSON 直接打印到控制台。前者增加了启动步骤，后者把用于归档、评测的
机器格式误当成面向运维人员的交互界面。

### 方法

- 新增 `hinataops investigate python-judge-worker-unavailable`。未指定 `--mcp-url` 时，它在当前 Python
  环境启动本地 MCP 子进程，以实际 `list_tools` 成功作为就绪条件，并在任何退出路径回收该子进程。
- 若用户显式提供 `--mcp-url`，CLI 只连接已有 Server，绝不终止其生命周期，保留开发调试与未来远端部署
  的入口。
- 为 Investigation Workflow 增加仅通知的事件出口。展示层可接收 Tool Catalog、计划采证、开始采证、
  单项观测完成和诊断阶段事件；监听器异常被隔离，不能改变 Planner、预算或 Tool 执行。
- 使用 Rich 渲染中文进度与最终报告；完整 JSON 默认以 UTF-8 写入 `.hinataops/runs/`，而不是输出到终端。
  `evaluation_runner` 保持原有 JSON 基线用途不变。

### 结果

人工调查由两个窗口和冗长 JSON 收敛为一条命令、过程进度和一份可定位的审计记录。MCP 仍是独立 HTTP
信任边界，因此将来 FastAPI/SSE 展示层可以复用相同 Agent Core 与 MCP Server，而无需改写 Tool 层。

### 验证与边界

新增 CLI 参数、默认归档路径和工作流事件隔离测试；全量测试为 65 项通过。本地冒烟检查已确认
“启动 MCP → `list_tools` 就绪 → 退出回收”可用，未调用 LLM 或远程基础设施。当前人工命令仅开放已有
真实基线的 Python Judge Worker 场景；它尚不是可自由输入任意事故描述的通用对话 CLI，也没有实时取消、
历史检索或人工审批操作入口。

## E-003：评测 Ground Truth 与人工调查解耦

### 原因

首次人工 CLI 直接复用了 `python_judge_worker_unavailable` 评测场景。尽管 Planner 只收到事故描述，
诊断器仍收到该场景的唯一允许根因码。这把评测期望答案带入了人工调查，且场景名容易让使用者误以为
Agent 已预设“Worker 不可用”。同时，CLI 仅显示 Core 生成的通用 `complete` 摘要，隐藏了 Prometheus、
Docker 和 Redis 观测中的实际指标与模型候选假设。

### 方法

- 新增不含 Ground Truth 的 `python_judge_task_no_result` 事故画像；每次运行生成新的 Incident ID。人工
  CLI 只使用事故描述、只读 Tool 白名单与预算，诊断器不再接收预期根因码。
- 保留 `EvaluationScenario` 与 `evaluation_runner` 原有逻辑：仅评测入口把 expected cause code 作为
  评分约束传入，评测与人工审计记录分离。
- 在 AoiLearn Plugin 新增 Evidence Presenter，将领域结构化数据压缩为有界关键指标：Worker 可达性和
  沙箱池、Judge 容器状态、Redis consumers/lag/pending 及流水线聚合。Agent Core 仍只产出通用 Observation。
- CLI 新增“模型假设评估”面板，显示候选/主根因、置信度、支持与反驳证据、待补充事实；并把预算耗尽
  明确标成“预算受限结束”。人工画像的 Tool 预算从 4 调整为 5，使四次采证后模型能主动结束。

### 结果

健康环境中的人工调查现在应明确显示“Worker 不可用”只是被证据反驳的低置信候选，而不是预设或确认的
根因；过程输出也会展示实际指标。评测仍可验证受控故障注入时是否命中 `judge_worker_unavailable`，但该
答案不会进入人工调查 Prompt 或归档 JSON。

### 验证与边界

新增领域展示器、人工归档不含 `evaluation`/`expected_primary_cause_code`、候选假设渲染等测试；全量测试
为 69 项通过，并完成 CLI 帮助命令与 Python 编译检查。尚未用本轮代码发起真实 LLM 调查，避免未经用户
确认消耗 API 额度；下一步应在健康负例和受控 Worker 停止正例各运行一次，观察模型是否主动结束及指标
展示是否足以让人工复核。

## E-004：可审计调查决策轨迹与分批采证

### 原因

人工 CLI 虽已展示 Tool 结果，但只能看到“计划采证”和最终报告，无法判断模型为什么选择某项检查、是否在
新证据出现后改变路径，或为什么停止。一次真实运行还出现 `LLM 返回内容不是 JSON`：错误仅在最终停止原因
中可见，难以定位是 Planner 契约失败还是基础设施观测失败。

### 方法

- 定义 `PlanningTrace`：每轮保存状态（已规划/主动结束/规划失败）、受限决策摘要、至多两个 Tool 调用与
  结束原因。它保存的是模型面向人工的简短决策摘要，不是不可审计的内部思维链。
- Planner 的结构化契约新增必填 `decision_summary`，要求陈述已知事实、仍缺证据与本轮选择原因；每轮最多
  两项 Tool，使下一轮确实能使用上一批观测，而非一次并发穷举全部检查。
- 规划失败立即发布事件并写入运行记录，再降级为基于现有证据的诊断；终端最终显示完整“调查决策轨迹”。
- 对 JSON Object 模式返回的单层 Markdown JSON 代码围栏进行严格剥离后再 `json.loads`，兼容供应商偶发的
  围栏包装；其他非 JSON 内容仍按契约失败处理，不做猜测性修复。

### 结果

终端现在可按轮次阅读“模型决策摘要 → 受控采证 → 新观测 → 下一轮决策”，归档 JSON 同时保留该轨迹。
如果 Planner 再次产生无效结构化输出，用户会在发生时看到明确的 `规划失败`，并能从运行记录区分其与
MCP Tool 超时、部分证据或诊断器失败。

### 验证与边界

新增 PlanningTrace、Planner 输出契约、规划失败降级和 Markdown 围栏 JSON 兼容测试；全量测试为 70 项通过，
并完成编译与 CLI 帮助检查。尚未运行真实 LLM，以免擅自消耗 API 额度。该轨迹是模型受 Schema 约束生成的
可审计摘要，不应被当作模型全部内部推理或绝对可信事实；事实仍以 Observation 与其来源 Tool 为准。
