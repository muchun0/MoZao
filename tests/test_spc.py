# -*- coding: utf-8 -*-
"""P2-2 SPC 控制图专项测试（docs/REQ-门禁质检正向影响.md P2-2）。

覆盖：
- i_mr_chart：个体值-移动极差控制图，均值/σ/UCL/LCL/超限信号
- 样本不足 / 无波动 → 无信号（不误报）
- quality_spc_report：聚合 quality_metrics 为三个指标（通过率/重写轮数/违规率）
- QualityChecker.ensure_quality 落 quality_metrics（一次通过/重写轮数/合规命中）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_quality import _LONG_OK_TEXT  # 复用优质长文常量（避免占位文本触发复读检测）

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.pipeline.spc import i_mr_chart, quality_spc_report


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="SPC 测试书", chapter_words=1000, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def test_imr_chart_no_signal_flat():
    chart = i_mr_chart([1, 1, 1, 1])
    assert chart["mean"] == 1.0
    assert chart["sigma_est"] == 0.0
    assert chart["signals"] == []


def test_imr_chart_insufficient():
    assert i_mr_chart([])["signals"] == []
    assert i_mr_chart([0.8])["signals"] == []


def test_imr_chart_detects_outlier():
    # 前面稳定为 0（通过率 0%），尾部跳升 0.9 → 高于 UCL 被标为超限信号
    chart = i_mr_chart([0.0, 0.0, 0.0, 0.0, 0.9, 0.9])
    assert chart["sigma_est"] > 0
    assert chart["ucl"] < 0.9  # 上限低于跳升点
    assert any(i in (4, 5) for i in chart["signals"])


def test_spc_report_aggregates_metrics(store):
    store.save_quality_metric(1, {"passed_once": True, "rewrite_rounds": 0, "compliance_hit": False})
    store.save_quality_metric(2, {"passed_once": False, "rewrite_rounds": 2, "compliance_hit": True})
    rep = quality_spc_report(store.load_quality_metrics())
    assert len(rep["by_chapter"]) == 2
    assert rep["by_chapter"][0]["passed_once"] == 1
    assert rep["by_chapter"][1]["rewrite_rounds"] == 2
    assert rep["passed_once"]["n"] == 2
    assert rep["compliance_hit"]["n"] == 2


def test_spc_report_empty(store):
    rep = quality_spc_report(store.load_quality_metrics())
    assert rep["by_chapter"] == []
    assert rep["passed_once"]["signals"] == []
    assert rep["rewrite_rounds"]["n"] == 0


def test_ensure_quality_records_metric(store):
    """真实链路：ensure_quality 结束落 quality_metrics。"""
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
        return ""

    llm.chat.side_effect = _chat
    qc = QualityChecker(store.load_config(), llm, store)
    chapter, result = qc.ensure_quality(1, "第一章", "推进", _LONG_OK_TEXT, "摘要")
    metric = store.load_quality_metrics().get("1")
    assert metric is not None
    assert metric["passed_once"] is True
    assert metric["rewrite_rounds"] == 0
    assert metric["compliance_hit"] is False
