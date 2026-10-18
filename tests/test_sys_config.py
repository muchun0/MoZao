# -*- coding: utf-8 -*-
"""后台开关管理（sys_config）测试。

覆盖（TECH-后台开关管理 §6.1）：
- seed 幂等 / 注册表覆盖 / 新增开关自动入表（"新增开关历史数据无问题"核心动机）
- 更新写库 + 审计（旧值/新值/operator）/ 同值不写审计
- 路由层校验：类型严格转换 / RANGE_RULES 范围 / select 枚举 / 未知 key 拒绝
- 覆盖注入：表有键一律以表为准；DB 异常回退默认；项目 config.json 快照不被改写
- 权限：非管理员 GET/PUT → 403

说明：
- 依赖 MySQL 的用例沿用 skipif 约定（本机 MySQL 不可用时跳过）；
- 所有改写 sys_config 的用例自行恢复原值，不污染共享表。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402
from webapp import db  # noqa: E402
from webapp.db import config as cfg_db  # noqa: E402


def _db_ok() -> bool:
    try:
        with db._connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_NEED_DB = pytest.mark.skipif(not _db_ok(), reason="本机 MySQL 不可用")


# ---------- 纯函数：类型推导 / 转换 ----------

@pytest.mark.p2
def test_flag_type_and_json_default():
    assert cfg_db.flag_type(True) == "bool"
    assert cfg_db.flag_type(3) == "int"
    assert cfg_db.flag_type(0.6) == "float"
    assert cfg_db.flag_type("medium") == "str"
    assert cfg_db.flag_type({"a": 1}) == "json"
    assert cfg_db.flag_type([1, 2]) == "json"
    assert cfg_db.json_default(True) == "true"
    assert cfg_db.json_default(False) == "false"
    assert cfg_db.json_default(5) == "5"
    assert cfg_db.json_default("medium") == "medium"
    assert cfg_db.json_default({"chapter": 150}) == '{"chapter":150}'


@pytest.mark.p1
def test_type_coerce_bool_int_float():
    assert cfg_db.coerce_config_type("bool", "true") is True
    assert cfg_db.coerce_config_type("bool", "FALSE") is False
    assert cfg_db.coerce_config_type("bool", "1") is True
    assert cfg_db.coerce_config_type("int", "200") == 200
    assert cfg_db.coerce_config_type("float", "0.6") == 0.6
    assert cfg_db.coerce_config_type("str", "hello") == "hello"
    assert cfg_db.coerce_config_type("json", '{"chapter":150}') == {"chapter": 150}
    with pytest.raises(ValueError):
        cfg_db.coerce_config_type("bool", "abc")
    with pytest.raises(ValueError):
        cfg_db.coerce_config_type("int", "1.5")


# ---------- seed / 注册表覆盖 ----------

@pytest.mark.p2
@_NEED_DB
def test_seed_flags_power_idempotent():
    """seed 幂等：重复执行不报错，表行数 = 注册表条数。"""
    from novel_gen.config import FEATURE_FLAGS

    cfg_db.seed_feature_flags()
    cfg_db.seed_feature_flags()
    assert cfg_db.count_configs() == len(FEATURE_FLAGS)


@pytest.mark.p1
@_NEED_DB
def test_seed_flags_cover_registry():
    """sys_config 的 key 集合 == FEATURE_FLAGS 的 key 集合（注册表是唯一入口）。"""
    from novel_gen.config import FEATURE_FLAGS

    cfg_db.seed_feature_flags()
    keys = {r["config_key"] for r in cfg_db.list_configs()}
    assert keys == {f.key for f in FEATURE_FLAGS}


@pytest.mark.p1
@_NEED_DB
def test_seed_new_flag_auto_insert(monkeypatch):
    """模拟新增开关：注册表加一行 → seed 自动以默认值入表（核心动机验证）。"""
    from novel_gen.config import FEATURE_FLAGS, FeatureFlag

    flags = FEATURE_FLAGS + [FeatureFlag("writing", "__test_new_flag__", 42, "测试新增开关")]
    monkeypatch.setattr("novel_gen.config.FEATURE_FLAGS", flags)
    try:
        added = cfg_db.seed_feature_flags()
        assert added == 1
        row = cfg_db.get_config("__test_new_flag__")
        assert row is not None
        assert row["config_value"] == "42"
        assert row["config_group"] == "writing"
        assert row["config_type"] == "int"
    finally:
        with db._connect() as conn:
            conn.execute("DELETE FROM sys_config WHERE config_key=%s", ("__test_new_flag__",))


# ---------- 更新 + 审计 ----------

@pytest.mark.p1
@_NEED_DB
def test_update_and_audit():
    old = cfg_db.get_config("state_extract_frequency")["config_value"]
    try:
        n = cfg_db.update_configs([{"key": "state_extract_frequency", "value": "3"}], "tester")
        assert n == 1
        row = cfg_db.get_config("state_extract_frequency")
        assert row["config_value"] == "3"
        assert row["updated_by"] == "tester"
        with db._connect() as conn:
            log = conn.execute(
                "SELECT config_key, old_value, new_value, operator FROM sys_config_log "
                "WHERE config_key=%s ORDER BY id DESC LIMIT 1",
                ("state_extract_frequency",),
            ).fetchone()
        assert log["old_value"] == old
        assert log["new_value"] == "3"
        assert log["operator"] == "tester"
        # 同值不写审计
        n2 = cfg_db.update_configs([{"key": "state_extract_frequency", "value": "3"}], "tester2")
        assert n2 == 0
    finally:
        cfg_db.update_configs([{"key": "state_extract_frequency", "value": old}], "test_restore")


# ---------- 路由层校验（纯函数） ----------

def _validate(items):
    from webapp.routers.admin import _validate_config_items, ConfigUpdateItem
    return _validate_config_items([ConfigUpdateItem(**it) for it in items])


@pytest.mark.p1
def test_unknown_key_rejected():
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "hack_key", "value": "1"}])
    assert e.value.status_code == 400


@pytest.mark.p1
def test_invalid_type_rejected():
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "book_memory", "value": "abc"}])   # bool 开关传非布尔
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "memory_window", "value": "1.5"}])  # int 开关传浮点
    assert e.value.status_code == 400


@pytest.mark.p1
def test_range_rule_rejected():
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "max_loops", "value": "0"}])        # 下限 1
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "ai_taste_threshold", "value": "200"}])  # 上限 100
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "beat_gate_min_coverage", "value": "1.5"}])  # 上限 1.0
    assert e.value.status_code == 400


@pytest.mark.p1
def test_select_enum_rejected():
    with pytest.raises(HTTPException) as e:
        _validate([{"key": "scene_state_mode", "value": "random"}])
    assert e.value.status_code == 400
    ok = _validate([{"key": "scene_state_mode", "value": "deterministic"}])
    assert ok == [{"key": "scene_state_mode", "value": "deterministic"}]


@pytest.mark.p1
def test_valid_items_normalized():
    ok = _validate([
        {"key": "book_memory", "value": "False"},
        {"key": "state_extract_frequency", "value": "3"},
        {"key": "pacing_density", "value": "high"},
    ])
    assert ok == [
        {"key": "book_memory", "value": "false"},
        {"key": "state_extract_frequency", "value": "3"},
        {"key": "pacing_density", "value": "high"},
    ]


# ---------- 覆盖注入 ----------

@pytest.mark.p0
def test_apply_global_overrides(monkeypatch):
    """表有键一律以表为准（顶层 + guard 子项 + scene_limits）；DB 异常回退默认。"""
    import webapp.core as core
    from novel_gen.config import NovelConfig

    rows = [
        {"config_key": "memory_window", "config_value": "9", "config_type": "int"},
        {"config_key": "book_memory", "config_value": "false", "config_type": "bool"},
        {"config_key": "enabled", "config_value": "false", "config_type": "bool"},
        {"config_key": "max_loops", "config_value": "50", "config_type": "int"},
        {"config_key": "json_schema_output", "config_value": "false", "config_type": "bool"},
        {"config_key": "scene_limits", "config_value": '{"chapter":300}', "config_type": "json"},
    ]
    monkeypatch.setattr(cfg_db, "list_configs", lambda: rows)
    cfg = NovelConfig()
    core._apply_global_overrides(cfg)
    assert cfg.memory_window == 9
    assert cfg.book_memory is False
    assert cfg.agent_guard.enabled is False
    assert cfg.agent_guard.max_loops == 50
    assert cfg.json_schema_output is False
    assert cfg.agent_guard.scene_limits == {"chapter": 300}

    # DB 异常 → 回退项目快照/注册表默认，不阻塞
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(cfg_db, "list_configs", boom)
    cfg2 = NovelConfig()
    core._apply_global_overrides(cfg2)
    assert cfg2.memory_window == 5
    assert cfg2.agent_guard.enabled is True

    # 表值非法 → 跳过该键，其余继续
    bad = [{"config_key": "memory_window", "config_value": "abc", "config_type": "int"},
           {"config_key": "verify_every", "config_value": "7", "config_type": "int"}]
    monkeypatch.setattr(cfg_db, "list_configs", lambda: bad)
    cfg3 = NovelConfig()
    core._apply_global_overrides(cfg3)
    assert cfg3.memory_window == 5     # 非法值跳过
    assert cfg3.verify_every == 7      # 合法值仍生效


@pytest.mark.p0
@_NEED_DB
def test_override_not_persist_to_project(tmp_path):
    """覆盖注入是内存态：项目 config.json 快照内容不被改写。"""
    import webapp.core as core
    from novel_gen.config import NovelConfig
    from novel_gen.store.project import ProjectStore

    store = ProjectStore.create(tmp_path / "np", NovelConfig(title="不落盘"))
    cfg = store.load_config()
    before = (store.dir / "config.json").read_text(encoding="utf-8")
    core._apply_global_overrides(cfg)
    after = (store.dir / "config.json").read_text(encoding="utf-8")
    assert before == after


@pytest.mark.p1
@_NEED_DB
def test_table_missing_key_fallback(tmp_path):
    """表缺键（未 seed 窗口）回退项目快照/注册表默认；seed 后恢复表权威。"""
    import webapp.core as core
    from novel_gen.config import NovelConfig
    from novel_gen.store.project import ProjectStore

    cfg_db.seed_feature_flags()
    with db._connect() as conn:
        conn.execute("DELETE FROM sys_config WHERE config_key=%s", ("memory_window",))
    try:
        store = ProjectStore.create(tmp_path / "fb", NovelConfig(title="缺键回退"))
        cfg = store.load_config()
        core._apply_global_overrides(cfg)
        assert cfg.memory_window == 5          # 表缺键 → 快照/默认

        cfg_db.seed_feature_flags()            # seed 后恢复表权威
        with db._connect() as conn:
            conn.execute("UPDATE sys_config SET config_value=%s WHERE config_key=%s",
                         ("7", "memory_window"))
        cfg2 = store.load_config()
        core._apply_global_overrides(cfg2)
        assert cfg2.memory_window == 7         # 表有键 → 以表为准
    finally:
        with db._connect() as conn:
            conn.execute("UPDATE sys_config SET config_value=%s WHERE config_key=%s",
                         ("5", "memory_window"))


# ---------- 权限 ----------

@pytest.mark.p0
@_NEED_DB
def test_admin_required():
    from fastapi.testclient import TestClient
    from webapp.server import app

    c = TestClient(app)
    # 未登录 → 401
    assert c.get("/api/admin/config").status_code == 401
    assert c.put("/api/admin/config", json={"items": []}).status_code == 401

    test_user = "cfg_perm_user"
    try:
        ok, _why = db.create_user(test_user, "pass1234", is_admin=False,
                                  email="cfg_perm@test.local")
        assert ok
        u = db.get_user_by_name(test_user)
        token = db.create_session(u["id"])
        # 普通用户 → 403
        assert c.get("/api/admin/config",
                     headers={"Authorization": f"Bearer {token}"}).status_code == 403
        assert c.put("/api/admin/config", json={"items": []},
                     headers={"Authorization": f"Bearer {token}"}).status_code == 403
        # 管理员 → 200（开关全量）
        admin = db.get_user_by_name("admin")
        atoken = db.create_session(admin["id"])
        r = c.get("/api/admin/config", headers={"Authorization": f"Bearer {atoken}"})
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 94
        mods = {g["module"] for g in body["groups"]}
        assert mods == {"memory", "writing", "review", "guard"}
        # 校验 400 路径（管理员 + 非法 key）
        r2 = c.put("/api/admin/config", json={"items": [{"key": "hax", "value": "1"}]},
                   headers={"Authorization": f"Bearer {atoken}"})
        assert r2.status_code == 400
    finally:
        with db._connect() as conn:
            conn.execute("DELETE FROM users WHERE username=%s", (test_user,))
