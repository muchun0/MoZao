# -*- coding: utf-8 -*-
"""pytest 全局入口：显式初始化数据库。

导入 ``webapp.db`` 已不再自动建表（见 REQ/fix-db-init-side-effect），
测试会话作为独立入口，在这里显式执行一次建表 + 默认配置。
MySQL 不可用时仅告警，由各用例自身的 skipif 决定是否跳过。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.logger import get_logger  # noqa: E402

logger = get_logger("tests")

_DISABLED = {"0", "false", "no", "off"}


@pytest.fixture(scope="session", autouse=True)
def _init_db_for_session():
    """测试会话启动时显式初始化；MOZAO_AUTO_INIT=0 时尊重禁写约定直接跳过。"""
    if (os.environ.get("MOZAO_AUTO_INIT") or "").strip().lower() in _DISABLED:
        logger.info("MOZAO_AUTO_INIT=0：测试会话跳过数据库初始化")
        yield
        return
    from webapp import db
    try:
        db.init_schema()
        db.seed_defaults()
    except Exception as exc:
        logger.warning("测试会话数据库初始化跳过（%s: %s）", type(exc).__name__, exc)
    yield
