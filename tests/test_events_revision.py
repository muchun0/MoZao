# -*- coding: utf-8 -*-
"""事件流版本接线（REQ/TECH-R24 P2 补丁 A）：重写章另立一版 + 旧版标注。

背景：`append()` 的 id 用「本章批次内位置」当 seq，同一章重写时新旧 id 撞车，
新事实会被当成重复静默丢弃，账本停在旧版。这里验证三条：
1. `next_revision()` 首写为 1、写后为 2；
2. 同章重写带 `revision=2` 能真正入库（不再 skipped）；
3. `supersede_revision()` 标注后 `query()` 隐藏旧版、`all()` 仍保留原始行。
"""
import pytest

from novel_gen.pipeline.state.events import EventStream, make_id
from novel_gen.store.project import ProjectStore


@pytest.fixture
def store(tmp_path):
    s = ProjectStore(tmp_path / "book")
    s.dir.mkdir(parents=True, exist_ok=True)
    return s


def _row(ch, seq_desc):
    return {"ch": ch, "desc": seq_desc, "cause": seq_desc,
            "participants": ["陆沉"], "kind": "state"}


def test_next_revision_counts_per_chapter(store):
    stream = EventStream(store)
    assert stream.next_revision(3) == 1
    stream.append([_row(3, "第一次")], ch=3)
    assert stream.next_revision(3) == 2
    assert stream.next_revision(4) == 1  # 别的章不受影响


def test_rewrite_chapter_writes_new_revision(store):
    stream = EventStream(store)
    stream.append([_row(3, "初版甲"), _row(3, "初版乙")], ch=3)
    rev = stream.next_revision(3)
    res = stream.append([_row(3, "改写甲")], ch=3, revision=rev)
    assert rev == 2
    assert res["written"] == 1 and res["skipped"] == 0
    rows = stream.all()
    new_row = [r for r in rows if r.get("revision") == 2][0]
    assert new_row["desc"] == "改写甲"
    assert new_row["id"] == make_id(3, 0, 1, 2)


def test_supersede_hides_old_but_keeps_raw(store):
    stream = EventStream(store)
    stream.append([_row(3, "初版甲"), _row(3, "初版乙")], ch=3)
    stream.append([_row(3, "改写甲")], ch=3, revision=2)
    marked = stream.supersede_revision(3, 2)
    assert marked == 1
    # query() 隐藏旧版（被替代的两条里只有首条有对应新版）
    descs = [r["desc"] for r in stream.query() if r["ch"] == 3]
    assert "改写甲" in descs and "初版甲" not in descs
    # all() 仍保留原始行：可回滚、可重放
    ids = {r["id"] for r in stream.all()}
    assert make_id(3, 0, 1, 1) in ids


def test_supersede_revision_noop_without_old_version(store):
    stream = EventStream(store)
    stream.append([_row(5, "首版")], ch=5)
    assert stream.supersede_revision(5, 1) == 0  # 首版没有可替代的旧版
    assert EventStream(store).superseded() == {}


@pytest.mark.parametrize("rev", [0, -1])
def test_supersede_revision_rejects_bad_revision(store, rev):
    assert EventStream(store).supersede_revision(3, rev) == 0

def test_manager_wiring_rewrite_replaces_ledger(store):
    """生产接线：同一章重生成两次，第二次的事实必须进账本并替代旧版。"""
    from unittest.mock import MagicMock

    from novel_gen.config import NovelConfig
    from novel_gen.pipeline.manager import NovelPipeline

    cfg = NovelConfig(title="t")
    cfg.event_stream = True
    cfg.foreshadow_unified = False
    store.save_config(cfg)
    pipe = NovelPipeline(cfg, store, MagicMock())

    store.save_chapter_state({"chapter": 3, "characters": {"陆沉": ""},
                              "items": {"铜匣": ""},
                              "facts": ["陆沉取出铜匣"], "numbers": []})
    assert pipe._update_state_layer(3) is True
    assert EventStream(store).next_revision(3) == 2

    store.save_chapter_state({"chapter": 3, "characters": {"陆沉": ""},
                              "items": {"铜匣": {"owner": "陆沉", "state": "lost"}},
                              "facts": ["陆沉把铜匣沉入井底"], "numbers": []})
    assert pipe._update_state_layer(3) is True
    ch3 = [r["desc"] for r in EventStream(store).query() if r["ch"] == 3]
    assert "陆沉把铜匣沉入井底" in ch3
    assert "陆沉取出铜匣" not in ch3  # 旧版已被 supersede 隐藏
    assert EventStream(store).next_revision(3) == 3
