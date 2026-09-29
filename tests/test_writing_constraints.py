# -*- coding: utf-8 -*-
"""C 类问题修复测试：金手指机制卡 + 人物一致性 / 开篇锚点前置 / 母题密度 写作约束（P2）。

背景（批注 C 类根因）：
- 金手指规则未定义：面板语义歧义、历史死亡史矛盾、标题未兑现、蚀痕作用未讲 → 机制卡未在设定层固化
- 左右手横跳：疤在左腕/右腕反复变 → 无「细节一致性」约束
- 钩子太晚、情绪落点缺失：老陈头埋在末尾 → 无「开篇锚点前置」约束
- 母题复读密度过高：拇指摩挲9次等 → 无「母题密度」约束
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import World, Character, Outline
from novel_gen.pipeline import templates
from novel_gen.pipeline.memory import Memory


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "wc", cfg)


def _cfg():
    return NovelConfig(title="测试书", chapter_words=2500, total_words=10000)


def _world():
    return World(
        world_view="灰雾笼罩的墟，可死而复生的铁匠世界。",
        characters=[Character(name="陆沉", role="主角", appearance="左腕内侧有旧疤", personality="冷")],
        locked_settings=["灰卒为不可阻挡的慢压迫型怪物"],
        mechanism_rules=(
            "机制名：今日可死三次。语义：'可死次数'=今日剩余复活次数，不是已死次数；"
            "子时重置为3，每死一次减一，蚀痕随每次死亡延长，蚀痕满则永久死亡；"
            "首章必须实际用掉机制并兑现标题，数字全书一致。"
        ),
    )


@pytest.mark.p2
def test_world_schema_roundtrip_mechanism_rules():
    """World mechanism_rules 序列化/反序列化往返一致，且兼容 dict/list 非标量。"""
    w = _world()
    d = w.to_dict()
    assert d["mechanism_rules"] == w.mechanism_rules
    back = World.from_dict(d)
    assert back.mechanism_rules == w.mechanism_rules
    # 兼容 LLM 返回 dict/list
    as_list = World.from_dict({"mechanism_rules": ["规则A", "规则B"]})
    assert "规则A" in as_list.mechanism_rules and "规则B" in as_list.mechanism_rules
    # 缺失字段不报错
    assert World.from_dict({}).mechanism_rules == ""


@pytest.mark.p2
def test_world_prompt_outputs_mechanism_rules():
    """世界观生成提示词要求产出 mechanism_rules（机制语义/结算/代价/兑现标题）。"""
    cfg = _cfg()
    system, user = templates.world_prompt(cfg, Outline(title="测试", intro="x"), "")
    assert "mechanism_rules" in user
    assert "结算规则" in user
    assert "代价" in user or "终局" in user
    assert "兑现标题" in user


@pytest.mark.p2
def test_world_from_content_prompt_outputs_mechanism_rules():
    """续写模式世界观提炼提示词同样要求 mechanism_rules。"""
    cfg = _cfg()
    system, user = templates.world_from_content_prompt(cfg, "用户已有正文…")
    assert "mechanism_rules" in user
    assert "结算规则" in user


@pytest.mark.p2
def test_memory_injects_mechanism_card(store):
    """memory.build_context 在强约束区注入金手指机制卡（最高权重、永不裁剪）。"""
    w = _world()
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=1, chapter_goal="推进")
    assert "金手指机制卡" in ctx
    assert "可死次数" in ctx
    assert "子时重置为3" in ctx
    assert ctx.index("金手指机制卡") > ctx.index("L1·核心锁定")  # 在强约束区
    # 强约束区位于 prompt 最末尾（权重最高）
    assert ctx.strip().endswith("数字全书一致。")


@pytest.mark.p2
def test_chapter_and_scene_prompt_have_writing_constraints():
    """chapter/scene 提示词系统含写作约束：人物一致性 / 开篇锚点前置 / 母题密度。"""
    cfg = _cfg()
    sys_c, _ = templates.chapter_prompt(
        cfg, 1, "第一章", "入墟挖晶", "", "", "", is_final=False)
    scene = {"location": "墟", "event": "入墟挖晶", "emotion": "压抑",
             "anchors": ["蚀晶"], "target_words": 1200}
    sys_s, _ = templates.scene_prompt(cfg, 1, "第一章", 1, scene, "", "", None)
    for s in (sys_c, sys_s):
        assert "人物一致性" in s
        assert "开篇锚点前置" in s
        assert "母题密度" in s
        assert "单章内出现不超过 2 次" in s


@pytest.mark.p2
def test_writing_constraints_block_self_consistent():
    """写作约束块包含三类约束。"""
    t = templates._writing_constraints()
    assert "人物一致性" in t and "同一物理事实全文只允许一种口径" in t
    assert "开篇锚点前置" in t and "开篇 500 字" in t
    assert "母题密度" in t and "口癖" in t


@pytest.mark.p2
def test_old_world_without_mechanism_rules_no_crash(store):
    """存量 world.json 没有 mechanism_rules 字段 → memory 构建不报错、不注入该块。"""
    w = World(world_view="旧世界观", characters=[Character(name="A", role="主角")])
    store.save_world(w)
    ctx = Memory(store, 5).build_context(w, up_to_chapter=1, chapter_goal="推进")
    assert "金手指机制卡" not in ctx  # 没机制卡就不注入该块


# ---------- REQ-章内自洽与机制账本：新写作约束 + 场景提示词注入（P2） ----------


@pytest.mark.p2
def test_writing_constraints_block_new_rules():
    """写作约束块包含时间轴/单口径/内心底线/悬念句四类新规则。"""
    t = templates._writing_constraints()
    assert "时间轴" in t and "三天后" in t and "回想" in t
    assert "事实与细节单口径" in t and "复活" in t
    assert "内心活动底线" in t and "零内心" in t
    assert "悬念句口径" in t and "提前引用" in t


@pytest.mark.p2
def test_world_prompt_outputs_mechanism_rules_extended():
    """世界观提示词要求机制卡补充数字账本/时间词语义/多数字关系/历史衔接。"""
    cfg = _cfg()
    system, user = templates.world_prompt(cfg, Outline(title="测试", intro="x"), "")
    assert "数字账本" in user
    assert "时间词语义" in user or "今日" in user
    assert "多数字关系" in user
    assert "历史衔接" in user
    system2, user2 = templates.world_from_content_prompt(cfg, "已有正文…")
    assert "数字账本写法" in user2
    assert "时间词语义" in user2
    assert "多数字关系" in user2


@pytest.mark.p2
def test_scene_prompt_injects_state_hint_and_time_marker():
    """场景提示词注入【本章已推进状态】与【本场景时间锚点】+ 状态/视角/内心硬约束。"""
    cfg = _cfg()
    scene = {"location": "墟内二层", "event": "第三次死亡复活", "emotion": "麻木",
             "anchors": ["面板数字"], "target_words": 1200, "time_marker": "三天后"}
    sys_s, user_s = templates.scene_prompt(
        cfg, 1, "第一章", 3, scene, "", "尾巴",
        None, core_event="死亡", scene_state_hint="可死次数：1；疤脸已死")
    assert "【本场景时间锚点】三天后" in user_s
    assert "【本章已推进状态" in user_s
    assert "可死次数：1" in user_s and "疤脸已死" in user_s
    assert "状态衔接" in user_s and "禁止回退" in user_s
    assert "时间过渡" in user_s and "闪回" in user_s
    assert "视角" in user_s and "肉眼可见" in user_s
    assert "内心反应底线" in user_s


@pytest.mark.p2
def test_scene_prompt_no_hint_when_empty():
    """无已推进状态/时间锚点时，对应块不出现。"""
    cfg = _cfg()
    scene = {"location": "墟", "event": "入墟挖晶", "emotion": "压抑",
             "anchors": [], "target_words": 1200}
    sys_s, user_s = templates.scene_prompt(cfg, 1, "第一章", 1, scene, "", "尾巴", None)
    assert "【本章已推进状态（后续场景必须延续" not in user_s
    assert "【本场景时间锚点】" not in user_s


@pytest.mark.p2
def test_split_scenes_prompt_requires_time_marker():
    """场景拆分提示词要求 time_marker + 关键事件等权重 + 时间顺延。"""
    cfg = _cfg()
    system, user = templates.split_scenes_prompt(cfg, 1, "第一章", "三次死亡", "三日后")
    assert "time_marker" in user
    assert "每日重置" in user
    assert "不得压成一句话摘要" in user
    assert "时间必须顺延" in user
