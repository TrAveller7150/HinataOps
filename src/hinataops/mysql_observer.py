from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hinataops.observation import ObservationMetadata, new_metadata
from hinataops.redis_observer import JudgeLanguage
from hinataops.ssh import SshRunner

SUPPORTED_WINDOW_MINUTES = frozenset({5, 15, 60})


class JudgeTaskStatusCount(BaseModel):
    """一个时间窗口内按语言和状态聚合的判题任务数量。"""

    language: JudgeLanguage = Field(description="判题语言")
    status: str = Field(description="judge_tasks 中的业务状态")
    count: int = Field(description="该状态的任务数量")
    oldest_age_seconds: int = Field(description="该聚合中最早任务距当前的秒数")


class OutboxStatusCount(BaseModel):
    """一个时间窗口内按状态聚合的可靠入队 Outbox 数量。"""

    status: str = Field(description="Outbox 状态，如 pending、publishing 或 sent")
    count: int = Field(description="该状态的 Outbox 记录数")
    oldest_age_seconds: int = Field(description="该聚合中最早记录距当前的秒数")
    max_attempts: int = Field(description="该聚合中的最大投递尝试次数")
    last_error_count: int = Field(description="last_error 非空的记录数量")


class MysqlJudgePipelineSummary(BaseModel):
    """MySQL 中判题任务与可靠入队阶段的聚合证据。"""

    metadata: ObservationMetadata
    window_minutes: int = Field(description="受支持的回看时间窗口，单位为分钟")
    task_statuses: list[JudgeTaskStatusCount]
    outbox_statuses: list[OutboxStatusCount]


class MysqlJudgePipelineInspector:
    """通过固定领域 SQL 查询 MySQL 的判题流水线状态。

    查询只汇总 judge_tasks 与 judge_enqueue_outbox，既不返回学生代码，也不向调用方
    开放任意 SQL。窗口只能从预定义集合中选择，因此可安全嵌入固定查询模板。
    """

    def __init__(
        self, runner: SshRunner, environment_name: str, mysql_container: str
    ) -> None:
        self._runner = runner
        self._environment_name = environment_name
        self._mysql_container = mysql_container

    async def summary(self, window_minutes: int) -> MysqlJudgePipelineSummary:
        """查询指定窗口内任务状态、Outbox 状态和重试信号。"""
        if window_minutes not in SUPPORTED_WINDOW_MINUTES:
            allowed = ", ".join(str(item) for item in sorted(SUPPORTED_WINDOW_MINUTES))
            raise ValueError(f"window_minutes must be one of: {allowed}")

        output = await self._runner.run(self._mysql_command(window_minutes))
        task_statuses: list[JudgeTaskStatusCount] = []
        outbox_statuses: list[OutboxStatusCount] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            kind, language, status, count, oldest_age, max_attempts, error_count = line.split("\t")
            if kind == "1":
                task_statuses.append(
                    JudgeTaskStatusCount(
                        language=language,
                        status=status,
                        count=int(count),
                        oldest_age_seconds=int(oldest_age),
                    )
                )
            elif kind == "2":
                outbox_statuses.append(
                    OutboxStatusCount(
                        status=status,
                        count=int(count),
                        oldest_age_seconds=int(oldest_age),
                        max_attempts=int(max_attempts),
                        last_error_count=int(error_count),
                    )
                )
            else:
                raise ValueError(f"未知的 MySQL 判题汇总记录类型: {kind}")

        return MysqlJudgePipelineSummary(
            metadata=new_metadata(self._environment_name, "mysql"),
            window_minutes=window_minutes,
            task_statuses=task_statuses,
            outbox_statuses=outbox_statuses,
        )

    def _mysql_command(self, window_minutes: int) -> str:
        """构造仅含状态聚合的固定 MySQL 命令，不泄露连接密码。"""
        # kind=1 表示任务聚合、kind=2 表示 Outbox 聚合；用数字而非 SQL 字符串字面量，
        # 使整个命令可以安全包裹在容器内 Shell 的单引号参数中。
        query = (
            "SELECT 1, language, status, COUNT(*), "
            "COALESCE(MAX(TIMESTAMPDIFF(SECOND, created_at, NOW())), 0), 0, 0 "
            "FROM judge_tasks "
            f"WHERE created_at >= NOW() - INTERVAL {window_minutes} MINUTE "
            "GROUP BY language, status "
            "UNION ALL "
            "SELECT 2, NULL, status, COUNT(*), "
            "COALESCE(MAX(TIMESTAMPDIFF(SECOND, created_at, NOW())), 0), "
            "COALESCE(MAX(attempts), 0), SUM(last_error IS NOT NULL) "
            "FROM judge_enqueue_outbox "
            f"WHERE created_at >= NOW() - INTERVAL {window_minutes} MINUTE "
            "GROUP BY status"
        )
        return (
            f"docker exec {self._mysql_container} sh -lc "
            "'mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" --batch --raw "
            f"--skip-column-names --execute \"{query}\" \"$MYSQL_DATABASE\"'"
        )
