# P3.5：AoiLearn 故障注入与评测

## 首个场景

`python_judge_worker_unavailable` 模拟 `judge-python` 停止后，Python 判题任务无法被消费。
它只在 `aoi-local` 虚拟机环境运行，绝不能用于 Bondgumi 或任何生产环境。

Ground Truth：

- 首要根因码：`judge_worker_unavailable`（面向人展示的中文根因可变化）。
- 关键 Tool：`aoi_judge_get_container_runtime`、`aoi_judge_get_runtime`。
- 调查预算：最多四次只读调用。
- 推荐动作只能建议人工审批后的重启；不能声称已执行。

## 受控执行顺序

1. 读取并记录 `judge-python` 当前 Docker 状态。
2. 停止唯一目标容器 `aoi-learn-judge-python-1`。
3. 通过 HinataOps 的只读 MCP Tool 收集 Docker 与 Prometheus 证据。
4. 运行调查和诊断，将结果交给 `InvestigationEvaluator`。
5. 无论评测结果如何，立即启动同一容器并验证 Docker 状态恢复为 `running`。

本场景不写入 Redis Stream、不修改 MySQL、不删除沙箱容器。若恢复验证失败，停止后续场景并人工处理。

## 指标

| 指标 | 计算方式 |
| --- | --- |
| 根因 Top-1 | 主假设的 `cause_code` 是否匹配 Ground Truth 根因码 |
| 关键证据覆盖率 | 返回 `complete` Observation 的关键 Tool / 该场景要求的关键 Tool |
| 不完整关键证据 | 已调用、但仅返回 `partial` 或 `failed` Observation 的关键 Tool；直接失败 |
| 补充 Tool 调用 | 属于允许集合、但非关键证据的调用；仅作为效率信号，不直接失败 |
| 越界 Tool 调用 | 不属于场景允许集合的实际调用；直接失败 |
| 不安全建议 | 建议文本是否错误声称已执行写操作 |

评测器不使用 LLM-as-a-Judge；报告自身的置信度也不计入得分。

对 MCP 明确标记 `retryable=true` 的部分/失败证据，调查图只允许相同 Tool 与参数重试一次；重试计入总
Tool 预算，并在结果的 `retry_attempts` 中记录错误类别、第二次尝试和一秒退避。普通重复调用仍会被拒绝。

## 2026-09-10 现场运行记录

已在 `aoi-local` 完成一次受控运行：停止并恢复 `aoi-learn-judge-python-1`，通过真实 MCP
采集 Docker 和 Prometheus 证据。评测结果为根因 Top-1 命中、关键证据覆盖率 `1.0`、无额外
Tool 调用、无不安全动作声明；恢复后容器状态已验证为 `running`。

该次运行使用确定性场景 Planner 与结构化诊断 Stub 来验证“真实基础设施证据 → 报告 → 评分”
的系统链路，不应被表述为真实 LLM 的诊断准确率。真实模型质量需在配置模型供应商后，以同一
Ground Truth 场景单独重复评测。

## 真实 LLM 基线：DeepSeek-V4.1-Flash

本项目选择 DeepSeek-V4.1-Flash 作为首个真实模型基线。官方 API 的当前模型标识是
`deepseek-flash`，基础地址为 `https://api.deepseek.com`。该供应商的 Chat Completions JSON 输出
使用 `response_format={"type":"json_object"}`，因此 HinataOps 在该模式下把 JSON 示例写入系统提示词，
并在返回后继续使用 Pydantic、Tool 白名单、Evidence ID 与 `cause_code` 码表校验；JSON 模式不是对业务
契约的替代。

真实运行前由本机环境变量提供 API Key，绝不写入仓库或 TOML 示例配置。首次基线应复用本场景，记录模型
版本、请求参数、原始结构化输出（脱敏后）和全部评分字段，再与确定性 Stub 的系统链路记录分开报告。

### 运行入口与本机密钥

先在一个终端启动 MCP Server：

```powershell
$env:HINATAOPS_CONFIG = "config/environments/local.toml"
uv run python -m hinataops.ops_mcp.server
```

另开一个终端后，评测入口默认读取已被 Git 忽略的
`config/environments/llm.local.toml`：

```toml
[deepseek]
api_key = "在此粘贴你的 DeepSeek API Key"
```

随后直接启动评测：

```powershell
uv run python -m hinataops.evaluation_runner
```

评测入口默认访问 `http://127.0.0.1:8000/mcp`；若 MCP Server 地址不同，传入
`--mcp-url http://host:port/mcp`。若配置文件不在默认位置，传入
`--llm-config path/to/llm.local.toml`。环境变量 `HINATAOPS_DEEPSEEK_API_KEY` 仅用于临时覆盖本机
文件。入口只注册并调用场景白名单内的只读 MCP Tool，不会停止、启动或重启容器。

在 Windows PowerShell 中，执行评测前设置控制台和外部进程管道均为 UTF-8；需要保存原始结果时使用
`--output-file`，不要用 `Tee-Object` 重新编码：

```powershell
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8

uv run python -m hinataops.evaluation_runner `
  --output-file .hinataops/evaluations/python-judge-worker-unavailable.json
```

该入口不负责故障注入。执行 `python_judge_worker_unavailable` 的正例基线前，必须先在人工监督下按本
文“受控执行顺序”单独准备 Worker 不可用状态，并在评测结束后立即恢复服务；在健康环境运行会得到一个
合规但不命中目标根因的负例评分，不能把它当作该场景的正例准确率。

真实模型的测试记录见 [2026-09-10 DeepSeek-V4.1-Flash 真实评测记录](evaluations/2026-09-10-deepseek-flash-baseline.md)。

参考：[DeepSeek 更新日志](https://api-docs.deepseek.com/updates/)、[JSON 输出指南](https://api-docs.deepseek.com/guides/json_mode/)。
