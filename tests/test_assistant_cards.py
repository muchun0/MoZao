# -*- coding: utf-8 -*-
"""小墨AI 确定性卡片测试（P1）：口径红线 / 阈值单一来源 / 排序 / 缺口声明。

覆盖 TECH §14 / REQ §5.2.1：
  - review.json 的 score 只表述为「质检扣分（缺陷分）」，不出现"质量分"；
  - 正向卡只给实测值 + 阈值 + 达标情况，不合成任何评分数值；
  - 阈值全部来自 ai_taste 常量（改常量即改卡片，不复制数值）；
  - 未接入的检测项必须明示缺口，不得臆造数据。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline import ai_taste
from webapp.assistant import cards

pytestmark = pytest.mark.p1


def _snap(review=None, chapter_no=1) -> dict:
    return {"chapter_no": chapter_no, "title": "第一章",
            "content": "正文" * 100, "review": review}


def _review() -> dict:
    return {
        "auto_revised": True,
        "quality_check": {"score": 12, "passed": False, "hook_score": 3},
        "issues": [
            {"level": "小改", "type": "用词", "detail": "个别用词重复"},
            {"level": "大改", "type": "结构", "detail": "冲突推进过慢"},
            {"level": "中改", "type": "一致性", "detail": "伏笔回收缺失"},
        ],
    }


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk_keys(item)


def test_quality_card_wording():
    """质检分只叫「缺陷分/扣分」，不得出现质量分口径。"""
    card = cards.build_cards(_StubStore(), _snap(_review()), None)[0]
    assert card["type"] == "quality"
    assert card["defect_score"] == 12
    assert card["passed"] is False
    assert "质检扣分（缺陷分）" in card["notice"]
    assert "score" not in card
    assert "质量分" not in card["notice"]


def test_quality_card_without_record():
    card = cards.build_cards(_StubStore(), _snap(None), None)[0]
    assert card["has_record"] is False
    assert card["defect_score"] is None
    assert "defect_score" in card  # 缺口如实给出 None，而非臆造 0 分


def test_issue_card_sorted_and_numbered():
    cards_list = cards.build_cards(_StubStore(), _snap(_review()), None)
    issue = next(c for c in cards_list if c["type"] == "issues")
    assert [i["id"] for i in issue["items"]] == ["Q-1", "Q-2", "Q-3"]
    assert [i["level"] for i in issue["items"]] == ["大改", "中改", "小改"]


def test_no_issue_card_when_empty():
    review = {"quality_check": {"score": 0, "passed": True}, "issues": []}
    types = [c["type"] for c in cards.build_cards(_StubStore(), _snap(review), None)]
    assert "issues" not in types


def test_version_card_requires_two_versions():
    store = _StubStore(versions=[
        {"v": 1, "selected": False, "words": 2000, "quality": {"score": 20, "issue_count": 3}},
        {"v": 2, "selected": True, "words": 2400, "quality": {"score": 8, "issue_count": 1}},
    ])
    ver = next(c for c in cards.build_cards(store, _snap(_review()), None)
               if c["type"] == "versions")
    assert ver["current"]["v"] == 2
    assert ver["previous"]["v"] == 1
    assert ver["word_delta"] == 400
    assert ver["defect_delta"] == -12

    single = _StubStore(versions=[{"v": 1, "selected": True, "words": 2000, "quality": {}}])
    assert not any(c["type"] == "versions"
                   for c in cards.build_cards(single, _snap(_review()), None))


def test_positive_card_thresholds_from_ai_taste():
    """阈值为 ai_taste 常量的镜像（改常量即改卡片）。"""
    positive = {
        "metrics": {"sentence_cv": 0.30, "ttr": 0.40, "filler_ratio": 0.01,
                    "simile_per_1k": 2.5},
        "zero_direct": True, "goods_items": [], "errors": [],
    }
    card = cards.build_positive_card(positive, 1)
    rows = {r["key"]: r for r in card["metrics"]}

    assert rows["sentence_cv"]["ok"] is False   # 0.30 < 0.45 过低
    assert rows["sentence_cv"]["threshold"] == (
        f"{ai_taste._SENT_CV_TOO_LOW}–{ai_taste._SENT_CV_TOO_HIGH}")
    assert rows["ttr"]["ok"] is True
    assert rows["ttr"]["threshold"] == f"≥{ai_taste._TTR_TOO_LOW}"
    assert rows["filler_ratio"]["ok"] is False
    assert rows["filler_ratio"]["threshold"] == f"≤{ai_taste._FILLER_LIMIT}"
    assert rows["simile_per_1k"]["ok"] is False
    assert rows["simile_per_1k"]["threshold"] == f"<{ai_taste.SIMILE_DENSITY_LIMIT}"


def test_positive_card_has_no_composite_score():
    """正向卡不合成任何评分：字段里不得出现 score 类键。"""
    positive = {"metrics": {"sentence_cv": 0.8, "ttr": 0.5, "filler_ratio": 0.0,
                            "simile_per_1k": 0.5},
                "zero_direct": False, "goods_items": [], "errors": []}
    card = cards.build_positive_card(positive, 1)
    assert "score" not in _walk_keys(card)
    assert "评分" in card["title"]          # 明确声明是"非质量评分"
    assert card["zero_direct"]["ok"] is True


def test_positive_card_declares_gap():
    """破折号密度/面板数字重复未接入 → 明示缺口，不生成指标行。"""
    card = cards.build_positive_card({"metrics": {}, "zero_direct": None,
                                      "errors": ["正文字数不足，指标不可用"]},
                                     1)
    assert card["metrics"] == []
    assert "破折号密度" in card["notice"]
    assert "面板数字重复" in card["notice"]
    assert card["errors"] == ["正文字数不足，指标不可用"]


def test_card_order():
    positive = {"metrics": {"sentence_cv": 0.8, "ttr": 0.5, "filler_ratio": 0.0,
                            "simile_per_1k": 0.5},
                "zero_direct": True, "goods_items": [], "errors": []}
    store = _StubStore(versions=[
        {"v": 1, "selected": False, "words": 100, "quality": {}},
        {"v": 2, "selected": True, "words": 200, "quality": {}},
    ])
    types = [c["type"] for c in cards.build_cards(store, _snap(_review()), positive)]
    assert types == ["quality", "issues", "versions", "positive"]


class _StubStore:
    """只实现卡片用到的版本查询（不触盘、不连库）。"""

    def __init__(self, versions=None):
        self._versions = versions or []

    def list_versions(self, no: int) -> list[dict]:
        return self._versions
