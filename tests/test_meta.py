# -*- coding: utf-8 -*-
"""项目列表轻量统计缓存（ProjectStore.load_or_refresh_meta）单元测试。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter


def _store(tmp_path):
    return ProjectStore.create(
        tmp_path / "p", NovelConfig(title="测试书", total_words=10000, genre="玄幻"))


def test_first_run_recompute(tmp_path):
    """首次调用全量重算并落盘。"""
    store = _store(tmp_path)
    meta = store.load_or_refresh_meta()
    assert meta["generated_words"] == 0
    assert meta["chapters_done"] == 0
    assert meta["chapters_total"] == 4  # NovelConfig 默认 4 章（无大纲时取 num_chapters）
    assert (store.dir / "meta.json").exists()


def test_cache_hit_when_unchanged(tmp_path):
    """章节未变化时命中缓存（updated_at 不变，不重算）。"""
    store = _store(tmp_path)
    meta = store.load_or_refresh_meta()
    time.sleep(0.02)
    meta2 = store.load_or_refresh_meta()
    assert meta2["updated_at"] == meta["updated_at"]


def test_new_chapter_invalidates(tmp_path):
    """新增章节使缓存失效，重算后字数/章节数更新。"""
    store = _store(tmp_path)
    store.load_or_refresh_meta()
    time.sleep(0.02)
    store.save_chapter(Chapter(no=1, title="第一章", content="内容" * 100))
    meta = store.load_or_refresh_meta()
    assert meta["chapters_done"] == 1
    assert meta["generated_words"] == 200


def test_edit_chapter_invalidates(tmp_path):
    """编辑章节（覆盖旧文件）使缓存失效。"""
    store = _store(tmp_path)
    store.save_chapter(Chapter(no=1, title="第一章", content="内容" * 100))
    store.load_or_refresh_meta()
    time.sleep(0.02)
    store.save_chapter(Chapter(no=1, title="第一章", content="修改" * 150))
    meta = store.load_or_refresh_meta()
    assert meta["generated_words"] == 300
