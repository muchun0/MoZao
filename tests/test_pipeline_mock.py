# -*- coding: utf-8 -*-
"""端到端流水线测试：用 mock provider 无 Key 跑通 调研→大纲→人物→逐章→导出。
"""
import pytest

from novel_gen.config import LLMConfig, NovelConfig
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.store.project import ProjectStore


def _pipeline(tmp_path, total_words=6000, chapter_words=1000):
    cfg = NovelConfig(
        title="端到端测试书",
        genre="玄幻",
        total_words=total_words,
        chapter_words=chapter_words,
        llm=LLMConfig(provider="mock"),
    )
    store = ProjectStore.create(tmp_path, cfg)
    llm = create_llm(cfg.llm)
    return NovelPipeline(cfg, store, llm), store


def test_full_pipeline_mock(tmp_path):
    pipeline, store = _pipeline(tmp_path)  # 6 章

    pipeline.run_research(silent=True)
    outline = pipeline.run_outline(silent=True)
    world = pipeline.run_world(silent=True)
    assert len(outline.chapters) == 6
    assert all(c.no == i + 1 for i, c in enumerate(outline.chapters))
    assert world.world_view
    assert world.characters

    for no in range(1, 7):
        pipeline.write_chapter(no, silent=True)

    done, total = pipeline.progress()
    assert (done, total) == (6, 6)

    out = pipeline.export(silent=True)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "第1章" in text and "第6章" in text


def test_resume_continues_from_partial(tmp_path):
    pipeline, store = _pipeline(tmp_path, total_words=4000, chapter_words=1000)  # 4 章

    # 只生成调研+大纲+前 2 章
    pipeline.run_research(silent=True)
    pipeline.run_outline(silent=True)
    pipeline.run_world(silent=True)
    pipeline.write_chapter(1, silent=True)
    pipeline.write_chapter(2, silent=True)
    assert pipeline.progress() == (2, 4)

    # 断点续跑应补齐剩余 2 章（产物驱动，不重复生成已有章节）
    pipeline.resume(silent=True)
    assert pipeline.progress() == (4, 4)


def test_resume_idempotent_when_done(tmp_path):
    pipeline, store = _pipeline(tmp_path, total_words=2000, chapter_words=1000)  # 2 章
    pipeline.run_all(silent=True)
    assert pipeline.progress() == (2, 2)

    # 已完成再 resume 不报错、不重写
    chapters_before = {c.no: c.content for c in store.list_chapters()}
    pipeline.resume(silent=True)
    chapters_after = {c.no: c.content for c in store.list_chapters()}
    assert chapters_before == chapters_after


def test_chapter_content_and_summary_extracted(tmp_path):
    pipeline, store = _pipeline(tmp_path, total_words=1000, chapter_words=1000)  # 1 章
    pipeline.run_research(silent=True)
    outline = pipeline.run_outline(silent=True)
    pipeline.run_world(silent=True)
    ch = pipeline.write_chapter(1, silent=True)
    assert ch.content
    assert "【摘要】" not in ch.content  # 标记已被剥离
    assert ch.summary
    summaries = store.load_summaries()
    assert len(summaries) == 1 and summaries[0]["no"] == 1
