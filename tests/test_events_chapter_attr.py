# -*- coding: utf-8 -*-
"""事件流章号归属回归：账本降频停在旧章时，事件必须落"本章"而不是旧章。

背景（用户实测）：时间线「已经发生的事件」全挂第 1 章、后续章零事件。
根因：`_update_state_layer` 读"最新章末快照"，而 `state_extract_frequency` 默认 3 章抽
1 次、确定性路径无数字时返回 None → 账本长期停在首章，同号事件又被 id 去重，
于是事件流一条不增。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.state.events import (EventStream, backfill_chapter_events,
                                             derive_chapter_events, derive_summary_events)


def _store(tmp_path):
    return ProjectStore.create(tmp_path / "p", NovelConfig(title="事件归属", total_words=8000,
                                                          genre="玄幻"))


def test_summary_events_use_own_chapter():
    rows = derive_summary_events("沈舟偷绳坠崖。阿桑用藤索把他拽上来；图纸上有小字", 3)
    assert rows and all(r["ch"] == 3 for r in rows)
    assert all(r["inferred"] is True for r in rows)
    assert len(rows) <= 4


def test_chapter_events_prefer_facts_of_same_chapter():
    state = {"chapter": 2, "facts": ["沈舟交出铜匣", "阿桑离开码头"]}
    chapter = Chapter(no=2, title="第2章", summary="沈舟交出铜匣。阿桑离开码头")
    rows = derive_chapter_events(2, state, chapter)
    assert [r["desc"] for r in rows] == ["沈舟交出铜匣", "阿桑离开码头"]
    assert all(r["ch"] == 2 for r in rows)


def test_chapter_events_fallback_when_state_is_stale():
    """账本停在第 1 章时，第 5 章的事件必须写 ch=5（不能用旧章号）。"""
    stale = {"chapter": 1, "facts": ["第1章的事实"]}
    chapter = Chapter(no=5, title="第5章", summary="沈舟在崖顶遇见守灯人。缆绳断了")
    rows = derive_chapter_events(5, stale, chapter)
    assert rows and all(r["ch"] == 5 for r in rows)
    assert all("第1章" not in r["desc"] for r in rows)


def test_chapter_events_empty_when_no_facts_no_summary():
    assert derive_chapter_events(7, {"chapter": 1, "facts": ["旧事实"]}, Chapter(no=7)) == []


def test_backfill_only_fills_empty_chapters_and_is_idempotent(tmp_path):
    store = _store(tmp_path)
    for no in (1, 2, 3):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content="正文" * 5,
                                   summary=f"第{no}章发生了事。又发生了一件事"), source="ai")
    stream = EventStream(store)
    stream.append([{"ch": 1, "desc": "第1章的原事件", "inferred": True}], ch=1, allow_inferred=True)

    res = backfill_chapter_events(store)
    assert res["chapters"] == 2 and res["events"] >= 2
    rows = stream.all()
    assert {int(r["ch"]) for r in rows} == {1, 2, 3}

    again = backfill_chapter_events(store)
    assert again["chapters"] == 0 and again["events"] == 0


def test_resolve_at_ch_takes_max_water_level(tmp_path):
    """读取侧水位：state.json=0 且账本停在第 1 章时，水位必须取已落盘最大章。"""
    from novel_gen.pipeline.book_facts import BookFacts

    store = _store(tmp_path)
    for no in (1, 2, 3, 4, 5):
        store.save_chapter(Chapter(no=no, title=f"第{no}章", content="正文" * 5), source="ai")
    store.save_chapter_state({"chapter": 1, "facts": ["第1章旧事实"]})
    assert store.last_chapter_no() == 5
    assert BookFacts(store, None).resolve_at_ch(0) == 5
