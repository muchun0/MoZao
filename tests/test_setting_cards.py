# -*- coding: utf-8 -*-
"""P1-1 分章设定卡索引测试（P1，REQ-token优化）。

覆盖：索引确定性构建、按章命中（人物/生物/地点/势力/伏笔）、异常降级返回空、
Memory 按章子集注入（命中生物注入、未命中不注入）、开关关闭回退全量。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (Chapter, ChapterPlan, Character, EnemyCard, World)
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.setting_cards import build_setting_cards


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _world():
    return World(
        world_view="玄幻大陆，灰雾笼罩的墟界。",
        characters=[
            Character(name="林小满", role="女主", desc="出身落霞城，性格坚韧"),
            Character(name="江澈", role="男主", desc="剑修，沉默寡言"),
        ],
        enemies=[
            EnemyCard(name="灰卒", form="人形怪物", behavior="见人就咬", kill="怕火"),
            EnemyCard(name="泥兽", form="泥浆生物", behavior="缓慢蠕动", kill="怕盐"),
        ],
        locations=[],
        factions=[],
    )


def _outline():
    return type("O", (), {
        "chapters": [
            ChapterPlan(no=1, title="第一章", goal="林小满大战灰卒，救下江澈"),
            ChapterPlan(no=2, title="第二章", goal="江澈负伤，林小满寻医"),
        ],
    })()


@pytest.mark.p1
def test_build_index_has_all_chapters(store):
    w, o = _world(), _outline()
    data = build_setting_cards(store, w, o)
    assert data["version"] == 1
    assert len(data["chapters"]) == 2
    assert data["chapters"][0]["no"] == 1
    assert data["chapters"][1]["no"] == 2


@pytest.mark.p1
def test_match_enemy_by_goal_keyword(store):
    w, o = _world(), _outline()
    data = build_setting_cards(store, w, o)
    ch1 = next(c for c in data["chapters"] if c["no"] == 1)
    # 第一章 goal 含「灰卒」→ 生物子集命中灰卒
    assert "灰卒" in ch1["enemies"]
    # 第二章 goal 不含灰卒/泥兽关键词 → 生物子集为空（非强约束，允许省略）
    ch2 = next(c for c in data["chapters"] if c["no"] == 2)
    assert ch2["enemies"] == []


@pytest.mark.p1
def test_build_degradation_empty(store):
    """world/outline 缺失或异常 → 返回空 dict（调用方跳过，回退全量注入）。"""
    assert build_setting_cards(store, None, _outline()) == {}
    assert build_setting_cards(store, _world(), None) == {}


@pytest.mark.p1
def test_memory_subset_injection(store):
    """setting_cards 存在时：命中生物注入、未命中生物不注入（输入裁剪生效）。"""
    w = _world()
    store.save_world(w)
    data = build_setting_cards(store, w, _outline())
    store.save_setting_cards(data)
    store.save_chapter(Chapter(no=1, title="第一章", content="正文" * 100, summary="s1"))
    # up_to_chapter=1 → 取第 1 章索引（enemies=[灰卒]）→ 注入灰卒，不注入泥兽
    ctx1 = Memory(store, 5).build_context(w, up_to_chapter=1)
    assert "生物图鉴" in ctx1
    assert "灰卒" in ctx1
    assert "泥兽" not in ctx1
    # up_to_chapter=2 → 取第 2 章索引（enemies=[]）→ 该章不注入生物块
    ctx2 = Memory(store, 5).build_context(w, up_to_chapter=2)
    assert "生物图鉴" not in ctx2


@pytest.mark.p1
def test_setting_cards_off_falls_back_full(store):
    """setting_cards=False：memory 不回退全量注入（生物全量）。"""
    cfg = store.load_config()
    cfg.setting_cards = False
    store.save_config(cfg)
    w = _world()
    store.save_world(w)
    data = build_setting_cards(store, w, _outline())
    store.save_setting_cards(data)
    store.save_chapter(Chapter(no=1, title="第一章", content="正文" * 100, summary="s1"))
    ctx = Memory(store, 5).build_context(w, up_to_chapter=1)
    assert "泥兽" in ctx  # 开关关闭 → 全量注入


@pytest.mark.p1
def test_store_roundtrip(store):
    data = {"version": 1, "chapters": [{"no": 1, "chars": ["林小满"]}]}
    store.save_setting_cards(data)
    assert store.load_setting_cards() == data
