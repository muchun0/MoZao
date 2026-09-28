# -*- coding: utf-8 -*-
"""P2-3 动态模块随机化测试。

覆盖：连续 20 章相邻章节视角/感官/节奏/随机变量不重复；同 seed 序列可复现；
输出格式与注入内容保持既有约定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.templates import _pick_dynamic_module, _LAST_DYNAMIC


def _field(line: str, key: str) -> str:
    for l in line.splitlines():
        if l.startswith(f"- {key}"):
            return l
    raise AssertionError(f"missing field {key} in {line[:60]!r}")


def _seq(genre: str, n: int = 20, seed: int | None = None):
    _LAST_DYNAMIC.clear()
    return [_pick_dynamic_module(i, genre, seed=seed) for i in range(1, n + 1)]


@pytest.mark.p2
def test_no_adjacent_repeat():
    """连续 20 章：相邻章节的视角/感官/节奏/随机变量均不重复（打破轮询规律）。"""
    seq = _seq("玄幻", n=20)
    assert len(seq) == 20
    for i in range(1, len(seq)):
        for key in ("视角", "感官", "节奏", "随机小变量"):
            assert _field(seq[i - 1], key) != _field(seq[i], key), f"第{i}章与第{i+1}章 {key} 重复"


@pytest.mark.p2
def test_seed_reproducible():
    """同 seed：模块序列完全可复现（调试/回归用）。"""
    _LAST_DYNAMIC.clear()
    s1 = [_pick_dynamic_module(i, "都市", seed=42) for i in range(1, 11)]
    _LAST_DYNAMIC.clear()
    s2 = [_pick_dynamic_module(i, "都市", seed=42) for i in range(1, 11)]
    assert s1 == s2


@pytest.mark.p2
def test_format_unchanged():
    """输出格式与注入约定不变：含【本章动态模块】区块与 4 个维度。"""
    m = _pick_dynamic_module(1, "玄幻", seed=1)
    assert "【本章动态模块】" in m
    for key in ("视角", "感官", "节奏", "随机小变量"):
        assert f"- {key}" in m
    assert "不颠覆整体叙事底色" in m


@pytest.mark.p2
def test_genre_pool_applied():
    """题材专属随机变量池生效（悬疑池与玄幻池不同）。"""
    a = _pick_dynamic_module(1, "悬疑", seed=7)
    b = _pick_dynamic_module(1, "玄幻", seed=7)
    assert _field(a, "随机小变量") != _field(b, "随机小变量")


@pytest.mark.p2
def test_seed_and_random_both_work():
    """有/无 seed 两条路径都可调用（seed=None 走随机+去重）。"""
    m = _pick_dynamic_module(3, "default")
    assert "【本章动态模块】" in m
    _LAST_DYNAMIC.clear()
