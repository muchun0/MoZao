# -*- coding: utf-8 -*-
"""P1-2 StageGate 正向增强旁路专项测试（docs/REQ-门禁质检正向影响.md P1-2）。

覆盖 QualityChecker 通过后、落盘前的 _enhance_pass：
- 无增强空间（无 warn 且 hook>=4）→ 不触发 LLM，返回 None
- 有 warn 问题 → LLM 增强润色 + 复检通过且分数不降 → 采纳增强稿
- 复检未通过 / 分数下降 / 增强稿过短 / LLM 异常 → 保留原稿返回 None（降级不阻塞）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.stages import ChapterWriter


def _cw(llm):
    """轻量构造 ChapterWriter：只设 _enhance_pass 用到的属性，不跑完整 __init__。"""
    cw = object.__new__(ChapterWriter)
    cw.guard = None
    cw.llm = llm
    return cw


def _qcll(enhanced_text="增强后的完整章节正文" * 50):
    llm = MagicMock()
    llm.chat.return_value = enhanced_text
    qc = MagicMock()
    qc.check.return_value = {"passed": True, "score": 90}
    return llm, qc


def test_no_space_returns_none_no_llm():
    llm, qc = _qcll()
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 200, "摘要",
                         {"passed": True, "issues": [], "score": 90, "hook_score": 4},
                         qc)
    assert r is None
    llm.chat.assert_not_called()


def test_warn_triggers_enhance_adopt():
    llm, qc = _qcll()
    cw = _cw(llm)
    content = "正文内容" * 100
    r = cw._enhance_pass(1, "标题", "目标", content, "摘要",
                         {"passed": True,
                          "issues": [{"type": "结构-字数偏少", "detail": "低于60%", "level": "中改"}],
                          "score": 70, "hook_score": 3},
                         qc)
    assert r is not None
    enhanced, summary = r
    assert summary == "摘要"
    assert "增强后的完整章节正文" in enhanced
    qc.check.assert_called_once()


def test_check_fail_keep_original():
    llm, qc = _qcll()
    qc.check.return_value = {"passed": False, "score": 60}
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 100, "摘要",
                         {"passed": True,
                          "issues": [{"type": "结构-字数偏少", "level": "中改"}],
                          "score": 70},
                         qc)
    assert r is None


def test_score_drop_keep_original():
    llm, qc = _qcll()
    qc.check.return_value = {"passed": True, "score": 68}  # 低于原 70
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 100, "摘要",
                         {"passed": True,
                          "issues": [{"type": "结构-字数偏少", "level": "中改"}],
                          "score": 70},
                         qc)
    assert r is None


def test_short_enhance_keep_original():
    llm, qc = _qcll(enhanced_text="太短")
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 100, "摘要",
                         {"passed": True,
                          "issues": [{"type": "结构-字数偏少", "level": "中改"}],
                          "score": 70},
                         qc)
    assert r is None


def test_llm_exception_keep_original():
    llm = MagicMock()
    llm.chat.side_effect = RuntimeError("llm 挂了")
    qc = MagicMock()
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 100, "摘要",
                         {"passed": True,
                          "issues": [{"type": "结构-字数偏少", "level": "中改"}],
                          "score": 70},
                         qc)
    assert r is None


def test_hook_low_triggers_enhance():
    # hook<4 触发（无 warn 也可）
    llm, qc = _qcll()
    cw = _cw(llm)
    r = cw._enhance_pass(1, "标题", "目标", "正文" * 100, "摘要",
                         {"passed": True, "issues": [], "score": 85, "hook_score": 2},
                         qc)
    assert r is not None


def test_enhance_pass_flag_default_false():
    assert NovelConfig(title="x").enhance_pass is False
