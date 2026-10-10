# -*- coding: utf-8 -*-
"""P0-5 修复专项测试（P1，2026-10-10 生成端第四轮）。

覆盖（对应第四轮评估 §5 两个盲区 + 第 5 章根因链）：
- 修复1：_PANEL_RE 补「蚀化」→ _deterministic_numbers 账本含蚀化、panel_baseline_dict 基线含蚀化
- 修复2：QualityChecker.check 叠加 baseline 方向校验（质检层拦蚀化 98→97 回退）
- 修复2：ensure_quality 重写轮内 best-version 兜底（重写"有效但更差"+下一轮无效 → 回退最优版本，坏稿不落盘）
- 修复3：_rewrite_whole dedup_block 命中 2.12「循环重演」关键词 + 无条件「重写即整体替换」拼接禁令
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.chapter_state import (
    _deterministic_numbers, panel_baseline_dict, panel_direction_violations,
)
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _qc(store, llm):
    cfg = store.load_config()
    return QualityChecker(cfg, llm, store, trace=None, guard=None)


# ---------- 修复1：_PANEL_RE 补蚀化（抽取层） ----------

@pytest.mark.p1
def test_deterministic_numbers_includes_蚀化():
    """P0-5 回归：正文「距离蚀化：98%」必须进入确定性账本（此前 _PANEL_RE 缺蚀化后缀）。"""
    text = "他低头看面板，距离蚀化：98%。今日可死次数：2。蚀痕：3/10。"
    nums = _deterministic_numbers(text)
    assert "距离蚀化：98" in nums
    assert "今日可死次数：2" in nums
    assert "蚀痕：3/10" in nums


@pytest.mark.p1
def test_panel_baseline_dict_includes_蚀化_after_fix():
    """P0-5 回归：账本 numbers 含蚀化后，基线 dict 必须带「蚀化」→ 防线4/质检层才能校验。"""
    nums = _deterministic_numbers("距离蚀化：97%。可死次数：1。")
    base = panel_baseline_dict(nums)
    assert base == {"蚀化": 97, "次数": 1}


@pytest.mark.p1
def test_pdv_rollback_detected_with_new_baseline():
    """基线含蚀化 → 正文 98→97 回退必须被 panel_direction_violations 检出（端到端）。"""
    nums = _deterministic_numbers("上一场景结束，距离蚀化：97%。")
    base = panel_baseline_dict(nums)
    viols = panel_direction_violations("距离蚀化：98%。他运功压制，距离蚀化：97%。", base)
    assert any("回退" in v and "蚀化" in v for v in viols)


# ---------- 修复2：QualityChecker.check 叠加 baseline 方向校验 ----------

@pytest.mark.p1
def test_quality_check_baseline_direction_violation(store):
    """check(baseline={"蚀化": 97}) 必须检出正文 98→97 回退（质检层二次兜底）。"""
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4}'
    qc = _qc(store, llm)
    content = "距离蚀化：98%。他运功压制，距离蚀化：97%。"
    result = qc.check(1, "第一章", "目标", content, baseline={"蚀化": 97})
    types = [it["type"] for it in result["issues"]]
    assert "写作-机制数字方向违规" in types
    assert any("回退" in it["detail"] for it in result["issues"] if it["type"] == "写作-机制数字方向违规")


@pytest.mark.p1
def test_quality_check_no_baseline_no_direction(store):
    """无 baseline（整章路径/首场景）→ 质检层不做方向校验（零误伤）。"""
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4}'
    qc = _qc(store, llm)
    result = qc.check(1, "第一章", "目标", "距离蚀化：97%。", baseline=None)
    assert not any(it["type"] == "写作-机制数字方向违规" for it in result["issues"])


# ---------- 修复2：ensure_quality best-version 兜底 ----------

@pytest.mark.p1
def test_ensure_quality_falls_back_to_best_when_rewrite_worse(store):
    """重写1「有效但更差」（2 个问题）+ 重写2 无效 break → 回退最优版本（初始 1 个问题），
    坏稿不落盘（第四轮第 5 章：重写 1 后 4 个问题 > 初始 2 个问题，重写 2 无效 → 坏稿落盘的缺口）。"""
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4}'
    qc = _qc(store, llm)
    init_content = "他站在铁匠铺门口，抬头看了一眼天色。"
    # 坏稿：同一长句重复 → 章内多稿自重复（大改）+ 字数不足 → 2 个问题（比初始更差）
    bad_draft = "他盯着面板数字看，心里盘算着接下来如何应对这场局面。" * 8
    with patch.object(QualityChecker, "_rewrite_once",
                      side_effect=[bad_draft, ""]) as mock_rw:
        chapter, result = qc.ensure_quality(1, "第一章", "目标", init_content, "摘要",
                                            baseline=None)
        assert mock_rw.call_count == 2          # 第 2 次重写无效即 break
        assert chapter.content == init_content  # 回退到最优版本（初始稿）
        assert not result["passed"]
    # 反例：重写稿更优（0 个问题且通过）→ best 更新为重写稿，不误回退初始
    init_issue = {"type": "结构-字数严重不足", "detail": "本章不足目标 30%", "level": "大改"}
    with (
        patch.object(QualityChecker, "check", side_effect=[
            {"passed": False, "issues": [init_issue], "score": 70},
            {"passed": True, "issues": [], "score": 95},
        ]),
        patch.object(QualityChecker, "_rewrite_once", return_value="更好的重写稿正文，内容完整连贯，节奏明快。"),
    ):
        chapter, result = qc.ensure_quality(1, "第一章", "目标", init_content, "摘要",
                                            baseline=None)
        assert chapter.content == "更好的重写稿正文，内容完整连贯，节奏明快。"   # 采用更优的重写稿
        assert result["passed"]


# ---------- 修复3：_rewrite_whole dedup 关键词扩展 + 无条件拼接禁令 ----------

@pytest.mark.p1
def test_rewrite_whole_dedup_block_hits_cycle_retell(store):
    """2.12「情节-同事件循环重演」issue 必须触发 dedup_block（"只允许完整发生一次"），
    并恒含「重写即整体替换」无条件拼接禁令。"""
    llm = MagicMock()
    llm.chat.return_value = "重写后的正文。\n【摘要】死亡次数减至 2/3。"
    qc = _qc(store, llm)
    issues = [{
        "type": "情节-同事件循环重演",
        "detail": "同一情节节拍序列「对峙→面板→诊脉→面板」在章内出现 ≥2 次，"
                  "疑似同事件多版本重演（换措辞循环，文本相似度漏检）",
        "level": "大改",
    }]
    out = qc._rewrite_whole(1, "第一章", "目标", "旧正文内容。", issues)
    assert out
    user_prompt = llm.chat.call_args[0][1]
    assert "只允许完整发生一次" in user_prompt          # dedup_block 已触发（关键词含"循环重演"）
    assert "重写即整体替换" in user_prompt             # 无条件拼接禁令恒在
    assert "多稿拼接" in user_prompt
