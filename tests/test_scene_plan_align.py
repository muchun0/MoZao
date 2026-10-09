# -*- coding: utf-8 -*-
"""REQ-scene-plan-align 拆场景规划对齐大纲人物动线测试（P1）。

覆盖：
- _derive_events_from_moves 确定性派生：→切拍/句切分/死亡-复活因果合并/最短拍合并/
  空串与单拍回退/真实大纲样例
- split_scenes_prompt 骨架注入：有 moves 注入硬骨架、无 moves 走原规划路径
- 集成链路：派生事件覆盖模型输出（desc 锁骨架、final_state 按 id 合并保留）、
  场景引用必须恰好覆盖派生 id 集合（自造/遗漏 id → 回退整章）、开关关闭回归
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
from novel_gen.store.schemas import ChapterPlan, Outline
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter, _derive_events_from_moves
from novel_gen.pipeline.templates import split_scenes_prompt


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


# 真实《万墟之上》第2章 character_moves（诊断案例：原 7 拍全部拆成换措辞版本拼接）
_CH2_MOVES = (
    "苏妄言进入铁匠铺，老陈头回避→苏妄言用术法扫描陆沉，确认他没有执能→"
    "陆沉装傻，苏妄言不耐烦，说'你这种人我见多了'→苏妄言执能意外触发（言出法随），"
    "陆沉替她挡下失控的术法碎片，死了一次→复活后陆沉继续装晕，苏妄言以为他只是被震晕→"
    "苏妄言留下一枚铜牌，说'三天后到执刑司报到，算你临时征召'→陆沉捏着铜牌，心里骂娘。"
)

# 4 个事件 + 4 个场景（场景逐字引用事件1..4），模型补充 final_state
_DERIVED_SPLIT_JSON = (
    '{"events": [{"id": "事件1", "desc": "占位", "conflict": "苏妄言", "action": "试探",'
    ' "final_state": "面板2/2"},'
    ' {"id": "事件2", "desc": "占位", "conflict": "苏妄言", "action": "试探",'
    ' "final_state": "面板2/2"},'
    ' {"id": "事件3", "desc": "占位", "conflict": "苏妄言", "action": "抵挡",'
    ' "final_state": "蚀痕至腕"},'
    ' {"id": "事件4", "desc": "占位", "conflict": "苏妄言", "action": "征召",'
    ' "final_state": "执刑司征召令已下"}],'
    ' "scenes": [{"location": "铁匠铺", "scene_event_ref": "事件1", "emotion": "平静",'
    ' "anchors": ["火炉"], "time_marker": "清晨", "target_words": 620},'
    ' {"location": "铁匠铺", "scene_event_ref": "事件2", "emotion": "警惕",'
    ' "anchors": ["火炉"], "time_marker": "紧随其后", "target_words": 620},'
    ' {"location": "铁匠铺", "scene_event_ref": "事件3", "emotion": "惊险",'
    ' "anchors": ["术法碎片"], "time_marker": "片刻后", "target_words": 620},'
    ' {"location": "铁匠铺", "scene_event_ref": "事件4", "emotion": "压抑",'
    ' "anchors": ["铜牌"], "time_marker": "稍后", "target_words": 620}],'
    ' "characters_action": []}'
)


# ---------- _derive_events_from_moves 单元 ----------

@pytest.mark.p1
def test_derive_deterministic_same_input():
    """确定性：同一 character_moves 两次派生结果逐字一致（无 LLM 采样）。"""
    a = _derive_events_from_moves(_CH2_MOVES)
    b = _derive_events_from_moves(_CH2_MOVES)
    assert a == b


@pytest.mark.p1
def test_derive_arrow_split_merge_causal():
    """→ 切拍 + 死亡-复活因果合并：5 拍 → 4 事件，'复活'拍并入死亡拍（同事件）。"""
    moves = ("陆沉在墟口遭遇灰卒，被咬喉致死→复活后面板显示2→"
             "用铁锤砸碎一只灰卒的核心→小九出现，灰卒逃窜→陆沉回到铁匠铺。")
    evs = _derive_events_from_moves(moves)
    assert len(evs) == 4
    assert evs[0]["desc"] == "陆沉在墟口遭遇灰卒，被咬喉致死→复活后面板显示2"
    assert [e["id"] for e in evs] == ["事件1", "事件2", "事件3", "事件4"]
    # 每拍 desc 逐字保留骨架原文
    assert all(e["desc"] for e in evs)
    assert "被咬喉致死→复活后面板显示2" in evs[0]["desc"]


@pytest.mark.p1
def test_derive_sentence_split_no_arrow():
    """无 → 时按句切分（。；）。"""
    moves = "陆沉进入药铺。林半夏笑脸相迎。林半夏给陆沉诊脉。陆沉离开。"
    evs = _derive_events_from_moves(moves)
    assert len(evs) == 4
    assert evs[0]["desc"].startswith("陆沉进入药铺")


@pytest.mark.p1
def test_derive_empty_and_single_beat_returns_empty():
    """空串 / 单拍 → []（调用方回退 LLM 规划路径）。"""
    assert _derive_events_from_moves("") == []
    assert _derive_events_from_moves("  ") == []
    assert _derive_events_from_moves("陆沉独自在铁匠铺打铁。") == []


@pytest.mark.p1
def test_derive_merge_shortest_beat():
    """>4 拍且无复活标记 → 合并最短拍到前拍至 ≤4。"""
    moves = "拍一最长最长最长最长→拍二→拍三→拍四→拍五→拍六。"
    evs = _derive_events_from_moves(moves)
    assert 2 <= len(evs) <= 4
    # 因果顺序保留：事件1 从"拍一"开始，最后一事件以"拍六"结束
    assert evs[0]["desc"].startswith("拍一")
    assert evs[-1]["desc"].endswith("拍六。")


@pytest.mark.p1
def test_derive_real_chapter2_four_mutually_exclusive():
    """真实《万墟之上》第2章：7 拍 → 4 个事件，死亡-复活同事件，desc 保留骨架原文。"""
    evs = _derive_events_from_moves(_CH2_MOVES)
    assert 2 <= len(evs) <= 4
    joined = "".join(e["desc"] for e in evs)
    for frag in ("苏妄言进入铁匠铺", "扫描陆沉", "挡下失控的术法碎片", "临时征召", "心里骂娘"):
        assert frag in joined, f"骨架片段丢失: {frag}"
    # 死亡-复活必须同事件（'死了一次→复活后'不得拆到两个事件）
    for e in evs:
        d = e["desc"]
        if "死了一次" in d:
            assert "复活后" in d, "死亡与复活被拆成两个事件（因果粘连合并失效）"


@pytest.mark.p1
def test_derive_all_real_chapters_count_in_range():
    """真实大纲前7章全部派生：事件数 ∈ [2,4]（场景数上限约束兼容）。"""
    import json
    p = Path(r"D:\NovelData\projects\20260928_211821_8b126a27_d6154b\outline.json")
    if not p.exists():
        pytest.skip("真实大纲不存在（离线环境）")
    o = json.loads(p.read_text(encoding="utf-8"))
    for ch in o["chapters"][:7]:
        evs = _derive_events_from_moves(ch.get("character_moves") or "")
        assert 2 <= len(evs) <= 4, f"第{ch['no']}章派生事件数 {len(evs)} 超界"


# ---------- split_scenes_prompt 骨架注入 ----------

def _prompt_user(moves: str = ""):
    cfg = NovelConfig(title="测试书", chapter_words=2500)
    _, user = split_scenes_prompt(cfg, 1, "第一章", "目标", prev_tail="", core_event="核心事件", moves=moves)
    return user


@pytest.mark.p1
def test_split_prompt_injects_skeleton_when_moves():
    """moves 非空 → 注入硬骨架段，第一步改为补字段（不要求重建事件清单）。"""
    user = _prompt_user(_CH2_MOVES)
    assert "已由编辑按人物动线确定" in user
    assert _CH2_MOVES[:20] in user            # 骨架原文注入
    assert "规划本章事件清单" not in user     # 不再让模型自行重建
    assert "逐条补充结构化字段" in user


@pytest.mark.p1
def test_split_prompt_without_moves_original():
    """moves 为空 → 保持原规划路径（模型自行生成互斥事件清单）。"""
    user = _prompt_user("")
    assert "规划本章事件清单" in user
    assert "已由编辑按人物动线确定" not in user


# ---------- 集成链路（mock LLM） ----------

def _save_outline(store, moves: str):
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    outline = Outline(
        chapters=[ChapterPlan(
            no=1, title="第一章", goal="苏妄言来铁匠铺试探陆沉",
            core_event="苏妄言盘问陆沉执能，执能意外触发，陆沉挡死一次，被临时征召",
            character_moves=moves,
        )],
    )
    store.save_outline(outline)


@pytest.mark.p1
def test_derived_flow_skeleton_in_prompt_and_events_locked(store):
    """派生链路：拆场景 prompt 注入骨架；场景引用派生 id 通过校验；
    场景 prompt 收到骨架 desc（模型无权改写）+ 模型补充的 final_state（按 id 合并保留）。"""
    _save_outline(store, _CH2_MOVES)
    llm = MagicMock()
    llm.chat.side_effect = [
        _DERIVED_SPLIT_JSON,
        "场景正文A。" * 160,
        "场景正文B。" * 160,
        "场景正文C。" * 160,
        "场景正文D。" * 160,
        "场景链路摘要",
    ]
    ch = _writer(store.load_config(), llm, store).write(1, "第一章", "目标", None, silent=True)
    # 1) 拆场景调用注入骨架
    split_user = llm.chat.call_args_list[0][0][1]
    assert "已由编辑按人物动线确定" in split_user
    # 2) 场景 prompt 收到派生事件 desc（骨架原文）+ 模型补充的 final_state
    scene_user = llm.chat.call_args_list[1][0][1]
    assert "苏妄言进入铁匠铺" in scene_user          # 事件1 desc = 骨架拍原文
    assert "面板2/2" in scene_user                    # 模型补充 final_state 被合并保留
    assert ch.content and "场景正文A" in ch.content
    assert llm.chat.call_count == 6                   # 拆场景 + 4场景 + 摘要


@pytest.mark.p1
def test_derived_ref_incomplete_falls_back_to_whole(store):
    """派生模式下模型自造/遗漏事件 id（引用集合 ≠ 派生 id 集合）→ 回退整章，
    整章 prompt 注入派生事件序列（事件不丢、不换措辞重演）。"""
    _save_outline(store, _CH2_MOVES)
    bad_json = (
        '{"events": [{"id": "事件5", "desc": "自造事件", "conflict": "苏妄言", '
        '"action": "试探", "final_state": "x"}],'
        ' "scenes": [{"location": "铁匠铺", "scene_event_ref": "事件5", "emotion": "平静",'
        ' "anchors": [], "time_marker": "清晨", "target_words": 2500}],'
        ' "characters_action": []}'
    )
    llm = MagicMock()
    llm.chat.side_effect = [
        bad_json,
        "整章回退正文。" * 520 + "【摘要】回退摘要",
    ]
    ch = _writer(store.load_config(), llm, store).write(1, "第一章", "目标", None, silent=True)
    assert llm.chat.call_count == 2                    # 拆场景失败 → 整章
    assert "整章回退正文" in ch.content
    whole_user = llm.chat.call_args_list[1][0][1]
    assert "苏妄言进入铁匠铺" in whole_user            # 派生事件注入整章 prompt


@pytest.mark.p1
def test_derive_fail_falls_back_to_llm_planning(store):
    """character_moves 缺失/派生失败 → 回退原 LLM 规划路径（prompt 无骨架，行为不变）。"""
    _save_outline(store, "")                            # moves 为空
    llm = MagicMock()
    simple_json = (
        '{"events": [{"id": "事件1", "desc": "林小满抵达落霞城", "conflict": "", '
        '"action": "", "final_state": ""}],'
        ' "scenes": [{"location": "落霞城", "scene_event_ref": "事件1", "emotion": "疲惫",'
        ' "anchors": [], "time_marker": "清晨", "target_words": 2500}],'
        ' "characters_action": []}'
    )
    llm.chat.side_effect = [
        simple_json,
        "场景正文：林小满抵达。" * 280,
        "场景链路摘要",
    ]
    ch = _writer(store.load_config(), llm, store).write(1, "第一章", "目标", None, silent=True)
    split_user = llm.chat.call_args_list[0][0][1]
    assert "规划本章事件清单" in split_user            # 原路径：模型自行规划
    assert "已由编辑按人物动线确定" not in split_user
    assert "林小满抵达" in ch.content


@pytest.mark.p1
def test_scene_plan_align_flag_off_regression(store):
    """scene_plan_align=False：即使 outline 有 character_moves，也走原 LLM 规划路径。"""
    _save_outline(store, _CH2_MOVES)
    cfg = store.load_config()
    cfg.scene_plan_align = False
    store.save_config(cfg)
    llm = MagicMock()
    simple_json = (
        '{"events": [{"id": "事件1", "desc": "苏妄言盘问陆沉", "conflict": "苏妄言", '
        '"action": "试探", "final_state": "面板2/2"}],'
        ' "scenes": [{"location": "铁匠铺", "scene_event_ref": "事件1", "emotion": "警惕",'
        ' "anchors": [], "time_marker": "清晨", "target_words": 2500}],'
        ' "characters_action": []}'
    )
    llm.chat.side_effect = [
        simple_json,
        "场景正文：苏妄言盘问。" * 280,
        "场景链路摘要",
    ]
    ch = _writer(cfg, llm, store).write(1, "第一章", "目标", None, silent=True)
    split_user = llm.chat.call_args_list[0][0][1]
    assert "规划本章事件清单" in split_user            # 回退原路径
    assert "已由编辑按人物动线确定" not in split_user
    assert "苏妄言盘问" in ch.content
