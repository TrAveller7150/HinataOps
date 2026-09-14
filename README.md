# HinataOps

HinataOps 是一个**面向生产级通用 SRE 调查平台的最小可信实现**：以可复用的调查引擎为核心，
通过 Toolset 与 MCP 等接入方式连接不同系统，在人工监督下完成跨数据源取证、根因分析与受控处置。

当前仓库仍处于学习与验证阶段，不宣称已经具备完整生产可用性。AoiLearn 判题系统是首个深度参考集成、
故障实验环境与端到端验收场，而不是 HinataOps 的产品边界。项目会参考 HolmesGPT 等成熟 SRE Agent 的
分层思想和调查循环，同时保留自身的证据溯源、细粒度策略控制与 MCP 隔离设计。

项目不试图替代 Prometheus、Grafana、Sentry 或 CloudWatch。它解决的是跨数据源调查：从事故描述
出发，受预算地收集事实，形成可追溯诊断报告，并在人工监督下提出有限处置建议。

目标设计见 [V3 目标架构](docs/ARCHITECTURE_V3.md)，当前代码的迁移顺序见
[V3 迁移清单](docs/V3_MIGRATION_CHECKLIST.md)。[V2 架构](docs/ARCHITECTURE.md)仅保留为当前实现快照。

## 当前已实现

- Ops MCP Server 通过 Plugin 发现并装配领域 Toolset。
- AoiLearn Judge Plugin 提供 Docker、Redis Stream、MySQL 流水线与 Prometheus 运行状态的受审核查询。
- Agent Core 通过 Streamable HTTP 作为 MCP Client；不持有 Docker、SSH、Redis 或数据库凭证。
- LangGraph 调查循环强制只读白名单、重复调用限制、轮次与总调用预算。
- LLM Planner 与诊断器使用严格 JSON Schema 或供应商 JSON 模式，并由 Pydantic 二次校验；报告只能引用本次收集的 Evidence ID。
- P2 动作账本提供人工审批后的受控 Docker 重启，默认不启用。
- P3.5 提供 Ground Truth 驱动的故障评测；已完成一次 `judge-python` 停止的真实、可恢复注入。

## 本地运行

将 `config/environments/aoi-local.example.toml` 复制为本机忽略的 `local.toml`，填写本机私钥路径后：

```powershell
uv sync --all-groups
uv run python -m hinataops.ops_mcp.server
```

MCP Streamable HTTP 地址为 `http://127.0.0.1:8000/mcp`。

面向人工的单窗口调查会自动启动并在结束后回收本地 MCP Server，完整 JSON 记录默认保存到
`.hinataops/runs/`，终端展示每轮可审计决策摘要、关键观测、最终报告与模型假设评估：

```powershell
uv run hinataops investigate python-judge-task-no-result
```

如需复用已经手动启动的 MCP Server，可显式指定地址；此时 CLI 不管理该 Server 的生命周期：

```powershell
uv run hinataops investigate python-judge-task-no-result --mcp-url http://127.0.0.1:8000/mcp
```

```powershell
uv run pytest
```

真实 DeepSeek 评测入口及 API Key 的本机填写方式见 [P3.5 评测文档](docs/P3_5_EVALUATION.md)。

## 当前实现边界

- 当前提供面向单个事故画像的人工调查 CLI；它不携带评测预期根因，尚未提供 API、事件流或 Web 展示层。
- 当前没有 Runbook RAG；仅在评测证明缺少运行知识是主要失败原因时再引入。
- 当前 Toolset 不向模型开放任意 Shell、SQL、Redis 命令或网络请求；通用只读查询能力及其策略边界将在
  V3 架构中重新定义。
- AoiLearn 是参考集成，不是 Agent Core 的内置领域。
