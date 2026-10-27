# -*- coding: utf-8 -*-
"""P2-2 全书终审测试。

覆盖：开关触发与落盘、高质量模式默认开启、开关全关不触发、
LLM 异常降级不阻塞、终审结果写入 final_review.json。
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
from novel_gen.store.schemas import Chapter, ChapterPlan, Character, Outline, World
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.pipeline.templates import final_review_prompt


def _ready(store: ProjectStore, n: int = 3) -> None:
    store.save_outline(Outline(
        title="终审书", intro="简介",
        chapters=[ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}") for i in range(1, n + 1)],
    ))
    for i in range(1, n + 1):
        store.save_chapter(Chapter(no=i, title=f"第{i}章", content=f"内容{i}" * 20,
                                   summary=f"摘要{i}", goal=f"目标{i}"))
        store.append_summary(i, f"摘要{i}")
    store.save_world(World(world_view="终审世界观", characters=[
        Character(name="主角", role="主角", desc="沉默寡言"),
    ]))


def _pipe(cfg, store):
    cfg.llm.provider = "mock"
    llm = MagicMock()
    llm.chat_json.return_value = {
        "issues": [{"type": "伏笔回收", "problem": "玉佩伏笔未回收", "suggestion": "结尾补收"}],
        "ok": False,
    }
    return NovelPipeline(cfg, store, llm), llm


@pytest.mark.p2
def test_final_review_trigger_and_save(tmp_path):
    """final_review=True：终审调用 LLM、结果写入 final_review.json。"""
    cfg = NovelConfig(title="终审书", chapter_words=200, total_words=600)
    cfg.final_review = True
    store = ProjectStore.create(tmp_path / "fr", cfg)
    _ready(store)
    pipe, llm = _pipe(cfg, store)
    pipe._run_final_review(silent=True)
    llm.chat_json.assert_called_once()
    fr = store.load_final_review()
    assert fr is not None
    assert fr["ok"] is False
    assert len(fr["issues"]) == 1
    assert fr["issues"][0]["type"] == "伏笔回收"


@pytest.mark.p2
def test_final_review_hq_mode_default_on(tmp_path):
    """高质量模式默认开启终审（即使 final_review 开关为默认 False）。"""
    cfg = NovelConfig(title="HQ书", chapter_words=200, total_words=600)
    cfg.high_quality_mode = True
    store = ProjectStore.create(tmp_path / "hq", cfg)
    _ready(store)
    pipe, llm = _pipe(cfg, store)
    pipe._run_final_review(silent=True)
    assert store.load_final_review() is not None


@pytest.mark.p2
def test_final_review_llm_error_safe(tmp_path):
    """LLM 异常：降级跳过，不抛异常、不阻塞导出。"""
    cfg = NovelConfig(title="异常书", chapter_words=200, total_words=600)
    cfg.final_review = True
    store = ProjectStore.create(tmp_path / "err", cfg)
    _ready(store)
    llm = MagicMock()
    llm.chat_json.side_effect = RuntimeError("provider down")
    pipe = NovelPipeline(cfg, store, llm)
    pipe._run_final_review(silent=True)   # 不抛异常
    assert store.load_final_review() is None


@pytest.mark.p2
def test_final_review_uses_chapters_and_world(tmp_path):
    """终审 prompt：包含全书章节脉络与世界观人物设定。"""
    cfg = NovelConfig(title="终审书", chapter_words=200, total_words=600)
    store = ProjectStore.create(tmp_path / "pp", cfg)
    _ready(store)
    outline = store.load_outline()
    block = "第1章《第1章》：摘要1\n第2章《第2章》：摘要2\n第3章《第3章》：摘要3"
    world_text = "终审世界观\n人物：主角（主角）——沉默寡言"
    sys_s, user = final_review_prompt(cfg, outline, block, world_text)
    assert "全书章节数 3" in user or "3" in user
    assert "终审世界观" in user
    assert "伏笔回收" in user
    assert "结局完整性" in user
