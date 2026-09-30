# -*- coding: utf-8 -*-
"""章节衔接质量回归测试（P1）。

背景：场景级生成路径（高质量模式/普通模式 ≥2000 字）缺少「承接上一章结尾」
约束——split_scenes_prompt 不知道上一章结尾、scene_prompt 只要求"不要写场景
过渡"、记忆上下文只注入近章 goal 而不注入大纲 transition（架构师设计的转场），
导致章节之间场景硬切、无铺垫转折（实测《万墟之上》第1章结尾在墟底石门，
第2章开头直接切到登记大厅）。

修复后断言：scene_prompt 首/后续场景均含承接硬规则；split_scenes_prompt 注入
prev_tail 并要求首场景承接；memory.build_context 注入本章 transition；_prev_tail
截取 300 字。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import templates
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline, Volume


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "fc", cfg)


def _outline_with_transition() -> Outline:
    return Outline(
        title="测试书",
        intro="测试",
        core_conflict="测试",
        main_line="测试",
        volumes=[Volume(no=1, name="卷一", intro="测试", main_conflict="测试",
                        character_growth="测试", climax="测试", start_ch=1, end_ch=2)],
        chapters=[
            ChapterPlan(no=1, title="第一章", goal="第一目标", transition="从A地回到小屋，次日清早"),
            ChapterPlan(no=2, title="第二章", goal="第二目标", transition="从小屋出发前往登记大厅"),
        ],
    )


@pytest.mark.p1
def test_scene_prompt_first_scene_requires_continuation():
    """scene_prompt 首场景必须包含「从上一章结尾直接续写」硬规则。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 2, "第二章", 1,
        {"location": "登记大厅", "event": "排队", "emotion": "压抑",
         "anchors": [], "target_words": 1200},
        "前情记忆", "上一章结尾：他踏上第一级台阶……", "",
    )
    assert "必须从【上一章节结尾】" in user
    assert "禁止硬切到无关新场景" in user
    assert "不要写场景之间的过渡总结" not in user  # 旧表述已替换为强制承接


@pytest.mark.p1
def test_scene_prompt_later_scene_requires_continuation():
    """scene_prompt 后续场景必须自然承接上一场景结尾。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 2, "第二章", 2,
        {"location": "黑市", "event": "交易", "emotion": "紧张",
         "anchors": [], "target_words": 1200},
        "前情记忆", "上一场景结尾：他走出登记大厅……", "",
    )
    assert "自然承接上一个场景的结尾" in user
    assert "禁止毫无交代的跳跃换场" in user


@pytest.mark.p1
def test_split_scenes_prompt_injects_prev_tail():
    """split_scenes_prompt 注入上一章结尾并要求首场景承接。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.split_scenes_prompt(
        cfg, 2, "第二章", "被拒绝登记", prev_tail="上一章结尾：他踏上第一级台阶……")
    assert "上一章结尾" in user
    assert "第一个场景必须承接上一章结尾" in user


@pytest.mark.p1
def test_memory_injects_chapter_transition(store):
    """记忆上下文注入本章 transition（架构师设计的转场衔接）。"""
    store.save_outline(_outline_with_transition())
    ctx = Memory(store, memory_window=5).build_context(
        None, up_to_chapter=2, chapter_goal="第二目标")
    assert "L3.5·本章转场衔接" in ctx
    assert "从小屋出发前往登记大厅" in ctx


@pytest.mark.p1
def test_prev_tail_length_300(store):
    """_prev_tail 截取上一章结尾 300 字作为衔接素材。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="承前启后。" * 200))
    writer = ChapterWriter(store.load_config(), None, store, Memory(store))
    tail = writer._prev_tail(2)
    assert len(tail) == 300
    assert "承前启后" in tail


@pytest.mark.p1
def test_scene_prompt_injects_core_event():
    """scene_prompt 注入本章核心事件，正文不得偏离大纲。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 1, "第一章", 1,
        {"location": "墟口", "event": "醒来", "emotion": "麻木",
         "anchors": [], "target_words": 1200},
        "前情", "", "",
        core_event="陆沉在墟口外碎石堆醒来，混入逃出的人群",
    )
    assert "【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】" in user
    assert "陆沉在墟口外碎石堆醒来" in user


@pytest.mark.p1
def test_split_scenes_prompt_injects_core_event():
    """split_scenes_prompt 注入核心事件，场景拆分必须覆盖。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.split_scenes_prompt(
        cfg, 1, "第一章", "目标", core_event="灰卒咬穿喉咙，墟口外复活")
    assert "【本章核心事件" in user
    assert "灰卒咬穿喉咙，墟口外复活" in user


@pytest.mark.p1
def test_chapter_prompt_injects_core_event():
    """chapter_prompt（整章路径）注入核心事件。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="跟随临时队下墟，灰卒群溃散",
    )
    assert "【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】" in user
    assert "跟随临时队下墟，灰卒群溃散" in user


@pytest.mark.p1
def test_chapter_prompt_injects_events_sequence():
    """2026-10-01 拆场景失败回退整章：chapter_events（拆场景救回的 events）注入
    事件序列 + 每事件只写一遍纪律——拆场景失败不再丢事件/让模型自发挥重复。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="死亡-复活",
        chapter_events="事件1（事件1）：陆沉被灰卒咬穿喉咙，面板 3/3→2/3，复活｜结束时机制状态：面板 2/3，蚀痕越肘弯\n事件2（事件2）：陆沉在碎石坡救下被三只赤尉围住的苏妄言｜结束时机制状态：赤尉退走",
    )
    assert "【本章事件序列" in user
    assert "每事件只写一遍" in user
    assert "陆沉在碎石坡救下被三只赤尉围住的苏妄言" in user
    assert "禁止任何事件重复出现" in user
    # 无 events 时不注入该块，但通用防重复纪律始终存在
    _, user2 = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="死亡-复活",
    )
    assert "【本章事件序列" not in user2
    assert "3.5 本章每个事件/冲突场面" in user2 and "只允许完整发生一次" in user2
