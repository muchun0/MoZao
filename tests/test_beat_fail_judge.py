# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S3.4 fail 章 LLM 二级复核 专项测试。

覆盖：
- P1：beat_fail_judge 默认关 → 不触发 LLM 复核
- P1：开启且 judge 确认未兑现 → 写入 verify_issues（L3 触发信号）
- P1：judge 认为已兑现（误报）→ 不记录
- P2：开启但无 missing/正文过短 → 跳过
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
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def writer(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                      chapter_words=1600, beat_fail_judge=True)
    store = ProjectStore.create(tmp_path / "sc", cfg)
    store.save_chapter(Chapter(no=3, content=("主角启程踏入废墟，残卷在怀中发热，他握紧剑柄。"
                                              "阴影里传来低语，他循声追去，撞见一道熟悉的身影。" * 8),
                               summary="ch3"))
    llm = MagicMock()
    w = ChapterWriter(cfg, llm, store, MagicMock())
    return w, store, llm


class TestBeatFailJudge:
    @pytest.mark.p1
    def test_default_off(self, writer):
        w, store, llm = writer
        cfg = w.cfg
        cfg.beat_fail_judge = False
        llm.chat.side_effect = AssertionError("默认关不应调用 LLM")
        w._beat_fail_judge(3, store.load_chapter(3).content,
                           "主角启程迎战强敌；解开残卷之谜", ["主角启程迎战强敌", "解开残卷之谜"])

    @pytest.mark.p1
    def test_judge_confirms_unfulfilled(self, writer):
        w, store, llm = writer
        llm.chat.return_value = '{"unfulfilled":["解开残卷之谜"],"reason":"正文未提残卷内容"}'
        w._beat_fail_judge(3, store.load_chapter(3).content,
                           "主角启程迎战强敌；解开残卷之谜", ["解开残卷之谜"])
        issues = [i for i in store.load_verify_issues() if i.get("chapter") == 3]
        assert any("BeatGate-fail 复核" in str(i.get("type", "")) for i in issues)

    @pytest.mark.p1
    def test_judge_considers_fulfilled_no_record(self, writer):
        w, store, llm = writer
        llm.chat.return_value = '{"unfulfilled":[],"reason":"拍已发生，属误报"}'
        w._beat_fail_judge(3, store.load_chapter(3).content,
                           "主角启程迎战强敌；解开残卷之谜", ["解开残卷之谜"])
        issues = [i for i in store.load_verify_issues() if i.get("chapter") == 3]
        assert not any("BeatGate-fail 复核" in str(i.get("type", "")) for i in issues)

    @pytest.mark.p2
    def test_no_missing_skips(self, writer):
        w, store, llm = writer
        llm.chat.side_effect = AssertionError("无 missing 不应调用 LLM")
        w._beat_fail_judge(3, store.load_chapter(3).content,
                           "主角启程迎战强敌", [])
