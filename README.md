# HinataOps

HinataOps 是一个面向 AoiLearn 的证据驱动运维 Agent Demo。第一阶段先将真实基础设施观测能力封装为只读 MCP Tool，再接入调查编排与 LLM。

## P1.5：领域 Toolset 与受控访问边界

当前 MCP Server 将“如何访问基础设施”和“如何解释 AoiLearn 判题领域事实”分离：

- `topology_get_service`：返回某个 AoiLearn 服务的职责和依赖关系。
- `aoi_judge_get_container_runtime`：经 SSH 读取 Docker 状态，区分判题相关服务、活动/历史沙箱池，以及其他项目容器。
- `aoi_judge_get_stream_summary(language)`：返回受审核 Stream 的历史长度、Consumer Group lag 和 pending 数量。
- `aoi_judge_get_pipeline_summary(window_minutes)`：返回 5、15 或 60 分钟内的任务与 Outbox 状态聚合、任务年龄和投递重试信号。
- `aoi_judge_get_runtime()`：返回 Python/SQL Judge 的可达性、沙箱池容量、扩容暂停状态和主机资源指标。

`ops_mcp/adapters/` 提供 Docker、Redis、MySQL 与 Prometheus 的实例级只读访问；
`ops_mcp/toolsets/aoi_learn_judge/` 仅保留判题业务语义；`ops_mcp/policy/` 将输出预算和实例访问入口固定在环境配置中。通用查询 Toolset 尚未开放，因而不存在任意 SQL、Redis 命令、PromQL 或远端命令入口。

本地运行前，将 `config/environments/aoi-local.example.toml` 复制为 `local.toml`，并填入本机 SSH 私钥路径。实际配置已被 Git 忽略；也可通过 `HINATAOPS_CONFIG` 指定其他环境文件。

```powershell
uv sync --all-groups
uv run python -m hinataops.ops_mcp.server
```

MCP Streamable HTTP 地址为 `http://127.0.0.1:8000/mcp`。

```powershell
uv run pytest
```

所有领域 Tool 均返回统一的采集环境、UTC 时间、完整性标记、警告和结构化错误；不接受任意 Redis 键、SQL、PromQL 或远端命令。

`history_length` 是 Redis Stream 的历史保留消息数，不等于积压。判断消费滞后应使用 Consumer Group 的 `lag` 与 `pending`。

## P2：人工审批的受控重启

P2 增加 SQLite 动作账本。动作计划包含目标服务、理由、风险、回滚方案和预先声明的恢复检查；人工审批必须提交与计划内容一致的 SHA-256 指纹。MCP 写 Tool 只接受 `action_id`，并且只能一次性领取已批准的计划，因此不会接受容器名、Shell 命令或可替换的执行参数。

`docker_restart_service` 默认不注册。若要在故障注入环境演示它，需在本机忽略的环境配置中显式设置：

```toml
[actions]
enabled = true
allowed_services = ["judge-python"]
```

执行结果不能直接视为恢复；Tool 会重新检查容器运行状态，并对 Judge 服务检查 Prometheus `up` 指标。动作账本默认位于 `.hinataops/state.sqlite3`，可通过 `HINATAOPS_STATE_DB` 指定给审批端与 MCP Server 共享的路径。

## P3.3：受预算的调查工作流

`agent_core/` 已接入 LangGraph 的最小调查循环：加载当前 MCP Tool Catalog、由 Planner 选择下一轮只读检查、由确定性策略校验白名单/重复调用/轮次和总调用预算、并发采集证据，再决定继续或结束。

可脚本化 Planner 用于验证编排边界；P3.4 已在下一节提供 LLM 实现。每次运行均保留已完成调用、结构化 Observation 和明确的停止原因；拒绝的调用不会到达 MCP Server。

## P3.4：结构化 LLM Planner

`LlmInvestigationPlanner` 通过独立的 `StructuredOutputClient` 端口调用模型；当前提供
`OpenAICompatibleStructuredOutputClient` 实现，可在构造时显式传入模型名、API Key 和兼容 API 的 `base_url`。调查图仍只依赖 `InvestigationPlanner`，不依赖具体模型 SDK。

模型只能看到事故、已采集证据和经白名单筛选的只读 Tool 描述。它通过严格 JSON Schema 返回检查建议；动态 Tool 参数以 `arguments_json` 字符串返回，Core 解析为 `ToolCall` 后仍会校验 Tool Catalog、重复调用和预算。模型输出不合法或选择未授权 Tool 时，工作流安全结束，不会调用 MCP Tool。

## P3.4B：证据约束的诊断报告

调查图的所有停止路径都会进入诊断节点。`LlmInvestigationDiagnostician` 只能引用本次
`Observation` 的 Evidence ID，并由 Core 将输出重建为 `Hypothesis` 与 `InvestigationReport`。
报告引用不存在的证据、缺少主假设或结构不合法时不会产生诊断结论；系统改为输出确定性的
`inconclusive` 报告，保留已采集证据和停止原因。`recommended_action` 仅为人工建议，不能执行写操作。
