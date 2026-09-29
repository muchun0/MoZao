# -*- coding: utf-8 -*-
"""多Agent协作分层 · 数据结构扩展（REQ-多Agent协作分层优化，阶段A）—— P1 用例。

覆盖：
1. World 新增底层锚点字段（theme/conflict/arc/motives/goldfinger/taboos）解析与往返
2. foreshadowing 从字符串列表升级为结构化 ForeshadowItem（旧数据兼容）
3. GoldfingerRules 五要素完整性判定
4. Outline 新增 key_nodes / volume_pacing 往返
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.store.schemas import (ArcCard, CharacterMotive, ConflictLayer,
                                     ForeshadowItem, GoldfingerRules, KeyNode,
                                     Outline, VolumePacing, World)


@pytest.mark.p1
def test_world_new_anchor_fields_roundtrip():
    w = World.from_dict({
        "world_view": "灵气复苏",
        "theme_proposition": "失去一切后重新定义自我",
        "conflict_matrix": [
            {"level": "personal", "description": "与恐惧斗争"},
            {"level": "interpersonal", "description": "与旧友决裂"},
            {"level": "world", "description": "对抗制度"},
        ],
        "protagonist_arc": {"belief": "复仇", "flaw": "偏执",
                            "change_nodes": ["放过仇人", "重建"],
                            "final_insight": "活着比复仇难"},
        "character_motives": [{"name": "阿离", "goal": "守护", "behavior_logic": "先动手",
                               "link_to_protagonist": "青梅竹马"}],
        "goldfinger_rules": {"trigger": "濒死", "upper_limit": "每日一次",
                             "lower_limit": "代价递增", "cost": "十年寿命",
                             "growth_path": "吞噬灵魂"},
        "setting_taboos": ["复活无代价"],
    })
    assert w.theme_proposition == "失去一切后重新定义自我"
    assert len(w.conflict_matrix) == 3
    assert isinstance(w.conflict_matrix[0], ConflictLayer)
    assert w.protagonist_arc.belief == "复仇"
    assert isinstance(w.protagonist_arc, ArcCard)
    assert isinstance(w.character_motives[0], CharacterMotive)
    assert w.goldfinger_rules.is_complete()
    assert w.setting_taboos == ["复活无代价"]
    # 往返一致
    w2 = World.from_dict(w.to_dict())
    assert w2.goldfinger_rules.is_complete()
    assert w2.protagonist_arc.final_insight == "活着比复仇难"
    assert len(w2.conflict_matrix) == 3


@pytest.mark.p1
def test_world_foreshadowing_structured_and_old_compat():
    # 旧数据：字符串列表 → 结构化 ForeshadowItem，reveal_ch=None（待补）
    w = World.from_dict({"world_view": "w", "foreshadowing": ["旧伏笔A", "旧伏笔B"]})
    assert len(w.foreshadowing) == 2
    assert isinstance(w.foreshadowing[0], ForeshadowItem)
    assert w.foreshadowing[0].name == "旧伏笔A"
    assert w.foreshadowing[0].reveal_ch is None
    assert w.foreshadowing[0].level == "medium"
    # 新数据：结构化条目
    w2 = World.from_dict({"foreshadowing": [
        {"id": "f1", "name": "项链", "buried_ch": 3, "reveal_ch": 50, "level": "large", "content": "来历"},
    ]})
    assert w2.foreshadowing[0].reveal_ch == 50
    assert w2.foreshadowing[0].level == "large"
    # 往返
    w3 = World.from_dict(w2.to_dict())
    assert w3.foreshadowing[0].name == "项链"
    assert w3.foreshadowing[0].buried_ch == 3


@pytest.mark.p1
def test_goldfinger_is_complete():
    gf = GoldfingerRules.from_dict({"trigger": "濒死"})
    assert gf.is_complete() is False
    full = GoldfingerRules(trigger="t", upper_limit="u", lower_limit="l",
                           cost="c", growth_path="g")
    assert full.is_complete() is True
    empty = GoldfingerRules()
    assert empty.is_complete() is False


@pytest.mark.p1
def test_outline_key_nodes_and_volume_pacing_roundtrip():
    o = Outline.from_dict({
        "title": "t",
        "key_nodes": [{"kind": "激励事件", "chapter_no": 1, "description": "穿越"}],
        "volume_pacing": [{"volume_no": 1, "payoff_density": "high",
                           "climax_ch": 30, "transition_range": "1-30"}],
    })
    assert isinstance(o.key_nodes[0], KeyNode)
    assert o.key_nodes[0].kind == "激励事件"
    assert isinstance(o.volume_pacing[0], VolumePacing)
    o2 = Outline.from_dict(o.to_dict())
    assert o2.key_nodes[0].chapter_no == 1
    assert o2.volume_pacing[0].climax_ch == 30


@pytest.mark.p1
def test_world_missing_anchors_returns_defaults():
    # 旧项目（缺全部新字段）不崩，回退默认
    w = World.from_dict({"world_view": "w", "foreshadowing": []})
    assert w.theme_proposition == ""
    assert w.conflict_matrix == []
    assert w.protagonist_arc is None
    assert w.goldfinger_rules is None
    assert w.setting_taboos == []
