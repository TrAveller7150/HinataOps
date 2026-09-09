from __future__ import annotations

import json
from collections import defaultdict

from pydantic import BaseModel, Field

from hinataops.ops_mcp.adapters.prometheus import PrometheusReadonlyAdapter
from hinataops.ops_mcp.config import JudgeStreamConfig
from hinataops.ops_mcp.contracts import ObservationMetadata, new_metadata
from hinataops.ops_mcp.toolsets.aoi_learn_judge.stream_summary import JudgeLanguage


class JudgeRuntimeSnapshot(BaseModel):
    """一个 Judge Worker 的 Prometheus 即时运行与沙箱池容量证据。"""

    language: JudgeLanguage = Field(description="判题语言")
    instance: str = Field(description="Prometheus 中对应的 Judge 实例标签")
    up: bool | None = Field(description="Prometheus 最近一次抓取是否成功")
    pool_active: float | None = Field(description="当前沙箱池容器总数")
    pool_available: float | None = Field(description="当前可复用的空闲沙箱数")
    pool_creating: float | None = Field(description="正在创建的沙箱数")
    expand_paused: bool | None = Field(description="资源阈值是否暂停沙箱扩容")
    host_cpu_percent: float | None = Field(description="Worker 观测到的主机 CPU 使用率")
    host_memory_percent: float | None = Field(description="Worker 观测到的主机内存使用率")


class PrometheusJudgeRuntimeSummary(BaseModel):
    """Prometheus 返回的所有受审核 Judge Worker 即时运行证据。"""

    metadata: ObservationMetadata
    runtimes: list[JudgeRuntimeSnapshot]


class PrometheusJudgeRuntimeInspector:
    """通过固定 PromQL 汇总 Judge Worker 的可用性与沙箱池饱和度。

    PromQL 不作为 Tool 参数暴露。指标集合经过审核，只包含判题流水线区分根因所需的
    Worker 可达性、池容量、扩容暂停和主机资源信号。
    """

    _QUERIES = (
        'up{job="judge"}',
        "aoilearn_judge_pool_active",
        "aoilearn_judge_pool_available",
        "aoilearn_judge_pool_creating",
        "aoilearn_judge_expand_paused",
        "aoilearn_judge_host_cpu_percent",
        "aoilearn_judge_host_memory_percent",
    )

    def __init__(
        self,
        environment_name: str,
        prometheus: PrometheusReadonlyAdapter,
        streams: list[JudgeStreamConfig],
    ) -> None:
        self._environment_name = environment_name
        self._prometheus = prometheus
        self._streams = list(streams)

    async def summary(self) -> PrometheusJudgeRuntimeSummary:
        """读取受审核指标，并按 Python/SQL Judge 实例归类。"""
        values_by_instance: dict[str, dict[str, float]] = defaultdict(dict)
        responses = self._query_responses(
            await self._prometheus.query_many_fixed(self._QUERIES)
        )
        for response in responses:
            for result in response["data"]["result"]:
                metric = result["metric"]
                instance = metric.get("instance")
                metric_name = metric.get("__name__")
                if instance and metric_name:
                    values_by_instance[instance][metric_name] = float(result["value"][1])

        runtimes = [
            self._runtime_for_stream(stream, values_by_instance.get(stream.prometheus_target, {}))
            for stream in self._streams
        ]
        return PrometheusJudgeRuntimeSummary(
            metadata=new_metadata(self._environment_name, "prometheus"),
            runtimes=runtimes,
        )

    @staticmethod
    def _query_responses(output: str) -> list[dict]:
        """解析一条远端命令分行返回的多个 Prometheus JSON 响应。"""
        responses = [json.loads(line) for line in output.splitlines() if line.strip()]
        if len(responses) != len(PrometheusJudgeRuntimeInspector._QUERIES):
            raise ValueError("Prometheus 返回的指标响应数量不完整")
        if any(response.get("status") != "success" for response in responses):
            raise ValueError("Prometheus 查询未返回 success")
        return responses

    @staticmethod
    def _runtime_for_stream(
        stream: JudgeStreamConfig, values: dict[str, float]
    ) -> JudgeRuntimeSnapshot:
        """将按实例收集的原始指标转换为稳定的语言级结构。"""
        def value(name: str) -> float | None:
            return values.get(name)

        up = value("up")
        paused = value("aoilearn_judge_expand_paused")
        return JudgeRuntimeSnapshot(
            language=stream.language,
            instance=stream.prometheus_target,
            up=None if up is None else up == 1,
            pool_active=value("aoilearn_judge_pool_active"),
            pool_available=value("aoilearn_judge_pool_available"),
            pool_creating=value("aoilearn_judge_pool_creating"),
            expand_paused=None if paused is None else paused == 1,
            host_cpu_percent=value("aoilearn_judge_host_cpu_percent"),
            host_memory_percent=value("aoilearn_judge_host_memory_percent"),
        )
