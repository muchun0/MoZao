# -*- coding: utf-8 -*-
"""产物字数统计单测（novel_gen/store/wordcount.py，计费字数的唯一事实来源）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.store.schemas import Chapter, Outline, Volume, ChapterPlan, World, Character
from novel_gen.store.wordcount import count_words


def test_chapter_counts_content_chars():
    assert count_words("chapter", Chapter(no=1, title="第一章", content="内容" * 10)) == 20
    assert count_words("chapter", {"content": "abc"}) == 3
    assert count_words("chapter", {"content": None}) == 0


def test_outline_counts_scalars_and_children():
    outline = Outline(title="总纲", intro="简介", volumes=[
        Volume(name="第一卷", intro="卷简介"),
    ], chapters=[
        ChapterPlan(no=1, title="标题", goal="目标"),
    ])
    expected = len("总纲") + len("简介") + len("第一卷") + len("卷简介") + len("标题") + len("目标")
    assert count_words("outline", outline) == expected


def test_volume_counts_intro_and_plans():
    payload = {"name": "第一卷", "intro": "卷简介",
               "chapters": [{"title": "标题", "goal": "目标"}, {"title": "标题2", "goal": "目标2"}]}
    expected = sum(len(s) for s in ("第一卷", "卷简介", "标题", "目标", "标题2", "目标2"))
    assert count_words("volume", payload) == expected


def test_world_counts_all_nested_text():
    world = World(world_view="世界观说明", characters=[Character(name="张三", desc="描述")])
    assert count_words("world", world) == len("世界观说明") + len("张三") + len("描述")
    assert count_words("world", {"a": ["b", {"c": "dd"}], "n": 1}) == 3


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        count_words("unknown", {})
