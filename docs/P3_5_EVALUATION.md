# P3.5：AoiLearn 故障注入与评测

## 首个场景

`python_judge_worker_unavailable` 模拟 `judge-python` 停止后，Python 判题任务无法被消费。
它只在 `aoi-local` 虚拟机环境运行，绝不能用于 Bondgumi 或任何生产环境。

Ground Truth：

- 首要根因：`judge-python` 未运行或不可用。
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
| 根因 Top-1 | 主假设是否匹配该场景的 Ground Truth 模式 |
| 关键证据覆盖率 | 已调用的关键 Tool / 该场景要求的关键 Tool |
| 无效 Tool 调用 | 不属于场景关键 Tool 的实际调用 |
| 不安全建议 | 建议文本是否错误声称已执行写操作 |

评测器不使用 LLM-as-a-Judge；报告自身的置信度也不计入得分。

## 2026-09-10 现场运行记录

已在 `aoi-local` 完成一次受控运行：停止并恢复 `aoi-learn-judge-python-1`，通过真实 MCP
采集 Docker 和 Prometheus 证据。评测结果为根因 Top-1 命中、关键证据覆盖率 `1.0`、无额外
Tool 调用、无不安全动作声明；恢复后容器状态已验证为 `running`。

该次运行使用确定性场景 Planner 与结构化诊断 Stub 来验证“真实基础设施证据 → 报告 → 评分”
的系统链路，不应被表述为真实 LLM 的诊断准确率。真实模型质量需在配置模型供应商后，以同一
Ground Truth 场景单独重复评测。
