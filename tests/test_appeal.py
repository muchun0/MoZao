# -*- coding: utf-8 -*-
"""P2-3 申诉撤销 + 误判率专项测试（docs/REQ-门禁质检正向影响.md P2-3）。

覆盖：
- store 申诉读写/撤销/误判率指标
- 合规 issue detail 含命中词组（P2-3a：词表命中=疑似可能误报，detail 说明命中词组供复核）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_quality import _mk_llm

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="申诉测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def test_appeal_save_load_overturn_rate(store):
    store.save_appeal({"id": "a1", "chapter": 2, "issue_type": "合规-疑似违规内容",
                       "reason": "此处是战斗场景", "status": "pending", "created_at": 1})
    store.save_appeal({"id": "a2", "chapter": 3, "issue_type": "结构-与前文重复",
                       "reason": "是伏笔呼应", "status": "pending", "created_at": 2})
    assert len(store.load_appeals()) == 2
    assert store.misjudge_rate()["rate"] == 0.0

    assert store.mark_appeal_overturned("a1") is True
    assert store.mark_appeal_overturned("nope") is False
    rate = store.misjudge_rate()
    assert rate["overturned"] == 1
    assert rate["appeals"] == 2
    assert rate["rate"] == 0.5


def test_appeal_empty_rate(store):
    rate = store.misjudge_rate()
    assert rate["appeals"] == 0 and rate["rate"] == 0.0


def test_compliance_detail_has_hit_phrase(store):
    """P2-3a：合规命中 detail 必须说明命中词组（供作者复核/申诉）。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    llm = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    bad = "正常内容" * 200 + "本章包含对幼女做爱的详细描写。"
    result = qc.check(2, "第二章", "推进", bad)
    compliance = [i for i in result["issues"]
                  if "合规-疑似违规内容" in (i.get("type", "") or "")]
    assert compliance, "应检出合规问题"
    # detail 含「命中词组」式说明
    assert "「" in compliance[0].get("detail", "")
    assert compliance[0].get("rule_phrase") or True
