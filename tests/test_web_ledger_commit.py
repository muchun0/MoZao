# -*- coding: utf-8 -*-
"""缺陷修复：Web 写章链路补章后账本提交（P0）。

覆盖：
- `_generate_one_chapter` 在结算后调用 `commit_chapter_ledgers`（顺序不可省）；
- `commit_chapter_ledgers` 真正落盘 `chapter_state.json`，同章幂等 skip，force 重跑。

账本抽取算法本身由 test_chapter_state.py 覆盖，这里只测接线与落盘。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                 # noqa: E402
from novel_gen.pipeline.manager import NovelPipeline      # noqa: E402
from novel_gen.store.project import ProjectStore          # noqa: E402
from novel_gen.store.schemas import Chapter              # noqa: E402
from webapp import generate_service, settle_ledger        # noqa: E402

PID = "p2"


def _cfg(**kw) -> NovelConfig:
    cfg = NovelConfig(title="账本测试书", genre="玄幻", total_words=10000,
                      chapter_words=2500)
    cfg.pitfall_reconcile = False
    cfg.book_memory = False
    for key, value in kw.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def store(tmp_path):
    return ProjectStore.create(tmp_path / PID, _cfg())


@pytest.mark.p0
def test_web_generate_one_chapter_commits_ledgers(monkeypatch, store):
    """_generate_one_chapter：写章 → 结算 → 章后账本提交。"""
    calls: list[str] = []
    monkeypatch.setattr(settle_ledger, "snapshot_chapter_versions", lambda s, n: {})
    monkeypatch.setattr(settle_ledger, "settle_chapter_versions", lambda *a, **k: [])
    monkeypatch.setattr(generate_service, "commit_chapter_account", lambda *a, **k: None)
    monkeypatch.setattr(generate_service, "_record_chapter_feedback", lambda *a, **k: None)

    pipeline = MagicMock()
    pipeline.chapter_writer.write.side_effect = lambda *a, **k: calls.append("write")
    pipeline.commit_chapter_ledgers.side_effect = lambda no, **k: calls.append(f"ledger:{no}")
    outline = MagicMock()
    outline.chapters = [MagicMock(title="第1章", goal="下墟")]

    generate_service._generate_one_chapter(
        store, pipeline, outline, None, 1, 1,
        {"mode": "platform", "project_id": PID}, PID, [], [])

    assert calls == ["write", "ledger:1"]


@pytest.mark.p0
def test_commit_chapter_ledgers_writes_chapter_state(monkeypatch, store):
    cfg = _cfg(character_state_tracking=False, reveal_tracking=False,
               reader_ledger=False, chapter_state_tracking=True)
    store.save_chapter(Chapter(no=1, title="第1章", content="他推门而入，屋里空无一人。"))
    pipe = NovelPipeline(cfg, store, MagicMock())
    monkeypatch.setattr(
        "novel_gen.pipeline.chapter_state.extract_chapter_state",
        lambda *a, **k: {"chapter": 1, "numbers": {"今日可死次数": "3"}})

    result = pipe.commit_chapter_ledgers(1)

    assert result["status"] == "committed" and "chapter_state" in result["done"]
    assert store.load_chapter_state()["chapter"] == 1
    # 幂等：已 committed 且非 force → 跳过
    assert pipe.commit_chapter_ledgers(1)["status"] == "skipped"
    # force：正文被改写后必须重跑
    assert pipe.commit_chapter_ledgers(1, force=True)["status"] == "committed"
