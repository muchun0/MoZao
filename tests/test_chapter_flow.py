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
