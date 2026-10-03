# -*- coding: utf-8 -*-
"""P2 回归（P2，多Agent协作分层优化·扩展）：对白 Agent / 情绪曲线卡 / 章末钩子 / 场景落盘 / 术语统一卡。

覆盖：
- schemas：TerminologyItem/EmotionBeat/ChapterPlan.cliffhanger/World.terminology/
  dialogue_guideline/Outline.emotion_curve 序列化 roundtrip + 旧数据兼容。
- pacing.plan_outline 聚合情绪曲线卡（零 LLM）。
- DialogueAgent.build_guideline 聚合人物口吻。
- store.save_scenes/load_scenes 场景落盘。
- memory 注入术语卡/对话风格/章末钩子。
- 大纲 prompt 含 cliffhanger 字段。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (World, Character, Outline, ChapterPlan,
                                     TerminologyItem, EmotionBeat)
from novel_gen.pipeline.agents.pacing import PacingAgent
from novel_gen.pipeline.agents.dialogue import DialogueAgent
from novel_gen.pipeline.skills import STAGE_BY_ROLE
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.templates import outline_prompt, world_prompt


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "p", cfg)


def _outline_with_chapters() -> Outline:
    return Outline(title="T", intro="I", chapters=[
        ChapterPlan(no=1, title="一", goal="g", emotion="爽点打脸", tag="爽点"),
        ChapterPlan(no=2, title="二", goal="g", emotion="爽点升级", tag="爽点"),
        ChapterPlan(no=3, title="三", goal="g", emotion="虐心", tag="虐点"),
        ChapterPlan(no=4, title="四", goal="g", emotion="悬念", tag="悬念"),
    ])


@pytest.mark.p2
def test_p2_schemas_roundtrip_and_compat():
    w = World(
        terminology=[TerminologyItem(name="墟口", aliases=["墟口门", "墟界口"], definition="出口")],
        dialogue_guideline="口吻区分规则",
    )
    d = w.to_dict()
    w2 = World.from_dict(d)
    assert w2.terminology[0].name == "墟口" and "墟口门" in w2.terminology[0].aliases
    assert w2.dialogue_guideline == "口吻区分规则"
    # 旧数据无新字段 → 不崩、为空
    w3 = World.from_dict({"world_view": "旧"})
    assert w3.terminology == [] and w3.dialogue_guideline == ""


@pytest.mark.p2
def test_chapter_cliffhanger_roundtrip():
    cp = ChapterPlan(no=1, title="一", goal="g", cliffhanger="敌人逼近门口")
    d = cp.to_dict()
    cp2 = ChapterPlan.from_dict(d)
    assert cp2.cliffhanger == "敌人逼近门口"
    cp3 = ChapterPlan.from_dict({"no": 1})
    assert cp3.cliffhanger == ""


@pytest.mark.p1
def test_pacing_aggregates_emotion_curve(store):
    """P2-2：pacing.plan_outline 基于各章 emotion 聚合成情绪曲线区间（零 LLM）。"""
    cfg = store.load_config()
    outline = _outline_with_chapters()
    ag = PacingAgent(cfg, MagicMock(), store)
    out2, summary = ag.plan_outline(outline)
    curve = out2.emotion_curve
    assert curve, "应产出情绪曲线"
    # 1-2 章爽点合并为一个区间，3 章虐点、4 章悬念
    assert curve[0].emotion_type == "爽点" and curve[0].ch_from == 1 and curve[0].ch_to == 2
    assert any(e.emotion_type == "虐点" for e in curve)
    assert any(e.emotion_type == "悬念" for e in curve)
    assert summary.get("emotion_curve") == len(curve)


@pytest.mark.p1
def test_pacing_emotion_curve_idempotent(store):
    cfg = store.load_config()
    outline = _outline_with_chapters()
    outline.emotion_curve = [EmotionBeat(ch_from=1, ch_to=2, emotion_type="爽点")]
    ag = PacingAgent(cfg, MagicMock(), store)
    out2, _ = ag.plan_outline(outline)
    assert len(out2.emotion_curve) == 1  # 已有则不覆盖


@pytest.mark.p1
def test_dialogue_agent_builds_guideline(store):
    """P2-1：DialogueAgent 聚合人物 speech_style 生成对话风格指南（幂等）。"""
    cfg = store.load_config()
    world = World(world_view="设定", characters=[
        Character(name="主角", speech_style="简短冷硬"),
        Character(name="反派", speech_style="阴柔带笑"),
    ])
    ag = DialogueAgent(cfg, MagicMock(), store)
    w2, summary = ag.build_guideline(world)
    assert summary.get("cached") is False
    assert "主角：简短冷硬" in w2.dialogue_guideline
    assert "反派：阴柔带笑" in w2.dialogue_guideline
    assert "对话节奏" in w2.dialogue_guideline
    # 幂等
    w3, summary3 = ag.build_guideline(w2)
    assert summary3.get("cached") is True


@pytest.mark.p2
def test_scenes_store_roundtrip(store):
    """P2-4：场景落盘读写。"""
    store.save_scenes(1, [{"id": "s1", "desc": "相遇"}], {"hp": 10})
    store.save_scenes(2, [{"id": "s2", "desc": "战斗"}])
    data = store.load_scenes()
    assert "1" in data and data["1"]["scenes"][0]["id"] == "s1"
    assert data["1"]["state"]["hp"] == 10
    assert "2" in data and data["2"]["state"] == {}


@pytest.mark.p1
def test_stage_by_role_has_dialogue():
    assert "dialogue" in STAGE_BY_ROLE and STAGE_BY_ROLE["dialogue"] == ["world"]


@pytest.mark.p1
def test_memory_injects_terminology_dialogue(store):
    """P2-5/P2-1：术语卡 + 对话风格进强约束区。"""
    cfg = store.load_config()
    world = World(
        world_view="设定",
        terminology=[TerminologyItem(name="墟口", aliases=["墟口门"], definition="出口")],
        dialogue_guideline="口吻区分：主角简短冷硬",
    )
    mem = Memory(store, cfg.memory_window)
    ctx = mem.build_context(world, up_to_chapter=3, chapter_goal="目标")
    assert "术语统一" in ctx and "墟口" in ctx and "禁止写法" in ctx
    assert "对话风格" in ctx and "口吻区分" in ctx


@pytest.mark.p2
def test_outline_prompt_has_cliffhanger(store):
    cfg = store.load_config()
    sys_s, user_s = outline_prompt(cfg, "", batch=(1, 3, ""))
    assert "cliffhanger" in user_s
    sys2, user2 = outline_prompt(cfg, "")
    assert "cliffhanger" in user2
    s3, u3 = world_prompt(cfg, Outline(title="T", intro="I", chapters=[ChapterPlan(no=1, title="一", goal="g")]), "")
    assert "- terminology：" in u3
