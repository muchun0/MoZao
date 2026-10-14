# -*- coding: utf-8 -*-
"""三期 F1 Beat 覆盖度门禁 测试（2026-10-14）。

覆盖：
- _split_plan_beats：character_moves → 逐拍；<2 拍返回 []
- _char_bigrams：停用字过滤
- compute_beat_coverage：2/3 拍命中=pass；1/3=fail；无计划拍=skip
- 分级阈值边界：warn 区间落在 warn
- 拍改写不影响命中（bigram 对代词替换鲁棒）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.beat_gate import (
    _split_plan_beats, _char_bigrams, compute_beat_coverage,
)

_MOVES = "主角在墟口遭遇灰卒被咬死→复活后用铁锤砸碎灰卒核心→抱着小九回到铁匠铺"


@pytest.mark.p2
def test_split_plan_beats():
    beats = _split_plan_beats(_MOVES)
    assert len(beats) == 3
    assert _split_plan_beats("只有一拍没有分隔") == []   # <2 拍不误报
    assert _split_plan_beats("") == []
    assert _split_plan_beats("   ") == []


@pytest.mark.p2
def test_char_bigrams_stopwords_filtered():
    grams = _char_bigrams("主角在墟口")  # 在 是停用字
    assert "在墟" not in grams          # 停用字不进 bigram
    assert "墟口" in grams


@pytest.mark.p1
def test_coverage_two_of_three_pass():
    """正文覆盖 2/3 拍（0.67 ≥ 0.6）→ pass。"""
    content = ("陆沉在墟口拾荒时突然遭遇灰卒，被咬喉致死。随后他复活过来，"
               "拿起铁锤狠狠砸碎了灰卒的核心。之后他抱着小九一路走回铁匠铺。")
    r = compute_beat_coverage(content, _MOVES)
    assert r["coverage"] == pytest.approx(0.667, abs=0.02)
    assert r["verdict"] == "pass"
    assert len(r["planned"]) == 3


@pytest.mark.p1
def test_coverage_one_of_three_fail():
    """正文只沾了第 1 拍的边（< 0.4）→ fail 且 missing 列出漏拍。"""
    content = "陆沉在墟口拾荒，灰卒从暗处扑出来。"
    r = compute_beat_coverage(content, _MOVES)
    assert r["coverage"] < 0.4
    assert r["verdict"] == "fail"
    assert len(r["missing"]) >= 1


@pytest.mark.p2
def test_coverage_no_plan_skip():
    """无计划拍（moves/core_event 皆空）→ skip，不误报。"""
    r = compute_beat_coverage("随便一段正文", "", "")
    assert r["verdict"] == "skip"
    assert r["coverage"] is None


@pytest.mark.p2
def test_coverage_falls_back_to_core_event():
    """moves 空但 core_event 有 ≥2 拍 → 用 core_event 评估。"""
    r = compute_beat_coverage(
        "陆沉遭遇灰卒被咬死。复活后砸碎灰卒核心。",
        "", "主角遭遇灰卒被咬死→复活砸碎灰卒核心")
    assert r["verdict"] in ("pass", "warn")
    assert r["coverage"] is not None


@pytest.mark.p2
def test_coverage_paraphrase_robust():
    """拍文本与正文代词/措辞不同但核心词都在 → 命中（bigram 鲁棒性）。"""
    moves = "他踏入密林深处→发现隐藏的古老祭坛"
    content = "陆沉走进密林深处，竟在草丛后发现了一座隐藏的古老祭坛。"
    r = compute_beat_coverage(content, moves)
    assert r["coverage"] >= 0.6
    assert r["verdict"] == "pass"


@pytest.mark.p2
def test_coverage_missing_detail_counts_as_miss():
    """拍要求的关键信息（隐藏）正文没写 → 该拍不命中，覆盖率下降（防假阳性）。"""
    moves = "他踏入密林深处→发现隐藏的古老祭坛"
    content = "陆沉走进密林深处，竟在草丛后发现了一座古老的祭坛。"
    r = compute_beat_coverage(content, moves)
    # 第2拍缺「隐藏」 → 不命中，覆盖率落到 0.5（warn），不是 0 也不是全 pass
    assert 0.0 < r["coverage"] < 0.6

