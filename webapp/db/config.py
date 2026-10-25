# -*- coding: utf-8 -*-
"""
系统开关配置数据层（sys_config / sys_config_log）。

职责：FEATURE_FLAGS 注册表 ↔ sys_config 表之间的种子同步（新增开关自动入表）、
列表读取、批量更新与审计、类型转换。SQL 只允许出现在本模块（§11.2 db 层唯一写 SQL）。

以表中数据为准（REQ-后台开关管理 D1）：sys_config 是运行时唯一权威；
项目 config.json 快照不迁移不改写；覆盖注入只发生在 webapp 内存态。
"""
from __future__ import annotations

import json
from typing import Any

from .base import _connect, logger

# 后台展示分组顺序（与 novel_gen.config.FEATURE_FLAGS.module 对齐）
FLAG_MODULES: tuple[str, ...] = ("memory", "writing", "review", "guard", "assistant", "version")

# select 枚举白名单（唯一事实来源：seed 类型推导与路由层校验共用）。
# key → 可选值列表；此类开关注册表 default 为 str，但后台渲染为下拉框。
SELECT_ENUMS: dict[str, list[str]] = {
    "scene_state_mode": ["auto", "llm", "deterministic"],
    "pacing_density": ["low", "medium", "high"],
    "plot_speed": ["slow", "medium", "fast"],
}

# guard 模块中落在 cfg.agent_guard 上的标量子项（非 NovelConfig 顶层字段）。
# TECH §3.3 伪码漏了 enabled（否则后台改总开关不生效），按"以表为准"补齐为 6 项。
AGENT_GUARD_SCALAR: tuple[str, ...] = (
    "enabled", "max_loops", "max_tokens", "input_check", "output_check", "quality_check",
)
# guard 模块中 agent_guard.scene_limits（JSON 整段覆盖）；不在 FEATURE_FLAGS 注册表，
# 仅当表内存在该行时生效（二期 per-project 扩展位）。
AGENT_GUARD_JSON: str = "scene_limits"

__all__ = [
    "FLAG_MODULES", "SELECT_ENUMS", "AGENT_GUARD_SCALAR", "AGENT_GUARD_JSON",
    "coerce_config_type", "flag_type", "json_default", "seed_feature_flags",
    "list_configs", "get_config", "update_configs", "count_configs",
]


def _short_label(desc: str) -> str:
    """后台显示名：取首个出现的句读前片段（label 上限 60 字防超列宽）。"""
    pos = [i for i in (desc.find(s) for s in ("。", "；", "：", "，", "——")) if i > 0]
    idx = min(pos) if pos else len(desc)
    return desc[:min(idx, 60)]


def flag_type(default: Any) -> str:
    """由注册表默认值推导 config_type：bool/int/float/json(dict/list)/str。"""
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, (dict, list)):
        return "json"
    return "str"


def json_default(value: Any) -> str:
    """默认值序列化：bool→'true'/'false'，数字→str，str 原样，dict/list→JSON。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def coerce_config_type(config_type: str, raw: str):
    """按类型把存储串转回 Python 值（覆盖注入与路由层校验共用）。

    - bool：'true'/'false'（不区分大小写，含 1/0/yes/no）→ bool，其余抛 ValueError
    - int/float：严格转换，失败抛 ValueError
    - json：json.loads，失败抛 JSONDecodeError
    - str：原样返回
    """
    if config_type == "bool":
        v = str(raw).strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no", ""):
            return False
        raise ValueError(f"不是合法的布尔值: {raw!r}")
    if config_type == "int":
        return int(str(raw).strip())
    if config_type == "float":
        return float(str(raw).strip())
    if config_type == "json":
        return json.loads(raw)
    return str(raw)


def seed_feature_flags() -> int:
    """把 FEATURE_FLAGS 注册表全量幂等同步进 sys_config（新增开关自动入表）。

    INSERT ... ON DUPLICATE KEY UPDATE：仅同步 label/description/config_type 等元数据，
    不覆盖管理员已修改的 config_value / updated_by（以表值为准）。
    删除的注册项不删表（防误删，保留历史）。
    返回本次新增条数。
    """
    from novel_gen.config import FEATURE_FLAGS

    added = 0
    with _connect() as conn:
        for f in FEATURE_FLAGS:
            ctype = "select" if f.key in SELECT_ENUMS else flag_type(f.default)
            cur = conn.execute(
                "INSERT INTO sys_config "
                "(config_key, config_value, config_group, config_type, label, description, is_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, 1) "
                "ON DUPLICATE KEY UPDATE config_group=VALUES(config_group), "
                "config_type=VALUES(config_type), label=VALUES(label), "
                "description=VALUES(description), is_system=1",
                (f.key, json_default(f.default), f.module, ctype,
                 _short_label(f.desc), f.desc),
            )
            # MySQL 对 ON DUPLICATE KEY UPDATE：插入成功 rowcount=1，更新=2，无变化=0
            added += 1 if cur.rowcount == 1 else 0
    return added


def count_configs() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sys_config").fetchone()
    return int(row["n"])


def list_configs() -> list[dict]:
    """返回全部开关行（按模块分组顺序 + id 排序），供后台渲染与覆盖注入。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT config_key, config_value, config_group, config_type, label, description, "
            "is_system, updated_at, updated_by FROM sys_config "
            "ORDER BY FIELD(config_group, 'memory', 'writing', 'review', 'guard'), id",
        ).fetchall()
    return rows


def get_config(key: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT config_key, config_value, config_group, config_type, label, description, "
            "is_system, updated_at, updated_by FROM sys_config WHERE config_key=%s",
            (key,),
        ).fetchone()
    return row


def update_configs(updates: list[dict], operator: str) -> int:
    """批量保存：逐条 UPDATE + 同事务写审计；返回更新条数。

    调用方（路由层）已按注册表白名单 + 类型/范围校验，这里只做落库。
    键不存在时跳过（幂等保护，不视为错误）。
    """
    count = 0
    with _connect() as conn:
        for u in updates:
            key = u["key"]
            new_value = u["value"]
            row = conn.execute(
                "SELECT config_value FROM sys_config WHERE config_key=%s", (key,),
            ).fetchone()
            if row is None:
                logger.warning("sys_config 更新跳过（键不存在）: %s", key)
                continue
            old_value = row["config_value"]
            if old_value == new_value:
                continue
            conn.execute(
                "UPDATE sys_config SET config_value=%s, updated_by=%s WHERE config_key=%s",
                (new_value, operator, key),
            )
            conn.execute(
                "INSERT INTO sys_config_log (config_key, old_value, new_value, operator) "
                "VALUES (%s, %s, %s, %s)",
                (key, old_value, new_value, operator),
            )
            count += 1
    return count
