# -*- coding: utf-8 -*-
"""P1-6 editor diff 式修正测试（P1，REQ-token优化）。

覆盖：diff 合并（章节字段/全书字段/非法项跳过）、diff 解析失败回退整章重出、
开关关闭回退整章重出、章节数不变。
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
from novel_gen.pipeline.editor import EditorReviewer, EditorReview, _apply_updates


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "e", cfg)


def _outline():
    return Outline(
        title="旧书名",
        intro="旧简介",
        chapters=[
            ChapterPlan(no=1, title="第一章", goal="旧目标1"),
            ChapterPlan(no=2, title="第二章", goal="旧目标2"),
        ],
    )


def _review():
    return EditorReview(passed=False, score=5, issues=["开篇无力"], priority_actions=["改第一章"])


@pytest.mark.p1
def test_apply_updates_chapter_fields(store):
    o = _outline()
    out = _apply_updates(o, [
        {"no": 1, "title": "第一章·改", "goal": "新目标1"},
        {"no": 0, "title": "新书名", "intro": "新简介"},
    ])
    assert out is not None
    assert out.title == "新书名" and out.intro == "新简介"
    assert out.chapters[0].title == "第一章·改"
    assert out.chapters[0].goal == "新目标1"
    assert out.chapters[1].title == "第二章"  # 未提及章节不变


@pytest.mark.p1
def test_apply_updates_invalid_skipped(store):
    o = _outline()
    out = _apply_updates(o, [
        "not-dict",
        {"no": 999, "title": "不存在章"},
        {"no": "x", "goal": "坏编号"},
    ])
    assert out is not None
    assert o.chapters[0].goal == "旧目标1"
    assert o.chapters[1].goal == "旧目标2"


@pytest.mark.p1
def test_revise_diff_merges(store):
    """diff 式修正：LLM 只输出 updates → Python 合并回大纲，章节数不变。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = '{"updates": [{"no": 1, "goal": "新目标1"}, {"no": 0, "intro": "新简介"}]}'
    reviewer = EditorReviewer(llm)
    out = reviewer.revise(cfg, _outline(), _review(), "调研")
    assert len(out.chapters) == 2
    assert out.chapters[0].goal == "新目标1"
    assert out.intro == "新简介"
    assert out.chapters[1].goal == "旧目标2"
    # 输出上限应远小于整章重出（diff 用 1200，整章用 4096）
    assert llm.chat.call_args.kwargs.get("max_tokens", 0) == 1200


@pytest.mark.p1
def test_revise_diff_fail_falls_back_whole(store):
    """diff 解析失败 → 回退整章重出（原逻辑兜底）。"""
    cfg = store.load_config()
    llm = MagicMock()
    # 第 1 次调用（diff）返回非法 JSON → 触发整章重出；第 2 次返回完整大纲
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return "不是JSON"
        return '{"title": "重出书名", "intro": "重出简介", "chapters": [{"no":1,"title":"A","goal":"a"},{"no":2,"title":"B","goal":"b"}]}'

    llm.chat.side_effect = _chat
    reviewer = EditorReviewer(llm)
    out = reviewer.revise(cfg, _outline(), _review(), "调研")
    assert calls["n"] == 2
    assert out.title == "重出书名"
    assert len(out.chapters) == 2


@pytest.mark.p1
def test_revise_diff_off_uses_whole(store):
    """editor_diff_revise=False → 直接整章重出。"""
    cfg = store.load_config()
    cfg.editor_diff_revise = False
    llm = MagicMock()
    llm.chat.return_value = '{"title": "新", "intro": "新", "chapters": [{"no":1,"title":"A","goal":"a"},{"no":2,"title":"B","goal":"b"}]}'
    reviewer = EditorReviewer(llm)
    out = reviewer.revise(cfg, _outline(), _review(), "调研")
    assert out.title == "新"
    assert len(out.chapters) == 2
    assert llm.chat.call_args.kwargs.get("max_tokens", 0) == 4096
