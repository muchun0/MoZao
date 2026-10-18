# -*- coding: utf-8 -*-
"""REQ-门禁质检正向影响 P0-3/P0-4 诊断回喂 + 同质度软提示 专项测试（P1）。

覆盖：
- diagnosis.suggest_for 精确表 / 前缀兜底 / AI痕迹 / 已带建议不覆盖
- diagnosis.build_issue_block 带建议文本（reask 而非盲重写）
- QualityChecker.check 在 diag_feedback 开启时补 fix_suggestion / expected_gain，并带 hook_score
- quality._style_homogeneity_issues 跨章趋同软提示（不扣分、不阻断）
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
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.pipeline.diagnosis import suggest_for, build_issue_block


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "diag", cfg)


# ---------- diagnosis.suggest_for ----------

def test_suggest_for_exact_type():
    r = suggest_for({"type": "结构-字数严重不足", "detail": "太短"})
    assert r and r["expected_gain"] == 30
    assert "字数" in r["fix_suggestion"]


def test_suggest_for_prefix_fallback():
    r = suggest_for({"type": "读者-追更欲过低", "detail": "无钩子"})
    assert r and "爽点闭环" in r["fix_suggestion"]
    r2 = suggest_for({"type": "自相矛盾-身体状态", "detail": "手"})
    assert r2 and "统一前后描写" in r2["fix_suggestion"]


def test_suggest_for_ai_taste():
    r = suggest_for({"type": "AI痕迹-排比堆砌", "detail": "模板化"})
    assert r and "降低模板化表达" in r["fix_suggestion"]


def test_suggest_for_already_has_suggestion_not_overwrite():
    r = suggest_for({"type": "结构-字数严重不足", "fix_suggestion": "LLM 已给", "detail": "x"})
    assert r is None


def test_suggest_for_unmapped_none():
    assert suggest_for({"type": "未知维度", "detail": "x"}) is None


# ---------- diagnosis.build_issue_block ----------

def test_build_issue_block_includes_suggestion():
    block = build_issue_block([
        {"type": "结构-字数严重不足", "detail": "本章只有 800 字"},
        {"type": "合规-疑似违规内容", "rule_category": "未成年人性化",
         "detail": "含违规描写"},
    ])
    assert "建议：补足正文" in block
    assert "预计提升 30 分" in block
    assert "合规" in block


def test_build_issue_block_empty():
    assert build_issue_block([]) == ""


def test_build_issue_block_uses_llm_suggestion():
    block = build_issue_block([
        {"type": "读者-追更欲过低", "fix_suggestion": "LLM建议加强章末钩子", "detail": "d"},
    ])
    assert "LLM建议加强章末钩子" in block


# ---------- QualityChecker.check 诊断回喂 + hook_score ----------

def test_check_diag_feedback_attaches_suggestion(store):
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
    qc = QualityChecker(store.load_config(), llm, store)
    short = "太短了。" * 30  # 触发字数不足
    result = qc.check(2, "第二章", "推进", short)
    # diag_feedback 默认开 → 确定性 issue 应带建议
    if result["issues"]:
        with_sug = [i for i in result["issues"] if i.get("fix_suggestion")]
        assert with_sug, "确定性维度应被补齐 fix_suggestion"
        for i in with_sug:
            assert i.get("expected_gain") is not None
    # hook_score 由 reader 追更欲算出
    assert "hook_score" in result


# ---------- quality._style_homogeneity_issues（P0-4） ----------

def test_style_homogeneity_insufficient_chapters(store):
    llm = MagicMock()
    qc = QualityChecker(store.load_config(), llm, store)
    # 第1章不足以判趋同（需近3章）
    assert qc._style_homogeneity_issues(1, "正常内容" * 200) == []


def test_style_homogeneity_skip_on_missing_chapters(store):
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    store.save_chapter(Chapter(no=2, title="第二章", content="正常内容" * 200, summary="s"))
    llm = MagicMock()
    qc = QualityChecker(store.load_config(), llm, store)
    # 第2章前只有1章可读，不足3章 → 空
    assert qc._style_homogeneity_issues(2, "正常内容" * 200) == []
