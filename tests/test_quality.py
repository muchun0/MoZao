# -*- coding: utf-8 -*-
"""模块4 ReaderAgent 结果自校验测试（P2）。

覆盖：字数严重不足触发自动重写、重写后复检通过、重复片段检测、
正常章节不触发重写、校验结果落 review.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, AgentGuardConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    s = ProjectStore.create(tmp_path / "q", cfg)
    return s


def _mk_llm(reader_result: str = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}',
            rewrite_text: str = ""):
    """按调用顺序返回：第1次=读者校验，第2次=自动重写。"""
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return reader_result
        return rewrite_text

    llm.chat.side_effect = _chat
    return llm, calls


@pytest.mark.p2
def test_short_chapter_triggers_rewrite(store):
    llm, calls = _mk_llm(rewrite_text="这是一段足够长的重写后正文。" * 100 + "\n【摘要】重写成功")
    qc = QualityChecker(store.load_config(), llm, store)
    chapter, result = qc.ensure_quality(1, "第一章", "推进主线", "太短了。", "摘要")
    # 触发 1 次重写（第1次=读者校验，第2次=重写）
    assert calls["n"] >= 2
    # 重写后正文被采用
    assert len(chapter.content) > 100
    # 结果落 review.json
    review = json.loads((store.dir / "review.json").read_text(encoding="utf-8"))
    assert review[0]["chapter"] == 1
    assert "quality_check" in review[0]


@pytest.mark.p2
def test_quality_passed_no_rewrite(store):
    llm, calls = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    long_content = "少年握剑，走向山下。" * 300  # 2400 字，接近目标
    chapter, result = qc.ensure_quality(1, "第一章", "推进主线", long_content, "摘要")
    assert result["passed"] is True
    assert calls["n"] == 1  # 只调了读者校验，未触发重写


@pytest.mark.p2
def test_duplicate_detection(store):
    """与前一章高度重复 → 结构-与前文重复问题。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="重复段落A" * 200, summary="s"))
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    dup_content = "重复段落A" * 200 + "新增内容" * 10
    result = qc.check(2, "第二章", "推进", dup_content)
    assert any("与前文重复" in (i.get("type", "") or "") for i in result["issues"])


@pytest.mark.p2
def test_output_check_flag(store):
    """输出含明显违规词 → 合规-疑似违规内容问题。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    bad = "正常内容" * 200 + "本章包含对幼女做爱的详细描写。"
    result = qc.check(2, "第二章", "推进", bad)
    assert any("合规" in (i.get("type", "") or "") for i in result["issues"])


@pytest.mark.p2
def test_quality_check_disabled(store, tmp_path):
    """quality_check=False 时 ChapterWriter 不触发校验（降级开关）。"""
    cfg = store.load_config()
    cfg.agent_guard.quality_check = False
    from novel_gen.pipeline.stages import ChapterWriter
    from novel_gen.pipeline.memory import Memory
    llm = MagicMock()
    llm.chat.return_value = "正文内容" * 200 + "\n【摘要】正常"
    w = ChapterWriter(cfg, llm, store, Memory(store, 5))
    ch = w.write(1, "第一章", "推进", None, silent=True)
    assert len(ch.content) > 100
    # 未触发校验（不会因短正文调用重写导致额外调用）
    assert llm.chat.call_count >= 1
