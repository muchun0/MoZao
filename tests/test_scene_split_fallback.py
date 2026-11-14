# -*- coding: utf-8 -*-
"""真实样本回归：拆场景 LLM 调用异常时必须回退整章，不得因 events_list 未定义崩掉。

2026-11-12 M2 样本第 15 章实锤：`_split_scenes` 在 try 内才给 events_list 赋值，
异常走 except 后 return 引用未定义变量 → UnboundLocalError 终止整本书生成。
"""
from __future__ import annotations

from novel_gen.config import NovelConfig
from novel_gen.pipeline.stages import ChapterWriter


class _StubLLM:
    def chat(self, *a, **k):
        raise RuntimeError("模拟拆场景调用异常")


class _StubWriter:
    cfg = NovelConfig(title="t")

    llm = _StubLLM()

    def _memory(self):
        return None

    def _book_canon(self):
        return ""


def test_split_scenes_failure_returns_empty_lists():
    out = ChapterWriter._split_scenes(
        _StubWriter(), 15, "标题", "目标", "上文", "核心事件", [], [], silent=True)
    scenes, events_list, fallback_events, action_plan_block = out
    assert scenes == [] and events_list == [] and fallback_events == []
    assert action_plan_block == ""
