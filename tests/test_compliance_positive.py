# -*- coding: utf-8 -*-
"""REQ-门禁质检正向影响 P0-1/P0-2 合规红线专项测试（P1）。

覆盖：
- compliance.has_compliance / compliance_categories（含旧格式兜底）/ compliance_education_block
- safety.basic_content_check_structured 分类与 basic_content_check 同源
- QualityChecker.check 合规 issue 携带 rule_category/rule_phrase
- ensure_quality 合规命中 → compliance_rejected=True（删旧稿整章重写、不落盘信号）
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
from novel_gen.pipeline.compliance import (
    has_compliance, compliance_categories, compliance_education_block,
)
from novel_gen.safety import basic_content_check_structured, basic_content_check


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "cq", cfg)


# ---------- compliance 纯函数 ----------

def test_has_compliance_marker():
    assert has_compliance([{"type": "合规-疑似违规内容", "detail": "x"}]) is True
    assert has_compliance([{"type": "合规-疑似违规内容-未成年人性化", "detail": "x"}]) is True
    assert has_compliance([{"type": "内容重复", "detail": "x"}]) is False
    assert has_compliance([]) is False
    assert has_compliance(None) is False


def test_compliance_categories_with_rule_category():
    issues = [
        {"type": "合规-疑似违规内容", "rule_category": "未成年人性化", "detail": "幼女做爱"},
        {"type": "合规-疑似违规内容", "rule_category": "未成年人性化", "detail": "儿童性行为"},
        {"type": "内容重复", "detail": "x"},
    ]
    assert compliance_categories(issues) == ["未成年人性化"]  # 去重


def test_compliance_categories_old_format_fallback():
    # 旧 issue 无 rule_category，从 detail 短语反查大类
    issues = [{"type": "合规-疑似违规内容", "detail": "本章包含对幼女做爱的详细描写"}]
    assert compliance_categories(issues) == ["未成年人性化"]


def test_compliance_categories_empty():
    assert compliance_categories([]) == []
    assert compliance_categories([{"type": "内容重复"}]) == []


def test_compliance_education_block():
    blk = compliance_education_block(["未成年人性化"])
    assert "合规教育" in blk
    assert "未成年人性化" in blk
    assert "整稿废弃重写" in blk
    multi = compliance_education_block(["露骨色情描写", "危险行为教唆"])
    assert "露骨色情描写" in multi and "危险行为教唆" in multi
    assert compliance_education_block([]) == ""


# ---------- safety 结构化合规（与 basic_content_check 同源） ----------

def test_basic_content_check_structured_category():
    bad = "正常内容" * 20 + "本章包含对幼女做爱的详细描写。"
    hits = basic_content_check_structured(bad)
    assert hits, "应命中至少一条合规短语"
    assert any(h["rule_category"] == "未成年人性化" for h in hits)
    assert all("phrase" in h and "rule_desc" in h for h in hits)
    # 同源：basic_content_check 也必须命中同一短语
    assert basic_content_check(bad)
    assert basic_content_check_structured("正常内容" * 20) == []


def test_basic_content_check_structured_multi_category():
    bad = "毒贩在厨房研究自制炸药的配方，随后对幼女做爱。"
    cats = {h["rule_category"] for h in basic_content_check_structured(bad)}
    assert "危险行为教唆" in cats
    assert "未成年人性化" in cats


# ---------- QualityChecker.check 合规 issue 带分类 ----------

def test_check_compliance_issue_carries_category(store):
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
    qc = QualityChecker(store.load_config(), llm, store)
    bad = "正常内容" * 200 + "本章包含对幼女做爱的详细描写。"
    result = qc.check(2, "第二章", "推进", bad)
    comp = [i for i in result["issues"] if "合规" in str(i.get("type", ""))]
    assert comp, "合规问题应存在"
    # 确定性合规段（content_check）应携带 rule_category / rule_phrase
    assert any(i.get("rule_category") == "未成年人性化" for i in comp)


# ---------- ensure_quality 合规命中 → compliance_rejected ----------

def _llm_compliance_always_bad(reader_bad, rewrite_bad):
    """reader 恒返回合规 issue；rewrite 恒返回违规正文 → 循环重写后仍不合规。"""
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return reader_bad
        return rewrite_bad

    llm.chat.side_effect = _chat
    return llm, calls


def test_ensure_quality_compliance_rejected(store):
    """合规命中 → 删旧稿整章重写仍违规 → compliance_rejected=True（落盘红线信号）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    reader_bad = ('{"issues": [{"type": "合规-疑似违规内容", '
                  '"rule_category": "未成年人性化", "detail": "含对幼女做爱的描写"}], '
                  '"追更欲": 1, "一句话感受": "违规"}')
    llm, _ = _llm_compliance_always_bad(reader_bad, "正常内容" * 200 + "幼女做爱。")
    cfg = store.load_config()
    cfg.llm_semantic_beats = False  # 词表换LLM 批次一：P0-1 合规测试依赖固定 mock 响应序列，关新开关保持原调用布局
    qc = QualityChecker(cfg, llm, store)
    chapter, result = qc.ensure_quality(2, "第二章", "推进", "第一章标题",
                                        "正常内容" * 200, None, None)
    assert result["passed"] is False
    assert result["compliance_rejected"] is True  # P0-1 红线：不落盘信号
    assert any("合规" in str(i.get("type", "")) for i in result["issues"])


def test_ensure_quality_normal_issue_not_compliance_rejected(store):
    """普通质量问题（非合规）→ compliance_rejected=False。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    reader = '{"issues": [{"type": "结构-字数严重不足", "detail": "太短"}], "追更欲": 2, "一句话感受": "短"}'
    llm, _ = _llm_compliance_always_bad(reader, "正常内容" * 200 + "补充了更多内容。")
    qc = QualityChecker(store.load_config(), llm, store)
    _, result = qc.ensure_quality(2, "第二章", "推进", "第一章标题",
                                  "正常内容" * 200, None, None)
    assert result["compliance_rejected"] is False
