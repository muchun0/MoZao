# -*- coding: utf-8 -*-
"""Agent 统一入口前置守卫测试（P0 资金守卫 + P1 参数/锁定/手写章守卫）。

覆盖 TECH §4.1 守卫群：判定顺序固定的各守卫拒绝码（402/400/409）与放行路径。
守卫均为纯函数，直接调用断言 HTTPException，不起线程。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from webapp import db
from webapp import generate_service as gs


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _err_code(fn, *args) -> int:
    with pytest.raises(HTTPException) as ei:
        fn(*args)
    return ei.value.status_code


# ---------- 计费模式与余额守卫（P0 资金） ----------

@pytest.mark.p0
def test_resolve_billing_mode(monkeypatch):
    monkeypatch.setattr(db, "get_user_llm", lambda uid: {"api_key": "k", "provider": "deepseek"})
    assert gs.resolve_billing_mode(1) == "user_llm"
    monkeypatch.setattr(db, "get_user_llm", lambda uid: {})
    assert gs.resolve_billing_mode(1) == "platform"
    assert gs.resolve_billing_mode(None) == "platform"


@pytest.mark.p0
def test_guard_balance_platform_insufficient(monkeypatch):
    """平台付费 + 余额 ≤ 0 → 402；自备 Key 不校验平台余额。"""
    monkeypatch.setattr(db, "get_user", lambda uid: {"id": uid, "balance": 0.0})
    assert _err_code(gs.guard_balance, "platform", 1) == 402
    gs.guard_balance("user_llm", 1)  # BYOK 放行
    monkeypatch.setattr(db, "get_user", lambda uid: {"id": uid, "balance": 5.0})
    gs.guard_balance("platform", 1)  # 有余额放行
    gs.guard_balance("platform", None)  # 无归属（历史项目）放行


# ---------- agent / params 守卫 ----------

@pytest.mark.p1
def test_guard_agent_product():
    for agent in gs.ALLOWED_AGENTS:
        gs.guard_agent_product(agent)
    assert _err_code(gs.guard_agent_product, "editor.review") == 400
    assert _err_code(gs.guard_agent_product, "") == 400


@pytest.mark.p1
def test_guard_params_chapter_scope_inference():
    assert gs.guard_params("writer.chapter", {"ch_no": 3})["scope"] == "single"
    assert gs.guard_params("writer.chapter", {"from_ch": 2})["scope"] == "range"
    assert gs.guard_params("writer.chapter", {})["scope"] == "all"


@pytest.mark.p1
def test_guard_params_chapter_rejections():
    # 未知键
    assert _err_code(gs.guard_params, "writer.chapter", {"foo": 1}) == 400
    # ch_no 与 from_ch 互斥
    assert _err_code(gs.guard_params, "writer.chapter", {"ch_no": 1, "from_ch": 1}) == 400
    # scope=single 缺 ch_no / range 缺 from_ch
    assert _err_code(gs.guard_params, "writer.chapter", {"scope": "single"}) == 400
    assert _err_code(gs.guard_params, "writer.chapter", {"scope": "range"}) == 400
    # 非法 scope / 非正整数
    assert _err_code(gs.guard_params, "writer.chapter", {"scope": "chapter"}) == 400
    assert _err_code(gs.guard_params, "writer.chapter", {"ch_no": 0}) == 400
    assert _err_code(gs.guard_params, "writer.chapter", {"ch_no": "3"}) == 400


@pytest.mark.p1
def test_guard_params_volume():
    assert gs.guard_params("architect.volume", {"vol_no": 2}) == {"vol_no": 2}
    assert _err_code(gs.guard_params, "architect.volume", {}) == 400
    assert _err_code(gs.guard_params, "architect.volume", {"vol_no": 0}) == 400
    assert _err_code(gs.guard_params, "architect.volume", {"vol_no": 1, "x": 1}) == 400


@pytest.mark.p1
def test_guard_params_product_level():
    assert gs.guard_params("architect.outline", {}) == {}
    assert gs.guard_params("architect.world", {"scope": "product"}) == {}
    assert _err_code(gs.guard_params, "architect.outline", {"ch_no": 1}) == 400
    assert _err_code(gs.guard_params, "researcher", {"scope": "single"}) == 400


# ---------- 锁定守卫 ----------

def _write_lock(store, lock: dict) -> None:
    (store.dir / "lock.json").write_text(json.dumps(lock), encoding="utf-8")


@pytest.mark.p1
def test_guard_locked(store):
    _write_lock(store, {"outline": True, "world_view": True,
                        "volume": {"2": True}, "chapter": {"5": True}})
    assert _err_code(gs.guard_locked, store, "architect.outline", {}) == 409
    assert _err_code(gs.guard_locked, store, "architect.world", {}) == 409
    assert _err_code(gs.guard_locked, store, "architect.volume", {"vol_no": 2}) == 409
    assert _err_code(gs.guard_locked, store, "writer.chapter",
                     {"scope": "single", "ch_no": 5}) == 409
    # 未锁定放行；volume 1 未锁；chapter range 不在守卫层拒（执行层逐章跳过）
    gs.guard_locked(store, "architect.volume", {"vol_no": 1})
    gs.guard_locked(store, "writer.chapter", {"scope": "single", "ch_no": 3})
    gs.guard_locked(store, "writer.chapter", {"scope": "range", "from_ch": 1})


# ---------- 手写章守卫 ----------

@pytest.mark.p1
def test_guard_user_chapter(store):
    store.save_chapter(Chapter(no=1, title="一", content="内容" * 200))
    store.save_chapter(Chapter(no=3, title="三", content="手写" * 200), source="user")
    # 单章手写 → 400；单章非手写 → 放行
    assert _err_code(gs.guard_user_chapter, store, "writer.chapter",
                     {"scope": "single", "ch_no": 3}) == 400
    gs.guard_user_chapter(store, "writer.chapter", {"scope": "single", "ch_no": 1})
    # range/all 起点 ≤ 最大手写章号（3）→ 400；从第 4 章起 → 放行
    assert _err_code(gs.guard_user_chapter, store, "writer.chapter",
                     {"scope": "range", "from_ch": 2}) == 400
    assert _err_code(gs.guard_user_chapter, store, "writer.chapter", {"scope": "all"}) == 400
    gs.guard_user_chapter(store, "writer.chapter", {"scope": "range", "from_ch": 4})
    # 非章节 agent 不校验
    gs.guard_user_chapter(store, "architect.outline", {})


# ---------- Skill 上限守卫 ----------

@pytest.mark.p1
def test_guard_skill_limits():
    gs.guard_skill_limits([1, 2, 3, 4, 5])
    gs.guard_skill_limits([])
    assert _err_code(gs.guard_skill_limits, [1, 2, 3, 4, 5, 6]) == 400


# ---------- 全局回退开关 ----------

@pytest.mark.p1
def test_unified_generate_flag(monkeypatch):
    monkeypatch.setattr(db, "get_config", lambda key: {"config_value": "false"})
    assert gs.unified_generate_enabled() is False
    assert _err_code(gs.assert_unified_generate_enabled) == 503
    monkeypatch.setattr(db, "get_config", lambda key: {"config_value": "true"})
    assert gs.unified_generate_enabled() is True
    gs.assert_unified_generate_enabled()
    # 表无记录 → 注册表默认 True
    monkeypatch.setattr(db, "get_config", lambda key: None)
    assert gs.unified_generate_enabled() is True
