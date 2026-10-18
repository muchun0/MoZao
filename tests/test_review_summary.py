# -*- coding: utf-8 -*-
"""P0-3 全书质量汇总测试（P2）。

覆盖：空数据、问题总数/类型分布/质检均分/校验问题统计、无质检分时的均分空值。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.review_summary import build_review_summary


@pytest.mark.p2
def test_summary_empty():
    s = build_review_summary([], [])
    assert s["chapter_count"] == 0
    assert s["issue_total"] == 0
    assert s["issue_by_type"] == {}
    assert s["score_avg"] is None
    assert s["verify_issue_total"] == 0


@pytest.mark.p2
def test_summary_counts():
    items = [
        {"chapter": 1, "auto_revised": True,
         "issues": [{"type": "人设-言行OOC", "level": "大改", "detail": "言行不符"}],
         "quality_check": {"score": 78}},
        {"chapter": 2, "auto_revised": False,
         "issues": [{"type": "人设-言行OOC", "level": "小改", "detail": "语气不符"},
                    {"type": "节奏-拖沓", "level": "小改", "detail": "铺垫过长"}],
         "quality_check": {"score": 62}},
    ]
    verify = [{"chapter": 2, "problem": "前文矛盾", "suggestion": "修正"},
              {"chapter": 2, "problem": "读者可能困惑"}]
    s = build_review_summary(items, verify)
    assert s["chapter_count"] == 2
    assert s["issue_total"] == 3
    assert s["issue_by_type"] == {"人设-言行OOC": 2, "节奏-拖沓": 1}
    assert s["score_avg"] == 70.0
    assert s["verify_issue_total"] == 2
    assert s["verify_by_chapter"] == {2: 2}


@pytest.mark.p2
def test_summary_no_score():
    """部分章节无质检分时仅统计有分章节，全无分则 score_avg 为空。"""
    items = [
        {"chapter": 1, "issues": []},
        {"chapter": 2, "issues": [], "quality_check": {"score": 90}},
    ]
    s = build_review_summary(items, [])
    assert s["score_avg"] == 90.0
    s2 = build_review_summary([{"chapter": 1, "issues": []}], [])
    assert s2["score_avg"] is None


@pytest.mark.p2
def test_chapter_status_colors():
    """进度盘色带四色（P0-5/P1）：未过=红 / 追更>=4=绿 / 自动重写=黄 / 通过=灰。"""
    items = [
        {"chapter": 1, "issues": [], "quality_check": {"score": 60, "passed": False}},          # 未过 → 红
        {"chapter": 2, "issues": [], "quality_check": {"score": 88, "passed": True, "hook_score": 4}},  # 追更4 → 绿
        {"chapter": 3, "auto_revised": True, "issues": [], "quality_check": {"score": 80, "passed": True}},  # 自动重写 → 黄
        {"chapter": 4, "issues": [], "quality_check": {"score": 85, "passed": True}},              # 通过 → 灰
    ]
    s = build_review_summary(items, [])
    cs = s["chapter_status"]
    assert len(cs) == 4
    assert [c["color"] for c in cs] == ["red", "green", "yellow", "gray"]
    assert cs[0]["chapter"] == 1 and cs[0]["passed"] is False
    assert cs[1]["hook_score"] == 4
    assert cs[3]["score"] == 85


@pytest.mark.p2
def test_chapter_status_empty():
    s = build_review_summary([], [])
    assert s["chapter_status"] == []
