# -*- coding: utf-8 -*-
"""章节名唯一性测试：同一本小说章节名不能重复（AI 大纲 / 续写大纲 / 用户编辑全覆盖）。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_gen.config import NovelConfig, LLMConfig, SearchConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import ChapterPlan, Outline
from novel_gen.pipeline.stages import OutlineGenerator, unique_chapter_titles


def _cfg():
    return NovelConfig(
        title="唯一性测试", genre="玄幻", total_words=12500, chapter_words=2500,
        llm=LLMConfig(provider="mock", model="mock-model"),
        search=SearchConfig(provider=""),
    )


def _gen():
    return OutlineGenerator(_cfg(), None, None)  # type: ignore[arg-type]


def _outline_with(titles):
    return Outline(
        title="唯一性测试",
        intro="",
        chapters=[ChapterPlan(no=i + 1, title=tt, goal="g") for i, tt in enumerate(titles)],
    )


def test_unique_chapter_titles_appends_suffix():
    chs = [ChapterPlan(no=1, title="雨夜", goal=""), ChapterPlan(no=2, title="雨夜", goal=""),
           ChapterPlan(no=3, title="雨夜", goal=""), ChapterPlan(no=4, title="晴天", goal="")]
    unique_chapter_titles(chs)
    titles = [c.title for c in chs]
    assert titles == ["雨夜", "雨夜（二）", "雨夜（三）", "晴天"], titles


def test_placeholder_titles_renumbered():
    """占位式"第X章"（纯编号无内容）用实际章节序号重命名，天然唯一。"""
    chs = [ChapterPlan(no=1, title="第2章", goal=""), ChapterPlan(no=2, title="第1章", goal=""),
           ChapterPlan(no=3, title="第2章", goal="")]
    unique_chapter_titles(chs)
    titles = [c.title for c in chs]
    assert titles == ["第1章", "第2章", "第3章"], titles


def test_empty_titles_defaulted():
    chs = [ChapterPlan(no=1, title="", goal=""), ChapterPlan(no=2, title="  ", goal="")]
    unique_chapter_titles(chs)
    assert [c.title for c in chs] == ["第1章", "第2章"]


def test_normalize_deduplicates_and_keeps_meaningful_titles():
    """_normalize 集成：AI 大纲出现重名章时自动去重，有意义标题不被破坏。"""
    out = _outline_with(["觉醒", "觉醒", "归途", "", "觉醒"])
    norm = _gen()._normalize(out)
    titles = [c.title for c in norm.chapters]
    assert len(set(titles)) == len(titles), f"章节名重复：{titles}"
    assert titles[0] == "觉醒" and titles[2] == "归途"
    assert titles[3] == "第4章"
    assert any("（二）" in t for t in titles) and any("（三）" in t for t in titles), titles


def test_normalize_no_duplicates_when_input_unique():
    out = _outline_with(["风起", "云涌", "惊变", "决战", "终章"])
    norm = _gen()._normalize(out)
    titles = [c.title for c in norm.chapters]
    assert titles == ["风起", "云涌", "惊变", "决战", "终章"]


def test_continue_outline_merged_with_user_chapters_unique():
    """续写场景：用户章标题 + AI 规划章合并后仍唯一（用户章若重名也会被兜底去重）。"""
    from novel_gen.utils.chapter_splitter import split_chapters
    user_text = "第一章 雨夜\n正文一。\n\n第二章 雨夜\n正文二。\n"
    chs = split_chapters(user_text)
    plans = [ChapterPlan(no=i + 1, title=c["title"], goal="") for i, c in enumerate(chs)]
    plans += [ChapterPlan(no=3, title="雨夜", goal="续写"), ChapterPlan(no=4, title="终局", goal="结局")]
    unique_chapter_titles(plans)
    titles = [c.title for c in plans]
    assert len(set(titles)) == len(titles), f"续写合并后仍重复：{titles}"
    # 用户章标题原样保留（含其中重名章），AI 规划章与已有章撞名时兜底去重
    assert titles[0] == "第一章 雨夜"
    assert titles[1] == "第二章 雨夜"
    assert titles[2] == "雨夜" or titles[2] == "雨夜（二）", titles
