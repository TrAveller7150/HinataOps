# HinataOps 架构 V2

## 1. 定位

HinataOps 是一个可扩展的证据驱动运维 Agent Demo：通用调查引擎负责“如何调查”，领域 Plugin
负责“调查哪个系统、能读取哪些事实、如何评测”。AoiLearn 判题流水线是第一个深度参考集成，
用于真实 MCP 联调和可恢复故障注入；它不是 Core 的内置业务领域。

项目目标是展示一条可信闭环：

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
| LLM 接口 | OpenAI-compatible + 严格 JSON Schema | 可替换提供方，结构由程序验证 |
| 首个交付界面 | CLI 优先 | 先验证调查价值，再增加 Web 展示层 |
| 演进依据 | Ground Truth 评测 | 不用“看起来聪明”代替可量化质量 |

FastAPI、React、LangGraph checkpoint、Runbook RAG、通用探索 Toolset 均不是当前既定实现；只有当评测或
演示需求证明其必要时才进入路线图。

## 3. 组件与依赖边界

```text
┌──────────────────────────────────────────────────────────────┐
│ Application Shell（规划中：CLI，后续可接 Web）                 │
│  创建事故、选择 Investigation Profile、展示报告与评测结果      │
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

当前代码已经满足前两层 MCP Plugin 解耦；`agent_core/evaluation.py` 中仍有一个 AoiLearn 场景常量，
下一次重构应将它迁到 AoiLearn 领域侧，以完成该边界。

## 5. 调查与报告流程

```text
load_catalog → plan → authorize → execute_readonly_tools
                    ↑                  ↓
                    └──── 未达预算 ────┘
                                      ↓
                                  diagnose → report
```

- Planner 只能建议下一批只读 Tool；Core 校验 Tool Catalog、白名单、同参重复、轮次和总调用预算。
- MCP 返回被转换为带来源、时间、完整性和 Evidence ID 的 Observation。
- 诊断器只可引用本次 Observation 的 Evidence ID；不存在的引用会被领域模型拒绝。
- 模型不可用或报告不合法时，系统返回保留事实与停止原因的 `inconclusive` 报告。
- `recommended_action` 只是人工建议。真正的写操作仍需经过 P2 动作账本与审批指纹。

面向模型和用户的自然语言统一使用简体中文；Tool 名、JSON 字段、Evidence ID 与状态枚举保持英文
稳定标识，并在首次出现时以中文说明。

## 6. 评测优先的演进方式

Agent 质量由独立 Ground Truth 评测，不由模型自评或置信度决定。每个领域场景定义：

- 事故入口；
- 预期首要根因的匹配模式；
- 必须获取的关键 Tool 证据；
- 最大 Tool 调用次数；
- 可接受的人工建议边界。

当前 P3.5 已实现并验证 `python_judge_worker_unavailable`：真实 AoiLearn 容器短暂停止、MCP
采证、报告生成、评分、容器恢复。该现场运行使用结构化 Stub 验证系统闭环；真实 LLM 基线仍待建立。

## 7. 近期路线图

1. 将 AoiLearn 场景与 Ground Truth 从 `agent_core` 迁入领域 Plugin，并定义最小 Investigation Profile 契约。
2. 配置一个真实 OpenAI-compatible 模型，运行第一个真实 LLM 评测基线。
3. 增加 Redis 消费异常与沙箱容量受限两个场景，采用 Fixture 与安全真实注入混合方式。
4. 根据评测失败决定是否引入领域 Tool、Runbook RAG、假设更新或 CLI 展示。
5. 至少三类场景稳定后，完成 CLI；Web 与 checkpoint 仅在演示需要时实现。

## 8. 非目标

- 不提供任意 Shell、SQL、Redis 命令、PromQL 或网络请求给模型。
- 不自动执行生产环境动作。
- 不把 AoiLearn 的服务名、Redis Key、表名或指标名写入 Agent Core。
- 不在缺少评测证据时堆叠多 Agent、RAG、Web 前端或微服务。
