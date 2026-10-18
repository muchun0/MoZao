# -*- coding: utf-8 -*-
"""P1-3-B 多版本候选（Sudowrite 式）测试（docs/REQ-门禁质检正向影响.md P1-3-B）。

覆盖：
- editor.revise_candidates：生成多个不同侧重候选、去重、全部失败兜底整章重出
- store 大纲候选读写 + apply_outline_candidate（越界/正常）
- manager 开关 outline_multi_candidate 默认开启
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
from novel_gen.pipeline.editor import EditorReviewer, EditorReview


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "c", cfg)


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


def test_store_candidates_roundtrip_apply(store):
    o = _outline()
    store.save_outline_candidates([o])
    cands = store.load_outline_candidates()
    assert len(cands) == 1
    assert cands[0]["title"] == "旧书名"
    applied = store.apply_outline_candidate(0)
    assert applied is not None and applied.title == "旧书名"
    # 越界
    assert store.apply_outline_candidate(9) is None
    assert store.apply_outline_candidate(-1) is None


def test_revise_candidates_generates_multiple(store):
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.side_effect = [
        '{"updates": [{"no": 1, "goal": "候选A目标1"}]}',
        '{"updates": [{"no": 1, "goal": "候选B目标1"}]}',
        '{"updates": [{"no": 1, "goal": "候选C目标1"}]}',
    ]
    reviewer = EditorReviewer(llm)
    cands = reviewer.revise_candidates(cfg, _outline(), _review(), "调研", n=3)
    assert len(cands) >= 2
    goals = {c.chapters[0].goal for c in cands}
    assert len(goals) >= 2  # 候选间有差异
    for c in cands:
        assert len(c.chapters) == 2  # 章节数不变


def test_revise_candidates_dedup(store):
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = '{"updates": [{"no": 1, "goal": "相同目标"}]}'
    reviewer = EditorReviewer(llm)
    cands = reviewer.revise_candidates(cfg, _outline(), _review(), "调研", n=3)
    assert len(cands) == 1  # 全部相同 → 去重为 1


def test_revise_candidates_fallback_whole(store):
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.side_effect = [
        "解析失败1", "解析失败2", "解析失败3",   # diff 全部失败
        '{"title": "兜底书名", "intro": "兜底简介", "chapters": ['
        '{"no":1,"title":"第一章","goal":"g1"},{"no":2,"title":"第二章","goal":"g2"}]}',
    ]
    reviewer = EditorReviewer(llm)
    cands = reviewer.revise_candidates(cfg, _outline(), _review(), "调研", n=3)
    assert len(cands) == 1  # 兜底整章重出
    assert cands[0].title == "兜底书名"


def test_outline_multi_candidate_flag_default_true():
    assert NovelConfig(title="x").outline_multi_candidate is True
