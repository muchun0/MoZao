# -*- coding: utf-8 -*-
"""世界观跟随主角视角逐步展开测试（任务二）。

覆盖：
1. World.world_reveals 序列化 roundtrip + 旧数据（缺字段）兼容
2. Memory.build_context 逐章分层注入：已揭示→普通区；未揭示→强约束区禁剧透
3. 无 world_reveals 时回退全量注入（旧行为）
4. world_prompt / world_from_content_prompt 均产出 world_reveals 指令
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.templates import world_from_content_prompt, world_prompt
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import ChapterPlan, Outline, World, WorldReveal


def _mk_world_with_reveals() -> World:
    return World(
        world_view="全量总纲：帝国修仙纪元，主角穿越成废柴，金手指为可死次数系统。",
        world_reveals=[
            WorldReveal(from_ch=1, content="主角开局穿越到修仙废柴身上，拥有'可死次数'系统，死一次少一次。"),
            WorldReveal(from_ch=15, content="宗门秘境中主角得知自己血脉特殊，可死次数与血脉觉醒相关。"),
            WorldReveal(from_ch=30, content="帝国背后存在上古神魔，主角身世与神魔有关。"),
        ],
    )


@pytest.mark.p1
def test_world_reveal_serialization_roundtrip():
    w = _mk_world_with_reveals()
    d = w.to_dict()
    assert len(d["world_reveals"]) == 3
    w2 = World.from_dict(d)
    assert [r.from_ch for r in w2.world_reveals] == [1, 15, 30]
    assert w2.world_reveals[1].content == w.world_reveals[1].content
    assert w2.world_view == w.world_view


@pytest.mark.p2
def test_world_reveal_missing_field_compat():
    """旧数据无 world_reveals 字段：不报错，默认空列表。"""
    w = World.from_dict({"world_view": "旧设定", "characters": []})
    assert w.world_reveals == []
    assert w.world_view == "旧设定"


@pytest.mark.p0
def test_build_context_layers_at_chapter1(tmp_path):
    cfg = NovelConfig(title="测试", total_words=10000)
    store = ProjectStore.create(tmp_path / "p", cfg)
    ctx = Memory(store, 5).build_context(_mk_world_with_reveals(), up_to_chapter=1)
    # 已揭示（from_ch=1）进普通区
    assert "当前阶段·主角已知世界观" in ctx
    assert "穿越到修仙废柴" in ctx
    # 未揭示（from_ch=15/30）进强约束区禁剧透
    assert "尚未揭示设定" in ctx
    assert "血脉特殊" in ctx
    assert "上古神魔" in ctx
    # 不再全量注入完整总纲（防止上帝视角剧透）
    assert "世界格局" not in ctx


@pytest.mark.p0
def test_build_context_layers_at_chapter16(tmp_path):
    cfg = NovelConfig(title="测试", total_words=10000)
    store = ProjectStore.create(tmp_path / "p", cfg)
    ctx = Memory(store, 5).build_context(_mk_world_with_reveals(), up_to_chapter=16)
    # from_ch=15 已揭示 → 进普通区
    assert "当前阶段·主角已知世界观" in ctx
    assert "血脉特殊" in ctx
    # from_ch=30 仍未揭示 → 强约束区禁剧透
    assert "尚未揭示设定" in ctx
    assert "上古神魔" in ctx


@pytest.mark.p1
def test_build_context_fallback_no_reveals(tmp_path):
    """旧数据（无 reveals）回退全量注入，保持原行为。"""
    cfg = NovelConfig(title="测试", total_words=10000)
    store = ProjectStore.create(tmp_path / "p", cfg)
    w = World(world_view="全量总纲内容")
    ctx = Memory(store, 5).build_context(w, up_to_chapter=3)
    assert "世界观：全量总纲内容" in ctx


@pytest.mark.p1
def test_world_prompt_includes_reveals():
    cfg = NovelConfig(title="测试", total_words=10000)
    outline = Outline(chapters=[ChapterPlan(no=1, title="一", goal="g")])
    _, user = world_prompt(cfg, outline, "")
    assert "world_reveals" in user
    assert "from_ch" in user
    assert "禁止一次写全" in user


@pytest.mark.p1
def test_world_from_content_prompt_includes_reveals():
    cfg = NovelConfig(title="测试", total_words=10000)
    _, user = world_from_content_prompt(cfg, "用户手写正文若干")
    assert "world_reveals" in user
    assert "from_ch" in user
