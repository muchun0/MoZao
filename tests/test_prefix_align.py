# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S4.1 前缀缓存对齐测试。

覆盖：
1. chapter_prompt 在 prefix_cache_aligned=True（默认）时，稳定块（单章字数硬指标+硬约束）
   前置到 user 最前 → 同书跨章共享最大稳定前缀（命中 DeepSeek 前缀缓存价）
2. aligned=False 时保持原组装顺序（稳定块在中部，向后兼容）
3. Usage 新增 prompt_cache_hit_tokens 累计/最近值（cache_hit 埋点数据底座）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.llm.usage import Usage
from novel_gen.pipeline.templates import chapter_prompt

STABLE_MARK = "【单章字数·硬指标】"


def _mk_cfg(aligned: bool) -> NovelConfig:
    cfg = NovelConfig()
    cfg.prefix_cache_aligned = aligned
    return cfg


def _chapter(cfg, chapter_no: int):
    return chapter_prompt(
        cfg,
        chapter_no=chapter_no,
        chapter_title=f"第{chapter_no}回 试炼",
        chapter_goal=f"推进主线到节点{chapter_no}",
        research_block="",
        memory_context=f"第{chapter_no-1}章记忆",
        prev_tail="前情结尾",
    )


class TestPrefixCacheAligned:
    @pytest.mark.p1
    def test_aligned_stable_block_leads_user(self):
        """aligned=True 时，稳定块（单章字数硬指标）在 user 最前。"""
        cfg = _mk_cfg(True)
        _sys, user = _chapter(cfg, 1)
        assert user.lstrip().startswith(STABLE_MARK)

    @pytest.mark.p1
    def test_aligned_cross_chapter_shared_prefix(self):
        """aligned=True 时，第1章与第2章的 user 共享同一稳定前缀（可命中前缀缓存）。"""
        cfg = _mk_cfg(True)
        _, u1 = _chapter(cfg, 1)
        _, u2 = _chapter(cfg, 2)
        # 稳定块逐字节一致，[稳定块+字数指标正文] 是公共前缀；变化块（【本章】第N章）在后
        shared = STABLE_MARK + "本章正文（不含【摘要】行）必须达到"
        assert u1.startswith(shared) and u2.startswith(shared)
        assert u1[: len(shared)] == u2[: len(shared)]

    @pytest.mark.p1
    def test_aligned_chapter_marker_after_stable(self):
        """aligned=True 时，【本章】章号变化块位于稳定块之后（不在 user 开头）。"""
        cfg = _mk_cfg(True)
        _, user = _chapter(cfg, 1)
        assert "【本章】第1章" in user
        assert user.index("【本章】第1章") > user.index(STABLE_MARK)

    @pytest.mark.p1
    def test_unaligned_keeps_original_order(self):
        """aligned=False 时保持原组装：user 以【本章】开头，稳定块在中部。"""
        cfg = _mk_cfg(False)
        _, user = _chapter(cfg, 1)
        assert user.startswith("【本章】第1章")
        assert STABLE_MARK in user
        assert user.index(STABLE_MARK) > user.index("【本章】")


class TestUsageCacheHit:
    @pytest.mark.p1
    def test_note_accumulates_cache_hit(self):
        u = Usage()
        u.note(prompt_tokens=100, completion_tokens=50, prompt_cache_hit_tokens=40)
        u.note(prompt_tokens=200, completion_tokens=60, prompt_cache_hit_tokens=90)
        assert u.prompt_cache_hit_tokens == 130
        assert u.last_prompt_cache_hit_tokens == 90
        assert u.total_tokens == 410

    @pytest.mark.p1
    def test_note_default_cache_hit_zero(self):
        u = Usage()
        u.note(prompt_tokens=100, completion_tokens=50)
        assert u.prompt_cache_hit_tokens == 0
        assert u.last_prompt_cache_hit_tokens == 0
