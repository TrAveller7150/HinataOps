"""P2 运行时共享的动作授权存储定位。"""

from __future__ import annotations

import os
from pathlib import Path

from hinataops.actions.repository import ActionRepository


def action_repository() -> ActionRepository:
    """读取显式状态库路径，供审批端与独立 MCP Server 共享授权事实。"""
    database_path = Path(os.environ.get("HINATAOPS_STATE_DB", ".hinataops/state.sqlite3"))
    return ActionRepository(database_path)
