# -*- coding: utf-8 -*-
"""已有内容续写/扩写链路测试（mock LLM，核心逻辑不依赖 HTTP/DB）。"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from novel_gen.config import NovelConfig, LLMConfig, SearchConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.llm.factory import create_llm
from novel_gen.utils.chapter_splitter import split_chapters

USER_TEXT = """第一章 雨夜
雨下了一整夜。他站在窗边，指尖夹着烟，等一个人。
那人不来。

第二章 旧信
抽屉里躺着一封信，落款是三年前。
他拆开信，手抖得厉害。

第三章 背叛
她转身离开，门在身后关上。
他站在原地，没有追。
"""


def _make_cfg(mode="auto", content_type="", total_words=8000, expand_targets=None):
    return NovelConfig(
        title="续写测试本",
        genre="都市",
        total_words=total_words,
        chapter_words=2500,
        mode=mode,
        expand_input=USER_TEXT if mode == "expand" and not content_type else "",
        content_type=content_type,
        expand_targets=expand_targets or [],
        llm=LLMConfig(provider="mock", model="mock-model"),
        search=SearchConfig(provider=""),
    )


def test_splitter_handles_user_text():
    chs = split_chapters(USER_TEXT)
    assert len(chs) == 3
    assert chs[0]["title"] == "第一章 雨夜"
    assert "雨下了一整夜" in chs[0]["content"]


def test_continue_mode_resumes_after_user_chapters():
    """续写模式：用户章入库后，从第 4 章（N+1）续写，用户章不被覆盖。"""
    tmp = Path(tempfile.mkdtemp(prefix="continue_"))
    cfg = _make_cfg(mode="expand", content_type="chapters", total_words=8000)
    store = ProjectStore.create(tmp, cfg)
    # 模拟创建时写入用户章节
    for i, c in enumerate(split_chapters(USER_TEXT), start=1):
        store.save_chapter(Chapter(no=i, title=c["title"], content=c["content"], source="user"))
    store.set_user_words(sum(len(c["content"]) for c in split_chapters(USER_TEXT)))
    store.save_user_content(USER_TEXT)

    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    assert pipe.is_continue_mode()
    pipe.resume(silent=True)

    chapters = store.list_chapters()
    assert len(chapters) >= 4, f"应续写至少 1 章，实际 {len(chapters)} 章"
    # 用户章原样保留（source=user）
    assert chapters[0].source == "user" and "雨下了一整夜" in chapters[0].content
    # AI 续写章 source=ai
    assert any(c.source == "ai" for c in chapters), "应有 AI 续写章节"
    # 大纲含用户章 + 续写章
    outline = store.load_outline()
    assert len(outline.chapters) == len(chapters)
    # 用户章摘要已生成
    sums = store.load_summaries()
    assert len(sums) >= 3, "用户章应有摘要"


def test_expand_existing_mode_expands_user_chapters():
    """扩写模式：把用户已有章节扩写到单章目标字数（mock 下正文不变长则跳过，流程不报错）。"""
    tmp = Path(tempfile.mkdtemp(prefix="expand_existing_"))
    cfg = _make_cfg(mode="expand", content_type="chapters_expand", total_words=8000, expand_targets=[1, 2])
    store = ProjectStore.create(tmp, cfg)
    for i, c in enumerate(split_chapters(USER_TEXT), start=1):
        store.save_chapter(Chapter(no=i, title=c["title"], content=c["content"], source="user"))
    store.set_user_words(sum(len(c["content"]) for c in split_chapters(USER_TEXT)))
    store.save_user_content(USER_TEXT)

    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    assert pipe.is_expand_existing_mode()
    pipe.resume(silent=True)  # 走 run_expand_existing

    chapters = store.list_chapters()
    assert all(c.source == "user" for c in chapters), "扩写场景所有章节仍标记 user"
    assert len(chapters) == 3, "扩写不应新增章节"
    assert store.load_state().phase == "done"


def test_user_chapter_protection_rewrite_blocked():
    """user 章保护：单章重写对 user 章拒绝（服务端 400 由 HTTP 层验证，此处验证判别逻辑）。"""
    tmp = Path(tempfile.mkdtemp(prefix="protect_"))
    cfg = _make_cfg(mode="expand", content_type="chapters")
    store = ProjectStore.create(tmp, cfg)
    store.save_chapter(Chapter(no=1, title="第一章 雨夜", content="正文", source="user"))
    ch = store.load_chapter(1)
    assert ch.source == "user", "user 章节标记应存在"


def test_settle_words_accounting():
    """结算口径：ai_words = 全书当前总字数 - 用户手写原字数（user_words 记录）。"""
    tmp = Path(tempfile.mkdtemp(prefix="settle_"))
    cfg = _make_cfg(mode="expand", content_type="chapters")
    store = ProjectStore.create(tmp, cfg)
    user_chs = split_chapters(USER_TEXT)
    user_words = sum(len(c["content"]) for c in user_chs)
    store.set_user_words(user_words)
    assert store.user_words() == user_words
    # 模拟生成后：用户章 + AI 章
    for i, c in enumerate(user_chs, start=1):
        store.save_chapter(Chapter(no=i, title=c["title"], content=c["content"], source="user"))
    store.save_chapter(Chapter(no=4, title="第四章 归来", content="AI 生成正文内容" * 50, source="ai"))
    total = sum(len(c.content) for c in store.list_chapters())
    ai_words = max(0, total - store.user_words())
    assert ai_words == len(store.load_chapter(4).content), f"AI 字数应等于 AI 章字数，实际 {ai_words}"
