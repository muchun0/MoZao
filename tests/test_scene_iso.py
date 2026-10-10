# -*- coding: utf-8 -*-
"""P0-10 混合方案①⑤ 测试（2026-10-10）：跨场景同构节拍指纹 + 拆场景评审。

覆盖：
- _beats_scene_isomorphic：v7 ch5 药铺三重拼接命中 / 通用拍不误判 / 短序列不判
- _merge_duplicate_events：评审重复对 → 代码确定性合并（事件+场景去重）
- 拆场景评审 agent：mock LLM 返回重复对 → ChapterWriter._scene_split_review 解析
- 重写前诊断 agent：mock LLM 返回删除清单 → QualityChecker._diagnose_rewrite 解析
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
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.beats import (
    _extract_beat_sequence, _beats_scene_isomorphic,
)
from novel_gen.pipeline.stages import ChapterWriter, _merge_duplicate_events
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


# ---------- ⑤ 跨场景同构节拍指纹 ----------

@pytest.mark.p1
def test_scene_isomorphic_pharmacy_hit():
    """v7 ch5 实证：同一「诊脉→药包→离场」三拍写三遍 → 同构命中（3 拍 + 非通用拍 2）。"""
    seq = _extract_beat_sequence("他进门诊脉，接过药包，转身离开。")
    assert seq == ["诊脉", "药包", "离场"]
    ledger = [_extract_beat_sequence("进店查药粉，又替他诊脉，递上药包，随后离开。")]
    assert _beats_scene_isomorphic(seq, ledger) is True


@pytest.mark.p1
def test_scene_isomorphic_generic_no_false_positive():
    """通用拍（对峙→离场）不判同构——防误杀正常场景。"""
    seq = _extract_beat_sequence("他冷笑对峙，转身离开。")
    ledger = [_extract_beat_sequence("两人对峙，他跟着离开。")]
    assert _beats_scene_isomorphic(seq, ledger) is False


@pytest.mark.p1
def test_scene_isomorphic_short_and_empty():
    assert _beats_scene_isomorphic([], [[]]) is False
    assert _beats_scene_isomorphic(["离场"], [["离场"]]) is False       # <3 拍
    assert _beats_scene_isomorphic(["诊脉", "药包"], [["诊脉", "药包"]]) is False  # <3 拍


# ---------- P0-14 ⑤ 判据放宽（2026-10-10）：占比 0.8→0.6 ----------

@pytest.mark.p1
def test_scene_isomorphic_partial_repeat_hit():
    """v8 ch1 实证：三重拼接中场景 2/3 只重复场景 1 的核心 3~4 拍（面板→留牌→小九→
    离场），ratio≈0.6~0.75——旧 0.8 阈值漏检，放宽 0.6 后命中（非通用拍≥2 防误判）。"""
    seq = _extract_beat_sequence("面板闪了闪，他捏住留牌，小九探过身来，他转身离场，又约定了明日再见。")
    ledger = [_extract_beat_sequence("他触到碎片，面板跳了一下。强撑着对峙，又死了一次，"
                                     "面板亮了，留牌在掌心发烫，小九跟上来，他转身离开。")]
    # 公共子序列 = {面板, 留牌, 小九, 离场} = 4 拍；ratio = 4/min(6, 9) ≈ 0.67 ≥ 0.6
    assert _beats_scene_isomorphic(seq, ledger) is True


@pytest.mark.p1
def test_scene_isomorphic_partial_ratio_below_still_false():
    """放宽后仍守住下界：公共拍占比 < 0.6 不判同构（防误杀半相似场景）。"""
    seq = _extract_beat_sequence("面板亮了，他离开。")
    ledger = [_extract_beat_sequence("他进店查药粉，又替他诊脉，递上药包，随后离开。")]
    # common = {面板, 离场} = 2 拍 < 3 拍下限 → False
    assert _beats_scene_isomorphic(seq, ledger) is False


# ---------- ① 拆场景评审：合并函数（确定性） ----------

@pytest.mark.p1
def test_merge_duplicate_events():
    events = [{"id": "E1", "desc": "进店查药粉"}, {"id": "E2", "desc": "诊脉试探"},
              {"id": "E3", "desc": "探脉枕验毒"}]
    scenes = [{"id": "S1", "scene_event_ref": "E1"}, {"id": "S2", "scene_event_ref": "E2"},
              {"id": "S3", "scene_event_ref": "E3"}]
    merged = _merge_duplicate_events(events, scenes, [(1, 2, "同构")])
    assert merged is not None
    ne, ns = merged
    assert len(ne) == 2 and len(ns) == 2
    assert "E3" not in [e["id"] for e in ne]        # 被删的是 j=2 的 E3（保留 i=1 的 E2）
    assert "S3" not in [s["id"] for s in ns]        # 引用被删事件 → 场景一并删


@pytest.mark.p1
def test_merge_duplicate_events_noop():
    events = [{"id": "E1", "desc": "A"}, {"id": "E2", "desc": "B"}]
    assert _merge_duplicate_events(events, [], []) is None
    assert _merge_duplicate_events(events, [{"id": "S1", "scene_event_ref": "E1"}],
                                   [(0, 1, "x")]) is None  # 合并后仅 1 事件 → 不合并
    assert _merge_duplicate_events([], [], [(0, 1, "x")]) is None


# ---------- ① 拆场景评审 agent（mock LLM） ----------

@pytest.mark.p1
def test_scene_split_review_parses_pairs():
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.return_value = ('{"duplicate_pairs": [{"i": 0, "j": 2, '
                             '"reason": "同是药铺诊脉试探，仅换措辞"}]}')
    w = _writer(cfg, llm, store=None)
    events = [{"id": "E1", "desc": "进店查药粉"}, {"id": "E2", "desc": "诊脉试探"},
              {"id": "E3", "desc": "探脉枕验毒"}]
    scenes = [{"id": "S1", "scene_event_ref": "E1"}, {"id": "S2", "scene_event_ref": "E2"}]
    out = w._scene_split_review(1, "第一章", "目标", events, scenes, silent=True)
    assert out == [(0, 2, "同是药铺诊脉试探，仅换措辞")]
    llm.chat.assert_called_once()


@pytest.mark.p1
def test_scene_split_review_failure_degrades():
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.side_effect = Exception("api down")
    w = _writer(cfg, llm, store=None)
    assert w._scene_split_review(1, "第一章", "目标",
                                 [{"id": "E1", "desc": "A"}, {"id": "E2", "desc": "B"}],
                                 [], silent=True) == []
    # 事件 <2 → 不调用 LLM
    llm2 = MagicMock()
    w2 = _writer(cfg, llm2, store=None)
    assert w2._scene_split_review(1, "第一章", "目标", [{"id": "E1", "desc": "A"}],
                                  [], silent=True) == []
    llm2.chat.assert_not_called()


# ---------- ② 重写前诊断 agent（mock LLM） ----------

@pytest.mark.p1
def test_diagnose_rewrite_parses_findings():
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.return_value = ('{"findings": [{"type": "拼接", "start": 10, "end": 120, '
                             '"action": "删除", "reason": "同一诊脉过程仅换措辞"}]}')
    qc = QualityChecker(cfg, llm, None, trace=None, guard=None, memory=None)
    out = qc._diagnose_rewrite("0123456789" * 20)
    assert out and out[0]["type"] == "拼接" and out[0]["start"] == 10 and out[0]["end"] == 120


@pytest.mark.p1
def test_diagnose_rewrite_failure_degrades():
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.return_value = "not json"
    qc = QualityChecker(cfg, llm, None, trace=None, guard=None, memory=None)
    assert qc._diagnose_rewrite("正文") == []
    llm2 = MagicMock()
    llm2.chat.side_effect = Exception("down")
    qc2 = QualityChecker(cfg, llm2, None, trace=None, guard=None, memory=None)
    assert qc2._diagnose_rewrite("正文") == []


@pytest.mark.p1
def test_diag_block_format():
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    qc = QualityChecker(cfg, MagicMock(), None, trace=None, guard=None, memory=None)
    assert qc._diag_block([]) == ""
    blk = qc._diag_block([{"type": "拼接", "start": 0, "end": 5, "action": "删除",
                           "reason": "重复版本"}])
    assert "删除正文" in blk and "[0:5]" in blk and "重复版本" in blk
