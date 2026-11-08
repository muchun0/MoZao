# -*- coding: utf-8 -*-
"""场景重演重试耗尽 → 整章回退路径的返回契约测试（P0 回归）。

缺陷：回退路径 `return self._fallback_whole_chapter(...)` 只返回正文单值，
而调用方按 `text, scenes = self._generate_chapter_scenes(...)` 解包，
一旦触发该路径即抛 `ValueError: too many values to unpack (expected 2)`，
整章生成中断且不落盘。

本用例强制走该路径：场景内部节拍循环命中 → 2 次重试仍命中 → 回退整章。
修复前：解包报 ValueError；修复后：正常返回 (正文, []) 二元组。
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
from novel_gen.pipeline import stages as stages_mod
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def writer(tmp_path):
    cfg = NovelConfig(title="契约测试书", chapter_words=2500, total_words=10000)
    # 关掉依赖 LLM 的语义判定与踩坑对账，保证路径只由本用例的桩控制
    cfg.llm_semantic_beats = False
    cfg.llm_semantic_scene_events = False
    store = ProjectStore.create(tmp_path / "fb", cfg)
    llm = MagicMock()
    llm.chat.return_value = "苏妄言推门而入，抬手敲了三下。" * 2
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


def test_retry_exhausted_fallback_returns_tuple(writer, monkeypatch):
    # 拆场景产物：单场景单事件（避免依赖真实拆场景 LLM）
    monkeypatch.setattr(
        ChapterWriter, "_split_scenes",
        MagicMock(return_value=([{"event": "甲事件", "target_words": 1200}], [], [], "")),
    )
    # 节拍检测：本场景内部循环恒命中，重试也命中 → 必然走整章回退
    monkeypatch.setattr(stages_mod, "extract_beats", lambda *a, **k: ["盘问", "戳穿", "失控", "死亡", "复活"])
    monkeypatch.setattr(stages_mod, "_beat_cycle_issues", lambda *a, **k: [{"type": "节拍循环", "level": "大改"}])
    monkeypatch.setattr(stages_mod, "_beats_rewrite_detected", lambda *a, **k: False)
    monkeypatch.setattr(stages_mod, "_beats_local_rewrite_detected", lambda *a, **k: False)
    monkeypatch.setattr(stages_mod, "_beats_scene_isomorphic", lambda *a, **k: False)
    monkeypatch.setattr(ChapterWriter, "_fallback_whole_chapter", MagicMock(return_value="整章回退正文"))

    # 调用方契约：必须能按二元组解包（修复前此处抛 ValueError）
    text, scenes = writer._generate_chapter_scenes(
        chapter_no=1, plan_title="第一章", plan_goal="目标",
        memory_context="", prev_tail="", is_final=False,
        plan_core_event="", plan_moves="", conflict_names=(),
        silent=True,
    )
    assert text == "整章回退正文"
    assert scenes == []
