# -*- coding: utf-8 -*-
"""P2-4 质量指标报表聚合测试。

覆盖：空数据不报错、得分分布/重写率/问题 TopN 聚合正确、extra 字符串与 dict 双形态兼容。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.db.traces import _aggregate_quality


@pytest.mark.p2
def test_empty_rows():
    """无质检数据：空态不报错，字段齐全。"""
    r = _aggregate_quality([])
    assert r["quality_checks"] == 0
    assert r["score_avg"] is None
    assert r["rewrite_rate"] == 0
    assert r["score_buckets"] == {"0-59": 0, "60-79": 0, "80-100": 0}
    assert r["issue_top_types"] == []


@pytest.mark.p2
def test_score_buckets_and_rewrite():
    """得分分布桶、平均分、重写率、问题类型 TopN 聚合正确。"""
    rows = [
        {"extra": {"score": 90, "auto_revised": False,
                   "issues": [{"type": "AI痕迹-连接词堆砌"}, {"type": "AI痕迹-情绪词直白"}]}},
        {"extra": {"score": 55, "auto_revised": True,
                   "issues": [{"type": "结构-字数严重不足"}]}},
        {"extra": '{"score": 70, "auto_revised": false, "issues": [{"type": "读者-追更欲过低"}]}'},
    ]
    r = _aggregate_quality(rows)
    assert r["quality_checks"] == 3
    assert r["score_avg"] == round((90 + 55 + 70) / 3, 1)
    assert r["score_buckets"] == {"0-59": 1, "60-79": 1, "80-100": 1}
    assert r["rewrite_count"] == 1
    assert round(r["rewrite_rate"], 3) == round(1 / 3, 3)
    top = dict(r["issue_top_types"])
    assert top.get("AI痕迹") == 2
    assert top.get("结构") == 1
    assert top.get("读者") == 1
    # TopN 按数量降序
    assert r["issue_top_types"][0] == ("AI痕迹", 2)


@pytest.mark.p2
def test_weird_extra_tolerated():
    """extra 缺失/非 JSON/非 dict：跳过不报错。"""
    rows = [{"extra": None}, {"extra": "not json"}, {"extra": {}}, {"extra": []}]
    r = _aggregate_quality(rows)
    assert r["quality_checks"] == 4
    assert r["score_avg"] is None
    assert r["rewrite_count"] == 0


@pytest.mark.p2
def test_score_clamped_types():
    """score 非数值（None/字符串）不计入均分，但 check 计数保留。"""
    rows = [
        {"extra": {"score": None, "auto_revised": False, "issues": []}},
        {"extra": {"score": "90", "auto_revised": False, "issues": []}},
    ]
    r = _aggregate_quality(rows)
    assert r["quality_checks"] == 2
    assert r["score_avg"] is None  # 无有效数值分


@pytest.mark.p2
def test_issues_not_dict_skipped():
    """issues 条目非 dict 时跳过（防御脏数据）。"""
    rows = [{"extra": {"score": 80, "auto_revised": False, "issues": ["oops", {"type": "合规-疑似违规内容"}]}}]
    r = _aggregate_quality(rows)
    assert dict(r["issue_top_types"]).get("合规") == 1
