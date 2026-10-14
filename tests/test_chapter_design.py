# -*- coding: utf-8 -*-
"""章级设计卡核心字段接线测试（REQ-chapter-design 一期）。

覆盖：
- P1：确定性派生 A1–A7（goal/conflict 阶梯/turning_point/emotion/cliffhanger/出场角色/对白功能）；
       emotion_curve 补充源命中、空数据降级；store 读写 roundtrip；
       templates scene_prompt/chapter_prompt 的 chapter_design 参数渲染；
       stages._chapter_design_block 懒读注入与开关关闭降级；
       manager._backfill_pacing 幂等补齐 key_nodes/emotion_curve。
- P0：接线幂等——_build_chapter_design 已存在即复用、不重复覆盖；
       开关关闭时不生成不注入。
- P2：事件账本应发生 vs 已消费对账（弱覆盖，只产 warning 不阻断）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, LLMConfig
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline import templates
from novel_gen.pipeline.chapter_design import (
    build_chapter_designs, render_block, reconcile_event_ledger,
)
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (
    ChapterPlan, Character, ConflictLayer, EmotionBeat, Outline, Volume, World,
)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "cd", cfg)


def _world():
    return World(
        world_view="墟界，灰雾笼罩。",
        characters=[
            Character(name="林小满", role="女主", desc="坚韧"),
            Character(name="江澈", role="男主", desc="剑修"),
        ],
        conflict_matrix=[
            ConflictLayer(level="个人", description="林小满求生欲与承担责任的抉择"),
            ConflictLayer(level="人际", description="林小满与江澈之间的信任与试探"),
            ConflictLayer(level="世界", description="墟界蚀化对凡人的结构性吞噬"),
        ],
    )


def _outline():
    return Outline(
        title="测试书", intro="测", core_conflict="测", main_line="测",
        volumes=[Volume(no=1, name="卷一", intro="测", main_conflict="测",
                        character_growth="测", climax="测", start_ch=1, end_ch=2)],
        chapters=[
            ChapterPlan(no=1, title="第一章", goal="林小满大战灰卒救下江澈",
                        core_event="林小满被灰卒咬死一次后复活反杀",
                        conflict="生存压力与实力不足的冲突，只能靠死一次换情报",
                        emotion="压抑中带一丝荒诞冷幽默",
                        cliffhanger="梦里灰雾中声音数三二一",
                        character_moves="遭遇灰卒→被咬死→复活反杀", tag="开局"),
            ChapterPlan(no=2, title="第二章", goal="江澈负伤，林小满寻医问药",
                        core_event="林小满带江澈穿过黑市求医",
                        conflict="在黑市被中间人勒索，双方对峙谈判",
                        emotion="压抑到极致后见到一线转机",
                        character_moves="穿过黑市→被勒索→讨价还价"),
        ],
    )


# ---------- P1：确定性派生 ----------

@pytest.mark.p1
def test_build_has_all_chapters(store):
    data = build_chapter_designs(store, _world(), _outline())
    assert data["version"] == 1
    assert len(data["chapters"]) == 2
    assert data["chapters"][0]["no"] == 1


@pytest.mark.p1
def test_derives_goal_conflict_emotion_cliffhanger(store):
    data = build_chapter_designs(store, _world(), _outline())
    d1 = data["chapters"][0]["design"]
    assert d1["goal"].startswith("林小满大战灰卒")
    # A2：冲突核心（章级 conflict 直出，真未接线主源）
    assert "生存压力" in d1["conflict"]["core"]
    # conflict_matrix 三层阶梯齐全
    tiers = [x["tier"] for x in d1["conflict"]["ladder"]]
    assert any("低" in t for t in tiers) and any("顶" in t for t in tiers)
    # A4：情绪落点主源 = 章级 emotion
    assert "荒诞冷幽默" in d1["emotion"]["note"]
    # A5：cliffhanger
    assert "三二一" in d1["cliffhanger"]


@pytest.mark.p1
def test_emotion_curve_supplementary(store):
    """A4：emotion_curve 非空且命中本章区间 → 追加跨章配比参照。"""
    outline = _outline()
    outline.emotion_curve = [EmotionBeat(ch_from=1, ch_to=2, emotion_type="压抑",
                                          intensity=4, note="第1-2章压抑区间")]
    data = build_chapter_designs(store, _world(), outline)
    d1 = data["chapters"][0]["design"]
    assert d1["emotion"]["curve"]["type"] == "压抑"
    assert d1["emotion"]["curve"]["intensity"] == 4


@pytest.mark.p1
def test_appearing_chars_and_weights(store):
    data = build_chapter_designs(store, _world(), _outline())
    chars = data["chapters"][0]["design"]["characters"]
    names = {c["name"]: c["weight"] for c in chars}
    # 第一章 goal/core_event/moves 含林小满与江澈 → 均出场
    assert "林小满" in names and "江澈" in names
    assert names["林小满"] == "main" and names["江澈"] == "main"


@pytest.mark.p1
def test_dialogue_functions_heuristic(store):
    data = build_chapter_designs(store, _world(), _outline())
    funcs = data["chapters"][1]["design"]["dialogue_functions"]  # 第二章 conflict 含"对峙谈判"
    assert any("推进剧情" in f for f in funcs)
    assert any("制造冲突" in f for f in funcs)


@pytest.mark.p1
def test_render_block_soft_constraint(store):
    data = build_chapter_designs(store, _world(), _outline())
    block = render_block(data["chapters"][0]["design"])
    assert "本章设计卡" in block
    assert "冲突核心" in block
    assert "情绪落点" in block
    assert "开放参考" in block  # 关系模板开放指引，非白名单
    assert render_block({}) == ""


@pytest.mark.p1
def test_degradation_empty(store):
    """world/outline 缺失或异常 → 空 dict（调用方跳过落盘，行为零退化）。"""
    assert build_chapter_designs(store, None, _outline()) == {}
    assert build_chapter_designs(store, _world(), None) == {}


@pytest.mark.p1
def test_store_roundtrip(store):
    data = {"version": 1, "chapters": [{"no": 1, "design": {"goal": "测试目标"}}]}
    store.save_chapter_design(data)
    assert store.load_chapter_design() == data
    # 空 data 不落盘、不覆盖已有文件（幂等）
    store.save_chapter_design({})
    assert store.load_chapter_design() == data


# ---------- P1：templates 渲染 ----------

@pytest.mark.p1
def test_scene_prompt_injects_chapter_design():
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 1, "第一章", 1,
        {"location": "墟口", "event": "醒来", "emotion": "麻木", "anchors": [], "target_words": 1200},
        "前情记忆", "", "", core_event="醒来", chapter_design="- 本章冲突核心：生存压力",
    )
    assert "【本章设计卡" in user
    assert "生存压力" in user


@pytest.mark.p1
def test_chapter_prompt_injects_chapter_design():
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="核心事件", chapter_design="- 本章情绪落点：压抑爆发",
    )
    assert "【本章设计卡" in user
    assert "压抑爆发" in user


# ---------- P1：stages 懒读注入 ----------

@pytest.mark.p1
def test_writer_design_block_loaded(store):
    """ChapterWriter._chapter_design_block 命中本章 → 返回渲染块；无卡 → 空串。"""
    store.save_world(_world())
    store.save_outline(_outline())
    data = build_chapter_designs(store, _world(), _outline())
    store.save_chapter_design(data)
    writer = ChapterWriter(store.load_config(), None, store, Memory(store))
    block = writer._chapter_design_block(1)
    assert "本章设计卡" in block
    # 无设计卡的章号 → 空串
    assert writer._chapter_design_block(999) == ""


@pytest.mark.p1
def test_writer_design_block_switch_off(store):
    cfg = store.load_config()
    cfg.chapter_design = False
    store.save_config(cfg)
    data = build_chapter_designs(store, _world(), _outline())
    store.save_chapter_design(data)
    writer = ChapterWriter(store.load_config(), None, store, Memory(store))
    assert writer._chapter_design_block(1) == ""


# ---------- P1：manager._backfill_pacing 幂等补齐 ----------

@pytest.mark.p1
def test_backfill_pacing_fills_empty_fields(tmp_path):
    """扩写/复用短路路径下补齐 key_nodes/volume_pacing/emotion_curve（只补缺失）。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000,
                      llm=LLMConfig(provider="mock"))
    store = ProjectStore.create(tmp_path / "p", cfg)
    store.save_outline(_outline())
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    out = pipe._backfill_pacing(store.load_outline())
    assert len(out.key_nodes) >= 1          # 激励事件 + 卷高潮 + 终极高潮
    assert len(out.emotion_curve) >= 1     # 由各章 emotion/tag 聚合
    assert len(out.volume_pacing) >= 1
    # 幂等：再跑一遍不重复叠加（数量不增）
    n_nodes = len(out.key_nodes)
    out2 = pipe._backfill_pacing(out)
    assert len(out2.key_nodes) == n_nodes


