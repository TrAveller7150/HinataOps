"""把 AoiLearn 判题 Tool 的结构化证据压缩为人工可读的关键指标。"""

from __future__ import annotations

from hinataops.agent_core.models import Observation


class AoiLearnJudgeEvidencePresenter:
    """解释 AoiLearn 专属观测字段；未知 Tool 始终退回通用采集状态。"""

    def render(self, observation: Observation) -> tuple[str, ...]:
        """返回有限关键指标，避免将完整 Docker 快照或无界数据输出到终端。"""
        if observation.reliability != "complete":
            return (self._failure_line(observation),)
        value = observation.value
        if observation.tool_name == "aoi_judge_get_runtime":
            return self._runtime_lines(value)
        if observation.tool_name == "aoi_judge_get_container_runtime":
            return self._container_lines(value)
        if observation.tool_name == "aoi_judge_get_stream_summary":
            return self._stream_lines(value)
        if observation.tool_name == "aoi_judge_get_pipeline_summary":
            return self._pipeline_lines(value)
        return ("观测采集完整。",)

    @staticmethod
    def _runtime_lines(value: dict[str, object]) -> tuple[str, ...]:
        lines = []
        for runtime in _objects(value.get("runtimes")):
            language = _text(runtime.get("language"), "未知语言")
            instance = _text(runtime.get("instance"), "未知实例")
            lines.append(
                f"{language} Worker（{instance}）：up={_state(runtime.get('up'))}，"
                f"空闲沙箱={_number(runtime.get('pool_available'))}，"
                f"总沙箱={_number(runtime.get('pool_active'))}，"
                f"创建中={_number(runtime.get('pool_creating'))}，"
                f"CPU={_percent(runtime.get('host_cpu_percent'))}，"
                f"内存={_percent(runtime.get('host_memory_percent'))}"
            )
        return tuple(lines) or ("未返回 Judge Worker 指标。",)

    @staticmethod
    def _container_lines(value: dict[str, object]) -> tuple[str, ...]:
        services = _objects(value.get("services"))
        judges = [item for item in services if _text(item.get("service")).startswith("judge-")]
        lines = [
            f"{_text(item.get('service'), 'Judge 服务')}：{_text(item.get('state'), 'unknown')}"
            f"（{_text(item.get('status'), '无状态详情')}）"
            for item in judges
        ]
        lines.append(
            f"沙箱池：运行中={_number(value.get('sandbox_pool_running'))}，"
            f"已退出={_number(value.get('sandbox_pool_exited'))}"
        )
        return tuple(lines)

    @staticmethod
    def _stream_lines(value: dict[str, object]) -> tuple[str, ...]:
        language = _text(value.get("language"), "未知语言")
        stream_key = _text(value.get("stream_key"), "未知 Stream")
        group = value.get("consumer_group")
        if not isinstance(group, dict):
            return (f"{language} 队列 {stream_key}：未找到目标 Consumer Group。",)
        return (
            f"{language} 队列 {stream_key}：消费者={_number(group.get('consumers'))}，"
            f"lag={_number(group.get('lag'))}，pending={_number(group.get('pending'))}，"
            f"历史消息={_number(value.get('history_length'))}",
        )

    @staticmethod
    def _pipeline_lines(value: dict[str, object]) -> tuple[str, ...]:
        window = _number(value.get("window_minutes"))
        task_statuses = _objects(value.get("task_statuses"))
        outbox_statuses = _objects(value.get("outbox_statuses"))
        task_text = "；".join(
            f"{_text(item.get('language'), '未知')}/{_text(item.get('status'), '未知')}="
            f"{_number(item.get('count'))}"
            for item in task_statuses
        ) or "无任务记录"
        outbox_text = "；".join(
            f"{_text(item.get('status'), '未知')}={_number(item.get('count'))}"
            for item in outbox_statuses
        ) or "无 Outbox 记录"
        return (f"最近 {window} 分钟：任务 {task_text}；Outbox {outbox_text}",)

    @staticmethod
    def _failure_line(observation: Observation) -> str:
        error = observation.value.get("metadata")
        if isinstance(error, dict) and isinstance(error.get("error"), dict):
            message = error["error"].get("message")
            if isinstance(message, str):
                return f"观测不完整：{message}"
        return "观测不完整，未获得可用指标。"


def _objects(value: object) -> list[dict[str, object]]:
    """只接受结构化对象列表，防止展示层为未知返回格式猜测字段。"""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: object, default: str = "—") -> str:
    """将展示字段限制为字符串，避免终端输出任意嵌套对象。"""
    return value if isinstance(value, str) and value else default


def _number(value: object) -> str:
    """统一展示数值和缺失值，不将 None 误读为零。"""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value) if isinstance(value, int) else "未知"


def _percent(value: object) -> str:
    """为数值型主机资源指标补充百分号。"""
    return f"{_number(value)}%" if isinstance(value, (int, float)) else "未知"


def _state(value: object) -> str:
    """将 Prometheus up 的三态值渲染为明确中文，避免布尔值歧义。"""
    return "正常" if value is True else "不可达" if value is False else "未知"
