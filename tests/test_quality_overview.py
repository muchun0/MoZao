# -*- coding: utf-8 -*-
"""R12 章节质量总览测试（只读聚合，不触 LLM / MySQL）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import quality_overview as overview
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter

pytestmark = pytest.mark.p1


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="质量书", total_words=10000, chapter_words=2500, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _quality(score=60, passed=False, issues=None, issue_count=None):
    issues = issues or []
    return {"passed": passed, "score": score,
            "issue_count": len(issues) if issue_count is None else issue_count,
            "issues": issues}


def _save(store, no, text, quality=None):
    store.save_chapter(Chapter(no=no, title=f"第{no}章", content=text),
                       source="ai", quality=quality)


def _issue(level, type_, detail="示例问题描述"):
    return {"level": level, "type": type_, "detail": detail}


def test_mixed_chapters_overview(store):
    """有质检 / 无质检混合：分数、问题分桶、最差章、未质检提示都正确。"""
    _save(store, 1, "第一章内容。", _quality(score=60, issues=[
        _issue("大改", "读者-人物一致性"),
        _issue("大改", "自相矛盾-事件重复"),
        _issue("中改", "读者-代入感"),
        _issue("小改", "写作-字数超出目标"),
    ]))
    _save(store, 2, "第二章内容。", _quality(score=95, passed=True, issues=[]))
    _save(store, 3, "第三章内容。")  # 无 quality 记录

    data = overview.build_quality_overview(store)
    rows = {r["no"]: r for r in data["chapters"]}
    assert data["chapter_count"] == 3
    assert data["scored_count"] == 2 and data["unscored_count"] == 1

    bad = rows[1]
    assert bad["score"] == 60 and bad["has_quality"] is True
    assert bad["passed"] is False and bad["needs_attention"] is True
    assert bad["counts"] == {"high": 2, "medium": 1, "low": 1}
    assert bad["severity"] == "high" and bad["severity_label"] == "大改"
    assert bad["rank"] == 1

    good = rows[2]
    assert good["severity"] == "info" and good["severity_label"] == "无问题"
    assert good["needs_attention"] is False and good["rank"] == 2

    unscored = rows[3]
    # 未质检不伪造 0 分，也不参与排序
    assert unscored["has_quality"] is False and unscored["score"] is None
    assert unscored["severity"] == "none" and unscored["severity_label"] == "未质检"
    assert unscored["rank"] is None

    assert data["worst"]["no"] == 1
    assert any("1 章没有质检记录" in g for g in data["data_gaps"])


def test_ranking_prioritizes_major_issues_then_score(store):
    """大改数优先，同等大改数下分数低者更靠前。"""
    _save(store, 1, "甲。", _quality(score=100, issues=[_issue("大改", "A")] * 3))
    _save(store, 2, "乙。", _quality(score=20, issues=[_issue("大改", "A")] * 3))
    _save(store, 3, "丙。", _quality(score=50, issues=[_issue("大改", "A")] * 1))
    data = overview.build_quality_overview(store)
    ranks = {r["no"]: r["rank"] for r in data["chapters"]}
    assert ranks == {2: 1, 1: 2, 3: 3}
    assert data["worst"]["no"] == 2


def test_top_issues_aggregated_by_type(store):
    """问题类型聚合：同类计数取最高级别，最多 3 类。"""
    _save(store, 1, "甲。", _quality(issues=[
        _issue("小改", "写作-字数超出目标"),
        _issue("大改", "读者-人物一致性"),
        _issue("中改", "读者-人物一致性"),
        _issue("中改", "读者-代入感"),
        _issue("小改", "读者-阅读障碍"),
    ]))
    data = overview.build_quality_overview(store)
    row = data["chapters"][0]
    assert [t["type"] for t in row["top_issues"]][:3] == \
        ["读者-人物一致性", "读者-代入感", "写作-字数超出目标"]
    assert row["top_issues"][0]["count"] == 2
    assert row["top_issues"][0]["level"] == "大改"


def test_batch_reads_index_once_and_never_loads_chapters(store, monkeypatch):
    """性能约束：只读索引一次，不逐章加载正文/版本文件。"""
    _save(store, 1, "甲。", _quality(issues=[_issue("大改", "A")]))
    _save(store, 2, "乙。")
    calls = {"index": 0}
    original = store.load_chapter_versions

    def _counting_index():
        calls["index"] += 1
        return original()

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("质量总览不应逐章加载正文")

    monkeypatch.setattr(store, "load_chapter_versions", _counting_index)
    monkeypatch.setattr(store, "load_chapter", _forbidden)
    data = overview.build_quality_overview(store)
    assert calls["index"] == 1
    assert data["chapter_count"] == 2


def test_all_unscored_book_has_no_worst(store):
    """整本都没有质检记录：不比较、不显示 0 分最差章。"""
    _save(store, 1, "甲。")
    _save(store, 2, "乙。")
    data = overview.build_quality_overview(store)
    assert data["worst"] is None
    assert data["scored_count"] == 0 and data["unscored_count"] == 2
    assert all(r["score"] is None for r in data["chapters"])
    assert any("暂无章节质检记录" in g for g in data["data_gaps"])


def test_index_read_failure_degrades_without_raising(store, monkeypatch):
    """索引读取异常：降级为空质量结果 + data_gaps，不抛异常（对应接口不返回 5xx）。"""
    _save(store, 1, "甲。", _quality(issues=[_issue("大改", "A")]))

    def _boom():
        raise OSError("模拟索引损坏")

    monkeypatch.setattr(store, "load_chapter_versions", _boom)
    data = overview.build_quality_overview(store)
    assert data["chapter_count"] == 1
    assert data["scored_count"] == 0 and data["worst"] is None
    assert any("读取章节版本索引失败" in g for g in data["data_gaps"])


def test_empty_project_returns_gap(store):
    """没有章节正文：返回空结构 + 明确说明。"""
    data = overview.build_quality_overview(store)
    assert data["chapter_count"] == 0 and data["chapters"] == []
    assert any("还没有章节正文" in g for g in data["data_gaps"])
