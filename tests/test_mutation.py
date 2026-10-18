# -*- coding: utf-8 -*-
"""P2-2 变异自检 / 检测器灵敏度专项测试（docs/REQ-门禁质检正向影响.md P2-2）。

防检测器「躺平刷通过」：故意写坏章节注入，断言质检能检出（灵敏度）；
同时用正常章节断言不误报（特异性）。全量回归每次跑，检测器若被改坏/被调成
总通过，这里立即红灯。

覆盖变异样本：前文重复、合规违禁、面板数字矛盾；正常样本不误报。
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
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.store.schemas import Chapter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="变异自检书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _mk_llm(reader_result: str = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}',
            rewrite_text: str = ""):
    """按调用顺序返回：第1次=读者校验，第2次=自动重写（本测试只关心确定性检出）。"""
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return reader_result
        return rewrite_text

    llm.chat.side_effect = _chat
    return llm


def _qc(store):
    llm = _mk_llm()
    return QualityChecker(store.load_config(), llm, store)


def _types(result):
    return [i.get("type", "") or "" for i in result["issues"]]


def test_mutation_prev_chapter_repeat_detected(store):
    """变异①：与前章高度重复 → 必须检出「与前文重复」（防重复检测躺平）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="重复段落A" * 200, summary="s"))
    qc = _qc(store)
    dup = "重复段落A" * 200 + "新增内容" * 10
    result = qc.check(2, "第二章", "推进", dup)
    assert any("与前文重复" in t for t in _types(result))


def test_mutation_compliance_detected(store):
    """变异②：注入违禁内容 → 必须检出「合规-疑似违规内容」（防合规躺平）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    qc = _qc(store)
    bad = "正常内容" * 200 + "本章包含对幼女做爱的详细描写。"
    result = qc.check(2, "第二章", "推进", bad)
    assert any("合规" in t for t in _types(result))


def test_mutation_panel_number_stall_detected(store):
    """变异③：面板数字同值跨段复现 → 必须检出（防数字校验躺平）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    store.save_chapter_state({"chapter": 1, "numbers": ["今日可死次数：1/3"]})
    qc = _qc(store)
    stall = ("正常内容" * 200
             + "他看了一眼面板，今日可死次数：1/3。"
             + "正常内容" * 200
             + "厮杀半晌，他又看面板，今日可死次数：1/3。")
    result = qc.check(2, "第二章", "推进", stall)
    assert any("面板" in t or "数字" in t for t in _types(result))


def test_normal_chapter_not_false_positive(store):
    """特异性：正常章节（无变异）不应被确定性检测器误报（防过度拦截躺平反噬）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    qc = _qc(store)
    good = "全新剧情推进，主角继续前行，遇到新冲突，解决后向目标迈进。" * 40
    result = qc.check(2, "第二章", "推进", good)
    types = _types(result)
    # 无结构性/合规/数字类变异问题（LLM mock 返回无 issue）
    assert not any("与前文重复" in t or "合规" in t for t in types)
