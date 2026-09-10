# HinataOps

HinataOps 是一个面向多服务系统的证据驱动运维 Agent Demo。它以可复用的调查引擎为核心，
通过 MCP Plugin 接入具体系统；AoiLearn 判题系统是当前唯一的深度参考集成与故障实验环境。

项目不试图替代 Prometheus、Grafana、Sentry 或 CloudWatch。它解决的是跨数据源调查：从事故描述
出发，受预算地收集事实，形成可追溯诊断报告，并在人工监督下提出有限处置建议。

详细设计见 [架构文档](docs/ARCHITECTURE.md)。

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

```powershell
uv run pytest
```

真实 DeepSeek 评测入口及 API Key 的本机填写方式见 [P3.5 评测文档](docs/P3_5_EVALUATION.md)。

## 当前边界

- 当前没有 CLI、FastAPI 或 React 展示层；下一阶段先建立真实 LLM 评测基线。
- 当前没有 Runbook RAG；仅在评测证明缺少运行知识是主要失败原因时再引入。
- 通用 Toolset 不接受任意 Shell、SQL、Redis 命令或 PromQL；新业务系统通过新领域 Plugin 接入。
- AoiLearn 是参考集成，不是 Agent Core 的内置领域。
