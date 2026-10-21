# -*- coding: utf-8 -*-
"""全局开关覆盖注入（REQ-后台开关管理）：sys_config → NovelConfig 内存态。

从 webapp/core.py 抽出复用：生成链路（`_open_pipeline`）与小墨AI 对话（不跑生成管线）
都要拿到后台开关的实时值，避免同一逻辑两处维护（§11.2 单一权威源）。
"""
from __future__ import annotations

from novel_gen.config import NovelConfig

from .db import config as config_db
from .logger import get_logger

logger = get_logger("config_overrides")


def apply_global_overrides(cfg: NovelConfig) -> None:
    """把 sys_config 后台全局覆盖合并进 cfg（内存态，不落盘）。

    以表中数据为准：表里有的键一律覆盖——NovelConfig 顶层字段、
    cfg.agent_guard 标量子项、agent_guard.scene_limits（JSON 整段）。
    DB 读取/解析失败回退项目快照/注册表默认，logger 留痕不阻塞（§11.6）。
    """
    try:
        overrides = {r["config_key"]: r for r in config_db.list_configs()}
    except Exception:
        logger.exception("读取全局开关覆盖失败，本次使用项目快照默认值")
        return
    for key, row in overrides.items():
        try:
            value = config_db.coerce_config_type(row["config_type"], row["config_value"])
        except Exception:
            logger.warning("全局开关 %s 值非法已跳过: %r", key, row["config_value"])
            continue
        if key == config_db.AGENT_GUARD_JSON:
            cfg.agent_guard.scene_limits = value
        elif key in config_db.AGENT_GUARD_SCALAR:
            setattr(cfg.agent_guard, key, value)
        elif hasattr(cfg, key):
            setattr(cfg, key, value)
