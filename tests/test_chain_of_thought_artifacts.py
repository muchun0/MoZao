# 生成过程只读扩展（设计卡 / 摘要）用例：缺失与异常必须降级为空，不抛 5xx
import pytest

from webapp.routers.projects_chapters import _design_for_chapter, _summary_for_chapter


class _Store:
    def __init__(self, design=None, chapter=None, design_error=False, chapter_error=False):
        self._design = design
        self._chapter = chapter
        self._design_error = design_error
        self._chapter_error = chapter_error

    def load_chapter_design(self):
        if self._design_error:
            raise OSError("boom")
        return self._design

    def load_chapter(self, no):
        if self._chapter_error:
            raise OSError("boom")
        return self._chapter


class _Chapter:
    def __init__(self, summary):
        self.summary = summary


def test_design_returns_matching_chapter():
    store = _Store(design={"chapters": [
        {"no": 1, "design": {"goal": "第一章目标"}},
        {"no": 2, "design": {"goal": "第二章目标"}},
    ]})
    assert _design_for_chapter(store, 2) == {"goal": "第二章目标"}
    assert _design_for_chapter(store, 1) == {"goal": "第一章目标"}


def test_design_missing_chapter_or_file_degrades_empty():
    assert _design_for_chapter(_Store(design={"chapters": []}), 3) == {}
    assert _design_for_chapter(_Store(design=None), 3) == {}
    assert _design_for_chapter(_Store(design_error=True), 3) == {}


def test_design_tolerates_malformed_entry():
    store = _Store(design={"chapters": [{"no": None}, {"no": 2}]})
    assert _design_for_chapter(store, 2) == {}


def test_summary_returns_text():
    assert _summary_for_chapter(_Store(chapter=_Chapter("本章摘要")), 1) == "本章摘要"


def test_summary_missing_or_error_degrades_empty():
    assert _summary_for_chapter(_Store(chapter=None), 1) == ""
    assert _summary_for_chapter(_Store(chapter_error=True), 1) == ""
