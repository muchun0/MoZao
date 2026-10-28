# -*- coding: utf-8 -*-
"""recommend_versions 选版推荐语义测试（P0：不得误顶 user_managed / 合规否决）。

覆盖（REQ §5 + TECH §4.2）：
- 合规拒绝版 blocked_compliance，永不推荐
- 用户手写/手选版 user_managed，不参与排序
- eligible=True 中 hook_score 最大者 recommended（hook 权重压过大改数）
- 大改/未过线 → eligible=False
- 全不达标 → 全 neutral，不硬推
- reasons/fatal_issue 文案正确
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.version_diff import recommend_versions  # noqa: E402


def _v(v, *, hook=None, big=0, passed=True, compliance=False,
       source="ai", words=3000):
    qc = {"score": 80, "passed": passed, "hook_score": hook,
          "compliance_rejected": compliance, "issues": []}
    if big:
        qc["issues"] = [{"level": "大改", "type": "写作-x"}] * big
    return {"v": v, "selected": False, "source": source, "words": words,
            "quality": qc}


class TestBadgeStateMachine:
    def test_compliance_rejected_never_recommended(self):
        versions = [
            _v(1, hook=5, compliance=True),
            _v(2, hook=3),
        ]
        out = recommend_versions(versions, target_words=3000)
        b1 = next(x for x in out if x["v"] == 1)
        b2 = next(x for x in out if x["v"] == 2)
        assert b1["badge"] == "blocked_compliance"
        assert b1["fatal_issue"] == "合规被拒，不建议采用"
        assert b2["badge"] == "recommended"

    def test_user_managed_source_not_sorted(self):
        versions = [
            _v(1, hook=5, source="user"),
            _v(2, hook=3),
        ]
        out = recommend_versions(versions)
        b1 = next(x for x in out if x["v"] == 1)
        b2 = next(x for x in out if x["v"] == 2)
        assert b1["badge"] == "user_managed"
        # 手写版不参与推荐排序，机器推荐其余版
        assert b2["badge"] == "recommended"

    def test_user_selected_v_flagged(self):
        versions = [_v(1, hook=5), _v(2, hook=4)]
        out = recommend_versions(versions, user_selected_v=2)
        b2 = next(x for x in out if x["v"] == 2)
        assert b2["badge"] == "user_managed"

    def test_hook_score_wins_over_big_issue(self):
        # v1 hook=5 但有大改 → eligible=False；v2 hook=3 干净 → recommended
        versions = [
            _v(1, hook=5, big=1),
            _v(2, hook=3, big=0),
        ]
        out = recommend_versions(versions, target_words=3000)
        b1 = next(x for x in out if x["v"] == 1)
        b2 = next(x for x in out if x["v"] == 2)
        assert b1["badge"] == "neutral"
        assert b1["fatal_issue"] == "存在 1 条大改问题"
        assert b2["badge"] == "recommended"

    def test_hook_higher_ranked_first(self):
        versions = [
            _v(1, hook=3),
            _v(2, hook=5),
        ]
        out = recommend_versions(versions, target_words=3000)
        b2 = next(x for x in out if x["v"] == 2)
        assert b2["badge"] == "recommended"
        assert any("追更欲 5/5" in r for r in b2["reasons"])

    def test_passed_false_ineligible(self):
        versions = [_v(1, hook=5, passed=False)]
        out = recommend_versions(versions)
        assert out[0]["badge"] == "neutral"
        assert out[0]["fatal_issue"] == "质检未过线"

    def test_all_ineligible_no_force_recommend(self):
        versions = [
            _v(1, hook=1, big=1),
            _v(2, hook=0, passed=False),
        ]
        out = recommend_versions(versions)
        assert all(x["badge"] == "neutral" for x in out)

    def test_word_count_fatal(self):
        # v1 字数严重不达标但 hook 不低；v2 字数达标 hook 更高 → v2 recommended，
        # v1 作为非推荐版拿到字数 fatal
        versions = [
            _v(1, hook=4, words=800),
            _v(2, hook=5, words=3000),
        ]
        out = recommend_versions(versions, target_words=3000)
        b1 = next(x for x in out if x["v"] == 1)
        b2 = next(x for x in out if x["v"] == 2)
        assert b2["badge"] == "recommended"
        assert "字数仅达目标" in (b1["fatal_issue"] or "")

    def test_recommended_reasons_max_three(self):
        versions = [_v(1, hook=5)]
        out = recommend_versions(versions, target_words=3000,
                                 beat_pass={1: True})
        b = out[0]
        assert b["badge"] == "recommended"
        assert len(b["reasons"]) <= 3
        assert any("追更欲 5/5" in r for r in b["reasons"])
        assert any("拍点兑现" in r for r in b["reasons"])
