import asyncio

import pytest

from hinataops.ops_mcp.toolsets.aoi_learn_judge.pipeline_summary import MysqlJudgePipelineInspector


class FakeMysqlAdapter:
    """为 MySQL 聚合查询测试提供固定的 TSV 返回值。"""

    async def execute_trusted_tsv(self, statement: str) -> str:
        assert "INTERVAL 15 MINUTE" in statement
        return "\n".join(
            [
                "1\tpython\tpending\t2\t120\t0\t0",
                "2\tNULL\tpending\t2\t120\t3\t1",
            ]
        )


def test_mysql_summary_parses_task_and_outbox_aggregates() -> None:
    result = asyncio.run(
        MysqlJudgePipelineInspector("test", FakeMysqlAdapter()).summary(15)
    )

    assert result.task_statuses[0].language == "python"
    assert result.task_statuses[0].oldest_age_seconds == 120
    assert result.outbox_statuses[0].max_attempts == 3
    assert result.outbox_statuses[0].last_error_count == 1


def test_mysql_summary_rejects_unapproved_time_window() -> None:
    with pytest.raises(ValueError, match="5, 15, 60"):
        asyncio.run(
            MysqlJudgePipelineInspector("test", FakeMysqlAdapter()).summary(30)
        )