# ---------- P0：接线幂等 ----------

@pytest.mark.p0
def test_build_chapter_design_idempotent(tmp_path):
    """已存在即复用：重复 _build_chapter_design 不覆盖（不重算）。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000,
                      llm=LLMConfig(provider="mock"))
    store = ProjectStore.create(tmp_path / "p", cfg)
    store.save_world(_world())
    store.save_outline(_outline())
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    pipe._build_chapter_design(_world())
    first = store.load_chapter_design()
    assert first is not None and len(first["chapters"]) == 2
    # 手工改一处再调一次 → 应复用旧文件，不被重算覆盖
    first["chapters"][0]["design"]["goal"] = "人工改过的目标"
    store.save_chapter_design(first)
    pipe._build_chapter_design(_world())
    again = store.load_chapter_design()
    assert again["chapters"][0]["design"]["goal"] == "人工改过的目标"


@pytest.mark.p0
def test_build_chapter_design_off_noop(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000,
                      llm=LLMConfig(provider="mock"))
    cfg.chapter_design = False
    store = ProjectStore.create(tmp_path / "p", cfg)
    store.save_world(_world())
    store.save_outline(_outline())
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    pipe._build_chapter_design(_world())
    assert store.load_chapter_design() is None


# ---------- P2：事件账本对账（弱覆盖） ----------

@pytest.mark.p2
def test_reconcile_event_ledger_warnings():
    """规划事件 vs 已消费对账：缺消费/越权消费产 warning，不抛异常。"""
    warns = reconcile_event_ledger(["e1", "e2", "e3"], ["e1", "e2"])
    assert any("e3" in w for w in warns)
    warns2 = reconcile_event_ledger(["e1"], ["e1", "e9"])
    assert any("e9" in w for w in warns2)
    # 完全一致 → 无 warning
    assert reconcile_event_ledger(["e1"], ["e1"]) == []
