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
