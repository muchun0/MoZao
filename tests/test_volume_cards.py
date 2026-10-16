# -*- coding: utf-8 -*-
"""二期回归（P1/P2，REQ-写作正向引导 5.5）：分卷卡（volume_cards）+ 场景字数预算收敛。

覆盖：
- schemas：VolumeCard / Outline.volume_cards 序列化 roundtrip + 旧数据兼容（无字段不崩）。
- pacing.plan_outline 确定性派生 volume_cards（goal/obstacle/climax/legacy，legacy=下一卷
  intro 首句）；只补缺失不覆盖（幂等）。
- _normalize_scene_targets：target_words 合计精确等于 chapter_words（治逐场景超长）。
- ChapterWriter._volume_card_block：卷首/卷中/卷末位置文案 + 卷末章收束与跨卷钩子。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, LLMConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (Outline, Volume, VolumeCard, ChapterPlan)
from novel_gen.pipeline.agents.pacing import PacingAgent
from novel_gen.pipeline.stages import ChapterWriter, _normalize_scene_targets
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.llm.factory import create_llm


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试", chapter_words=2200, total_words=10000)
    return ProjectStore.create(tmp_path / "p", cfg)


def _outline_with_volumes() -> Outline:
    return Outline(
        title="T", intro="I",
        volumes=[
            Volume(no=1, name="天权篇", start_ch=1, end_ch=4,
                   intro="一个被认为没有执能的少年，在泥泞里活成了执刑司的异类",
                   main_conflict="苏妄言盯上了他不死的秘密", climax="墟口蚀潮中当众死而复生"),
            Volume(no=2, name="学宫篇", start_ch=5, end_ch=8,
                   intro="少年靠借来的力量混入天衍学宫，却发现天才们各怀秘密",
                   main_conflict="学宫考核与执刑司监视双重压力", climax="大比中复制出虚无"),
        ],
        chapters=[ChapterPlan(no=i, title=f"章{i}", goal="g") for i in range(1, 9)],
    )


# ---------- schemas ----------

@pytest.mark.p2
def test_volume_card_roundtrip():
    vc = VolumeCard(volume_no=1, goal="改变局面", obstacle="主要障碍",
                    climax="卷末高潮", legacy="遗留压力")
    d = vc.to_dict()
    vc2 = VolumeCard.from_dict(d)
    assert vc2.volume_no == 1 and vc2.goal == "改变局面"
    assert vc2.obstacle == "主要障碍" and vc2.climax == "卷末高潮" and vc2.legacy == "遗留压力"
    # 旧数据缺字段 → 不崩、默认空
    vc3 = VolumeCard.from_dict({"volume_no": 2})
    assert vc3.goal == "" and vc3.legacy == ""
    vc4 = VolumeCard.from_dict("坏数据")
    assert vc4.volume_no == 0


@pytest.mark.p2
def test_outline_volume_cards_roundtrip_and_compat():
    o = Outline(volume_cards=[VolumeCard(volume_no=1, goal="g", legacy="l")])
    d = o.to_dict()
    o2 = Outline.from_dict(d)
    assert len(o2.volume_cards) == 1 and o2.volume_cards[0].goal == "g"
    assert o2.volume_cards[0].legacy == "l"
    # 旧数据无 volume_cards → 不崩、为空
    o3 = Outline.from_dict({"title": "旧", "volumes": []})
    assert o3.volume_cards == []


# ---------- pacing.plan_outline 派生 ----------

@pytest.mark.p1
def test_plan_outline_derives_volume_cards(store):
    cfg = store.load_config()
    ag = PacingAgent(cfg, MagicMock(), store)
    out2, summary = ag.plan_outline(_outline_with_volumes())
    cards = out2.volume_cards
    assert len(cards) == 2, "两卷应各派生一张卷卡"
    c1 = cards[0]
    assert c1.volume_no == 1
    assert "泥泞" in c1.goal and c1.goal == "一个被认为没有执能的少年，在泥泞里活成了执刑司的异类"
    assert c1.obstacle == "苏妄言盯上了他不死的秘密"
    assert "蚀潮" in c1.climax
    # legacy = 下一卷 intro 首句
    assert c1.legacy == "少年靠借来的力量混入天衍学宫，却发现天才们各怀秘密"
    assert summary.get("volume_cards") == 2


@pytest.mark.p1
def test_plan_outline_volume_cards_idempotent(store):
    """已有卷卡不覆盖（只补缺失），重复调用不重复追加。"""
    cfg = store.load_config()
    outline = _outline_with_volumes()
    outline.volume_cards = [VolumeCard(volume_no=1, goal="已有设计")]
    ag = PacingAgent(cfg, MagicMock(), store)
    out2, _ = ag.plan_outline(outline)
    assert len(out2.volume_cards) == 2
    assert out2.volume_cards[0].goal == "已有设计"  # 已有不覆盖
    assert out2.volume_cards[1].goal != ""         # 缺失卷补齐
    out3, _ = ag.plan_outline(out2)
    assert len(out3.volume_cards) == 2


@pytest.mark.p1
def test_plan_outline_volume_cards_last_vol_no_legacy(store):
    """末卷无下一卷 → legacy 空（不越界）。"""
    cfg = store.load_config()
    o = Outline(volumes=[Volume(no=5, name="末卷", start_ch=1, end_ch=4,
                                 intro="末卷局面", main_conflict="障碍", climax="高潮")],
                chapters=[ChapterPlan(no=i, title=f"章{i}", goal="g") for i in range(1, 5)])
    ag = PacingAgent(cfg, MagicMock(), store)
    out2, _ = ag.plan_outline(o)
    assert out2.volume_cards[0].legacy == ""


@pytest.mark.p1
def test_backfill_pacing_fills_volume_cards(tmp_path):
    """manager._backfill_pacing：旧 outline（缺卷卡）→ write_chapter 前幂等补齐卷卡。"""
    cfg = NovelConfig(title="测试书", chapter_words=2200, total_words=10000,
                      llm=LLMConfig(provider="mock"))
    store = ProjectStore.create(tmp_path / "p", cfg)
    store.save_outline(_outline_with_volumes())
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    out = pipe._backfill_pacing(store.load_outline())
    assert len(out.volume_cards) == 2, "缺卷卡的旧 outline 应补齐"
    assert out.volume_cards[0].goal != "" and out.volume_cards[0].legacy != ""
    assert out.volume_cards[1].legacy == ""  # 末卷无下一卷 → 空
    # 幂等：再跑一遍不重复叠加
    out2 = pipe._backfill_pacing(out)
    assert len(out2.volume_cards) == 2


# ---------- 场景字数预算收敛 ----------

@pytest.mark.p1
def test_normalize_scene_targets_sums_to_chapter_words():
    scenes = [{"target_words": 1200}, {"target_words": 1200}, {"target_words": 1200}]
    _normalize_scene_targets(scenes, 2200)
    total = sum(int(s["target_words"]) for s in scenes)
    assert total == 2200, f"归一后合计必须精确等于章目标，实得 {total}"


@pytest.mark.p1
def test_normalize_scene_targets_keeps_proportion():
    scenes = [{"target_words": 1400}, {"target_words": 600}]
    _normalize_scene_targets(scenes, 2000)
    assert sum(int(s["target_words"]) for s in scenes) == 2000
    assert int(scenes[0]["target_words"]) > int(scenes[1]["target_words"])


@pytest.mark.p2
def test_normalize_scene_targets_guard_edges():
    # 合计为 0 → 均分（余数补最后一个）
    scenes = [{"target_words": 0}, {"target_words": 0}]
    _normalize_scene_targets(scenes, 2200)
    assert sum(int(s["target_words"]) for s in scenes) == 2200
    # 空列表 / 章目标 <=0 → 原样不崩
    assert _normalize_scene_targets([], 2200) == []
    s2 = [{"target_words": 1200}]
    _normalize_scene_targets(s2, 0)
    assert s2[0]["target_words"] == 1200


# ---------- 卷卡注入块 ----------

def _writer():
    return object.__new__(ChapterWriter)


@pytest.mark.p1
def test_volume_card_block_vol_middle():
    """卷中章：位置=卷中推进，注入本卷目标/障碍/高潮。"""
    o = _outline_with_volumes()
    ag = PacingAgent(NovelConfig(title="T"), MagicMock(), MagicMock())
    o, _ = ag.plan_outline(o)
    blk = _writer()._volume_card_block(3, o)
    assert blk and "本卷目标·本章位置" in blk
    assert "卷中推进" in blk
    assert "泥泞" in blk and "苏妄言" in blk and "蚀潮" in blk
    assert "跨卷钩子" not in blk  # 非卷末章不注入跨卷钩子


@pytest.mark.p1
def test_volume_card_block_vol_start():
    """卷首章（≤33%）：位置=卷首铺垫。"""
    o = _outline_with_volumes()
    ag = PacingAgent(NovelConfig(title="T"), MagicMock(), MagicMock())
    o, _ = ag.plan_outline(o)
    blk = _writer()._volume_card_block(1, o)
    assert blk and "卷首铺垫" in blk


@pytest.mark.p1
def test_volume_card_block_vol_end_has_legacy():
    """卷末章：位置=卷末高潮 + 强制跨卷钩子（legacy 或兜底文案）。"""
    o = _outline_with_volumes()
    ag = PacingAgent(NovelConfig(title="T"), MagicMock(), MagicMock())
    o, _ = ag.plan_outline(o)
    blk = _writer()._volume_card_block(4, o)
    assert blk and "卷末高潮" in blk
    assert "跨卷钩子" in blk
    assert "学宫" in blk  # legacy=下一卷 intro 首句


@pytest.mark.p2
def test_volume_card_block_guard_edges():
    """无卷卡 / 章不在任何卷区间 / outline 为空 → 空串（降级安全）。"""
    w = _writer()
    assert w._volume_card_block(3, Outline()) == ""
    assert w._volume_card_block(99, _outline_with_volumes()) == ""
    assert w._volume_card_block(3, None) == ""
