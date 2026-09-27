# -*- coding: utf-8 -*-
"""P1-3 按章触发注入设定（Lorebook 式裁剪）测试（P1）。

覆盖：按章裁剪只注入命中人物、预测失败回退全量、强约束/锁定项永不裁剪且置末尾、
开关关闭行为与旧版一致。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Character, World
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000,
                      background="修仙世界，灵气复苏", hero="江澈，沉默剑修",
                      forbidden=["穿越", "系统"])
    s = ProjectStore.create(tmp_path / "lb", cfg)
    return s


def _world():
    return World(
        world_view="修仙大陆，宗门林立",
        characters=[
            Character(name="林小满", role="女主", desc="落霞城出身，坚韧"),
            Character(name="江澈", role="男主", desc="剑修，沉默寡言"),
            Character(name="赵无咎", role="反派", desc="魔道长老，阴鸷"),
            Character(name="苏晚", role="配角", desc="医馆女郎中"),
            Character(name="老周", role="配角", desc="茶摊老板"),
            Character(name="柳如烟", role="配角", desc="师姐"),
        ],
        foreshadowing=["林小满玉佩中封存上古剑意", "江澈左臂旧伤与魔种有关", "柳如烟隐瞒身世"],
        locked_settings=["主角不得黑化"],
    )


@pytest.mark.p1
def test_trim_only_hits(store):
    """人物≥6 时按章裁剪：只注入 goal 命中的人物卡与相关伏笔。"""
    w = _world()
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=3, chapter_goal="林小满前往落霞城寻医")
    assert "本章相关人物卡" in ctx
    assert "林小满" in ctx
    assert "赵无咎" not in ctx          # 未出场人物卡被裁剪
    assert "苏晚" not in ctx
    assert "林小满玉佩" in ctx          # 与林小满相关伏笔保留
    assert "江澈左臂旧伤" not in ctx    # 与出场人物无关伏笔裁剪


@pytest.mark.p1
def test_predict_fail_fallback_full(store):
    """goal 未命中任何人名 → 回退全量注入（不裁关键信息）。"""
    w = _world()
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=1, chapter_goal="主角初入宗门")
    assert "本章相关人物卡" not in ctx
    assert "人物卡：" in ctx            # 全量注入（预测失败兜底）
    assert "赵无咎" in ctx and "苏晚" in ctx


@pytest.mark.p1
def test_strong_constraints_always_injected_at_end(store):
    """强约束/锁定项 100% 注入且位于 prompt 末尾。"""
    w = _world()
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=2, chapter_goal="林小满遇险")
    assert "故事背景（强约束）" in ctx
    assert "主角设定（强约束）" in ctx
    assert "避雷（强约束）" in ctx
    assert "核心设定锁定" in ctx
    assert "世界观：" in ctx
    # 强约束区位于 ctx 最末尾（prompt 尾部权重最高）
    tail = ctx.strip()
    assert "L1·核心锁定·强约束" in tail
    assert tail.endswith("主角不得黑化")


@pytest.mark.p1
def test_switch_off_full_injection(store):
    """lorebook_trim=False → 全量注入（行为与旧版一致）。"""
    cfg = store.load_config()
    cfg.lorebook_trim = False
    store.save_config(cfg)
    w = _world()
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=3, chapter_goal="林小满前往落霞城")
    assert "人物卡：" in ctx
    assert "赵无咎" in ctx and "苏晚" in ctx and "柳如烟" in ctx
    assert "已埋伏笔：" in ctx          # 全量伏笔


@pytest.mark.p1
def test_few_chars_skip_trim(store):
    """人物 ≤4 时不裁剪（避免小书误裁），保持全量注入。"""
    cfg = store.load_config()
    store.save_config(cfg)
    w = World(
        world_view="现代都市",
        characters=[
            Character(name="江澈", role="男主", desc="记者"),
            Character(name="林小满", role="女主", desc="医生"),
            Character(name="老周", role="配角", desc="主编"),
        ],
    )
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=2, chapter_goal="江澈查案")
    assert "人物卡：" in ctx
    assert "林小满" in ctx and "老周" in ctx
