# -*- coding: utf-8 -*-
"""大纲编辑 / 章节重写核心逻辑测试（服务端逻辑，不依赖 HTTP/DB）。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import HTTPException

from novel_gen.config import NovelConfig, LLMConfig, SearchConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.llm.factory import create_llm

from webapp.server import REGEN_DAILY_LIMIT, _check_regen_limit, _outline_diff


def _make_store():
    tmp = Path(tempfile.mkdtemp(prefix="outline_rewrite_"))
    cfg = NovelConfig(
        title="大纲重写测试",
        genre="都市",
        total_words=5000,
        chapter_words=2500,
        mode="expand",
        expand_input="主角陈默被逐出家门，靠写代码维生。大纲：\n1. 陈默被逐出家门，在出租屋接外包糊口。\n2. 他接到神秘项目委托，犹豫后接下。",
        llm=LLMConfig(provider="mock", model="mock-model"),
        search=SearchConfig(provider=""),
    )
    store = ProjectStore.create(tmp, cfg)
    return cfg, store


def test_outline_diff_detects_changed_and_removed():
    """diff：标题/目标变了的已生成章节 → changed；大纲已删除的已生成章节 → removed。"""
    _, store = _make_store()
    outline = Outline(title="t", intro="i", chapters=[
        ChapterPlan(no=1, title="第一章 逐出家门", goal="被逐出家门"),
        ChapterPlan(no=2, title="第二章 神秘委托", goal="接下委托"),
    ])
    store.save_outline(outline)
    # 第1章与大纲一致（带 goal 快照）；第2章标题已改（旧标题）；第3章已不在大纲（废弃）
    store.save_chapter(Chapter(no=1, title="第一章 逐出家门", content="内容1", summary="摘要1", goal="被逐出家门"))
    store.save_chapter(Chapter(no=2, title="第二章 旧标题", content="内容2", summary="摘要2", goal="接下委托"))
    store.save_chapter(Chapter(no=3, title="第三章 已删除", content="内容3", summary="摘要3", goal="旧目标"))
    changed, removed = _outline_diff(outline, store)
    assert changed == [2], f"changed 应为 [2]，实际 {changed}"
    assert removed == [3], f"removed 应为 [3]，实际 {removed}"


def test_outline_diff_ignores_legacy_chapter_without_goal():
    """旧数据章节无 goal 快照：标题一致时不误报 changed。"""
    _, store = _make_store()
    outline = Outline(title="t", intro="i", chapters=[
        ChapterPlan(no=1, title="第一章", goal="新目标"),
    ])
    store.save_outline(outline)
    store.save_chapter(Chapter(no=1, title="第一章", content="c", summary="s"))  # goal 为空（旧数据）
    changed, removed = _outline_diff(outline, store)
    assert changed == [], f"旧数据不应误报，实际 {changed}"


def test_regen_daily_limit():
    """重生成每日限次：第 11 次抛 429。"""
    _, store = _make_store()
    for _ in range(REGEN_DAILY_LIMIT):
        _check_regen_limit(store, "p1")
    with pytest.raises(HTTPException) as ei:
        _check_regen_limit(store, "p1")
    assert ei.value.status_code == 429
    # 计数落盘
    assert store.dir.joinpath("regen_count.json").exists()


def test_rewrite_keeps_later_chapters_and_updates_summary():
    """单章重写核心链路（mock LLM）：重写第1章后——
    第2章文件不动、第1章摘要被覆盖、goal 快照写入。"""
    _, store = _make_store()
    outline = Outline(title="t", intro="i", chapters=[
        ChapterPlan(no=1, title="第一章", goal="目标一"),
        ChapterPlan(no=2, title="第二章", goal="目标二"),
    ])
    store.save_outline(outline)
    ch2_content = "第二章正文内容"
    store.save_chapter(Chapter(no=1, title="第一章", content="旧正文", summary="旧摘要", goal="目标一"))
    store.save_chapter(Chapter(no=2, title="第二章", content=ch2_content, summary="摘要二", goal="目标二"))
    store.append_summary(1, "旧摘要")
    store.append_summary(2, "摘要二")

    cfg = store.load_config()
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    ch_new = pipe.chapter_writer.write(1, "第一章", "目标一", None, is_final=False)

    assert ch_new.content, "重写后正文不应为空"
    assert ch_new.goal == "目标一", "goal 快照应写入章节"
    # 后续章节不受影响
    assert store.load_chapter(2).content == ch2_content, "第2章正文不应被动"
    # 摘要幂等覆盖（memory 中第1章摘要 = 新摘要，共 2 条）
    sums = store.load_summaries()
    assert len(sums) == 2, f"摘要数量应为 2，实际 {len(sums)}"
    assert sums[0]["no"] == 1 and sums[0]["summary"] == ch_new.summary
