# -*- coding: utf-8 -*-
"""P1-4 定点重写测试（P1，REQ-token优化）。

覆盖：issue 段落定位、句锚点定位、无定位回退整章、窗口段落对齐、
定点替换拼接正确、targeted_rewrite=False 回退整章重写。
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
from novel_gen.pipeline.quality import (QualityChecker, _locate_problem_spans,
                                        _merge_spans, _align_to_paras)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "t", cfg)


_CONTENT = (
    "天还没亮，陆沉在界碑下蹲了一夜。\n"
    "墟口比昨天又大了些，灰雾贴着地面爬。\n"
    "陆沉认出独眼，没急着走近。\n"
    "雾里传来一声响，很轻，像骨头在关节窝里转了一圈。\n"
    "他数了数自己还剩多少块干粮，然后往前迈了一步。"
)


@pytest.mark.p2
def test_locate_by_paragraph_pos(store):
    issues = [{"type": "读者-节奏", "detail": "问题", "pos": "段3"}]
    spans = _locate_problem_spans(_CONTENT, issues)
    assert spans
    s, e = spans[0]
    assert "陆沉认出独眼" in _CONTENT[s:e]


@pytest.mark.p2
def test_locate_by_sentence_anchor(store):
    issues = [{"type": "写作-同句复读",
               "detail": "同一句/同一动作整句原样复读（[叙述×2] 陆沉认出独眼没急着走近……）"}]
    spans = _locate_problem_spans(_CONTENT, issues)
    assert spans
    s, e = spans[0]
    assert "陆沉认出独眼" in _CONTENT[s:e]


@pytest.mark.p2
def test_locate_no_anchor_returns_empty(store):
    issues = [{"type": "结构-字数严重不足", "detail": "本章仅 5 字，不足目标 30%"}]
    assert _locate_problem_spans(_CONTENT, issues) == []


@pytest.mark.p2
def test_align_to_paras(store):
    spans = [(40, 80)]
    aligned = _align_to_paras(_CONTENT, spans)
    s, e = aligned[0]
    assert _CONTENT[s] != " "            # 起点在段落首
    assert e > s


@pytest.mark.p2
def test_merge_spans_close(store):
    merged = _merge_spans([(0, 30), (35, 60)])
    assert len(merged) == 1
    merged2 = _merge_spans([(0, 30), (1000, 1030)])
    assert len(merged2) == 2


@pytest.mark.p2
def test_rewrite_span_replaces_only_window(store):
    """定点重写：只替换问题窗口，窗口外正文原样保留。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = "替换后的新段落。"
    qc = QualityChecker(cfg, llm, store)
    issues = [{"type": "读者-节奏", "detail": "问题", "pos": "段3"}]
    spans = _align_to_paras(_CONTENT, _locate_problem_spans(_CONTENT, issues))
    s, e = spans[0]
    rewritten = qc._rewrite_span(1, "第一章", "目标", _CONTENT, spans[0], issues)
    assert rewritten is not None
    assert "替换后的新段落" in rewritten
    assert _CONTENT[:s] in rewritten      # 窗口前原样
    assert _CONTENT[e:] in rewritten      # 窗口后原样
    assert "陆沉认出独眼" not in rewritten  # 问题窗口被替换
    assert "天还没亮" in rewritten          # 其他段落保留


@pytest.mark.p2
def test_rewrite_once_falls_back_to_whole(store):
    """无定位点（如字数严重不足）→ 回退整章重写。"""
    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = "整章重写后正文。" * 200 + "\n【摘要】重写成功"
    qc = QualityChecker(cfg, llm, store)
    issues = [{"type": "结构-字数严重不足", "detail": "本章仅 5 字，不足目标 30%"}]
    text = qc._rewrite_once(1, "第一章", "目标", "太短了。", issues)
    assert "整章重写后正文" in text
    # 整章重写应调用重写 prompt（非定点窗口 prompt）
    assert "【当前正文】" in llm.chat.call_args[0][1]


@pytest.mark.p2
def test_rewrite_once_targeted_disabled(store):
    """targeted_rewrite=False → 直接整章重写。"""
    cfg = store.load_config()
    cfg.targeted_rewrite = False
    llm = MagicMock()
    llm.chat.return_value = "整章重写后正文。" * 200 + "\n【摘要】重写成功"
    qc = QualityChecker(cfg, llm, store)
    issues = [{"type": "写作-同句复读",
               "detail": "同一句整句复读（[叙述×2] 陆沉认出独眼没急着走近……）"}]
    text = qc._rewrite_once(1, "第一章", "目标", _CONTENT, issues)
    assert "整章重写后正文" in text
    assert "【当前正文】" in llm.chat.call_args[0][1]
