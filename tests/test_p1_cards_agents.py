# -*- coding: utf-8 -*-
"""P1 回归（P1，多Agent协作分层优化·扩展）：开篇 Agent / 结局卡+结局 Agent / 物品卡+等级卡。

覆盖：
- schemas：ItemCard/PowerLevel/PowerLevelSystem/EndingCard 序列化 roundtrip + World 兼容旧数据。
- world_prompt 输出要求含 items/power_system；opening_prompt/ending_prompt 存在且结构完整。
- OpeningAgent.apply / EndingAgent.apply 幂等与回填。
- architect 编排接线（opening/ending 子 Agent 存在）。
- memory.build_context 注入物品/等级/结局卡到强约束区。
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
from novel_gen.store.schemas import (World, ItemCard, PowerLevel, PowerLevelSystem,
                                     EndingCard, ChapterPlan, Outline)
from novel_gen.pipeline.skills import STAGE_BY_ROLE
from novel_gen.pipeline.templates import world_prompt, opening_prompt, ending_prompt
from novel_gen.pipeline.agents.architect import ArchitectAgent
from novel_gen.pipeline.agents.opening import OpeningAgent
from novel_gen.pipeline.agents.ending import EndingAgent
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "p", cfg)


def _world_with_cards() -> World:
    return World(
        world_view="设定",
        items=[ItemCard(name="赤阳果", origin="墟底", cost="每月一次",
                        restriction="需力量匹配", effect="回复体力")],
        power_system=PowerLevelSystem(
            levels=[PowerLevel(name="炼气", range="1-9 重", requirement="引气入体",
                               capability="体魄强化"),
                    PowerLevel(name="筑基", range="1-9 重", requirement="结丹",
                               capability="灵力外放")],
            protagonist_level="炼气三重", progression_rule="须根基圆满才可突破",
        ),
        ending_card=EndingCard(end_state="墟门闭合，主角归乡",
                               theme_landing="重新定义自我",
                               protagonist_choice="放弃金手指", character_fates=["A归乡"],
                               tone="遗憾"),
    )


@pytest.mark.p1
def test_item_power_ending_schemas_roundtrip():
    """P1：新卡 to_dict/from_dict 往返一致；World 序列化含新字段。"""
    w = _world_with_cards()
    d = w.to_dict()
    w2 = World.from_dict(d)
    assert len(w2.items) == 1 and w2.items[0].name == "赤阳果"
    assert w2.power_system.levels[0].name == "炼气"
    assert w2.power_system.protagonist_level == "炼气三重"
    assert w2.ending_card.tone == "遗憾"
    assert w2.ending_card.protagonist_choice == "放弃金手指"


@pytest.mark.p2
def test_new_cards_old_data_compat():
    """P1：旧 World dict 无新字段，from_dict 不崩、新字段为空。"""
    w = World.from_dict({"world_view": "旧"})
    assert w.items == [] and w.power_system is None and w.ending_card is None


@pytest.mark.p1
def test_stage_by_role_has_ending_opening():
    assert "ending" in STAGE_BY_ROLE and STAGE_BY_ROLE["ending"]
    assert "opening" in STAGE_BY_ROLE and STAGE_BY_ROLE["opening"]


@pytest.mark.p1
def test_world_prompt_has_items_power(store):
    cfg = store.load_config()
    outline = Outline(title="T", intro="I", chapters=[
        ChapterPlan(no=1, title="一", goal="g")])
    sys_s, user_s = world_prompt(cfg, outline, "")
    assert "- items：" in user_s
    assert "- power_system：" in user_s


@pytest.mark.p2
def test_opening_prompt_and_agent(store):
    cfg = store.load_config()
    outline = Outline(title="T", intro="I", chapters=[
        ChapterPlan(no=1, title="一", goal="g1"),
        ChapterPlan(no=2, title="二", goal="g2"),
        ChapterPlan(no=3, title="三", goal="g3"),
        ChapterPlan(no=4, title="四", goal="g4"),
    ])
    sys_s, user_s = opening_prompt(cfg, outline)
    assert "core_event" in user_s and "前 3 章" in user_s
    # OpeningAgent.apply 幂等：前 3 章已有 core_event → 不调 LLM
    for c in outline.chapters[:3]:
        c.core_event = "已有设计"
    llm = MagicMock()
    ag = OpeningAgent(cfg, llm, store)
    out2, summary = ag.apply(outline)
    assert summary.get("cached") is True
    llm.chat_json.assert_not_called()
    # 前 3 章无 core_event → 调 LLM 回填
    for c in outline.chapters[:3]:
        c.core_event = ""
    llm.chat_json.return_value = {"chapters": [
        {"no": 1, "core_event": "主角被追杀立住", "goal": "立住主角+悬念"},
        {"no": 2, "core_event": "反杀并发现金手指", "goal": "冲突升级"},
    ]}
    out3, summary3 = ag.apply(outline)
    assert summary3.get("updated") == 2
    assert outline.chapters[0].core_event == "主角被追杀立住"
    assert outline.chapters[1].core_event == "反杀并发现金手指"


@pytest.mark.p1
def test_ending_agent_apply(store):
    cfg = store.load_config()
    world = World(world_view="设定")
    outline = Outline(title="T", intro="I", chapters=[ChapterPlan(no=1, title="一", goal="g")])
    llm = MagicMock()
    llm.chat_json.return_value = {"end_state": "墟门闭合", "theme_landing": "回归主题",
                                  "protagonist_choice": "放弃金手指",
                                  "character_fates": ["A归乡"], "tone": "遗憾"}
    ag = EndingAgent(cfg, llm, store)
    w2, summary = ag.apply(world, outline)
    assert summary.get("cached") is False
    assert w2.ending_card is not None and w2.ending_card.tone == "遗憾"
    # 幂等：再次调用不调 LLM
    w3, summary3 = ag.apply(w2, outline)
    assert summary3.get("cached") is True


@pytest.mark.p1
def test_architect_has_opening_ending(store):
    cfg = store.load_config()
    arch = ArchitectAgent(cfg, MagicMock(), store)
    assert hasattr(arch, "opening") and hasattr(arch, "ending")


@pytest.mark.p1
def test_memory_injects_cards(store):
    """P1：物品/等级/结局卡进强约束区。"""
    cfg = store.load_config()
    world = _world_with_cards()
    mem = Memory(store, cfg.memory_window)
    ctx = mem.build_context(world, up_to_chapter=3, chapter_goal="目标")
    assert "物品/道具卡" in ctx
    assert "赤阳果" in ctx
    assert "等级/境界体系" in ctx
    assert "炼气三重" in ctx
    assert "结局卡" in ctx
    assert "放弃金手指" in ctx
