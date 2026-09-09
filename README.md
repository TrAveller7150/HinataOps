# HinataOps

HinataOps 是一个面向 AoiLearn 的证据驱动运维 Agent Demo。第一阶段先将真实基础设施观测能力封装为只读 MCP Tool，再接入调查编排与 LLM。

## P0：只读基础设施观测

P0 提供两个 Tool：

- `topology_get_service`：返回某个 AoiLearn 服务的职责和依赖关系。
- `docker_list_services`：经 SSH 读取 Docker 状态，区分配置服务、活动/历史沙箱池，以及其他项目容器。

本地运行前，将 `config/environments/aoi-local.example.toml` 复制为 `aoi-local.toml`，并填入本机 SSH 私钥路径。实际配置已被 Git 忽略。

```powershell
uv sync --all-groups
uv run python -m hinataops.mcp_server
```

MCP Streamable HTTP 地址为 `http://127.0.0.1:8000/mcp`。

```powershell
uv run pytest
```

## P1：判题流水线多源证据

P1 在 P0 基础上增加三个面向判题流水线的只读 Tool：

- `redis_get_judge_stream_summary(language)`：返回受审核 Stream 的历史长度、Consumer Group lag 和 pending 数量。
- `mysql_get_judge_pipeline_summary(window_minutes)`：返回 5、15 或 60 分钟内的任务与 Outbox 状态聚合、任务年龄和投递重试信号。
- `prometheus_get_judge_runtime()`：返回 Python/SQL Judge 的可达性、沙箱池容量、扩容暂停状态和主机资源指标。

它们均返回统一的采集环境、UTC 时间、完整性标记、警告和结构化错误；不接受任意 Redis 键、SQL、PromQL 或远端命令。

`history_length` 是 Redis Stream 的历史保留消息数，不等于积压。判断消费滞后应使用 Consumer Group 的 `lag` 与 `pending`。
