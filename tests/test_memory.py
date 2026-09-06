# -*- coding: utf-8 -*-
"""记忆系统测试：设定库 + 最近 N 章摘要回填。"""
from novel_gen.config import NovelConfig
from novel_gen.pipeline.memory import Memory
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Character, World


def _store(tmp_path, memory_window=3):
    cfg = NovelConfig(title="t", genre="玄幻", total_words=3000, chapter_words=1000)
    store = ProjectStore.create(tmp_path, cfg)
    return store, Memory(store, memory_window)


def test_context_includes_world_and_recent_summaries(tmp_path):
    store, mem = _store(tmp_path, memory_window=3)
    world = World(
        world_view="九域大陆",
        characters=[Character(name="林夜", role="主角", desc="坚韧")],
        foreshadowing=["剑魂秘密"],
    )
    for no in range(1, 6):
        store.append_summary(no, f"第{no}章发生了剧情")

    ctx = mem.build_context(world, up_to_chapter=6)
    assert "九域大陆" in ctx
    assert "林夜" in ctx
    assert "剑魂秘密" in ctx
    # 只回填最近 3 章（第3/4/5章），第1/2章不出现
    assert "第1章发生了剧情" not in ctx
    assert "第2章发生了剧情" not in ctx
    assert "第3章发生了剧情" in ctx
    assert "第5章发生了剧情" in ctx


def test_context_excludes_current_and_future(tmp_path):
    store, mem = _store(tmp_path, memory_window=10)
    store.append_summary(2, "第二章节")
    store.append_summary(3, "第三章节")
    ctx = mem.build_context(world=None, up_to_chapter=3)
    # 当前章(3)与未来不进入记忆
    assert "第三章节" not in ctx
    assert "第二章节" in ctx


def test_context_empty_when_no_data(tmp_path):
    store, mem = _store(tmp_path)
    assert mem.build_context(world=None, up_to_chapter=1) == ""
