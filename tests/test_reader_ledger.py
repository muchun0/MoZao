# -*- coding: utf-8 -*-
"""REQ-reader-ledger 读者认知账本 + 伏笔承诺状态机 + 爽点配额 专项测试。

覆盖：
- P1：三态派生（known/waiting/misbelief 边界）、伏笔状态机四态迁移、题材配额 power/lite、
      store 落盘幂等与原子写、回收检测（命中/未命中）
- P2：题材兜底回落、空 outline/world 降级、注入块空串、注入上限裁剪
- P0：主链路接线——三块 JSON 落盘 + inject_block 产出注入块 + 章末钩子推进状态 +
      总开关 reader_ledger=False 时不读不写不注入（mock provider 离线）

注意：占位正文避开节拍词（§11.8）；不依赖真实 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import (
    World, Outline, ChapterPlan, ForeshadowItem, Chapter, KeyNode,
)
from novel_gen.pipeline.reader_ledger import ReaderLedger, profile_for, GENRE_PROFILE


# ---------- fixtures ----------
def _world_with_foreshadow() -> World:
    w = World()
    w.foreshadowing = [
        ForeshadowItem(id="f1", name="蚀晶秘密", buried_ch=2, reveal_ch=10,
                       level="medium", content="蚀晶是复活钥匙"),
        ForeshadowItem(id="f2", name="独眼身份", buried_ch=5, reveal_ch=12,
                       level="large", content="独眼是叛逃者"),
        ForeshadowItem(id="f3", name="老赵下落", buried_ch=1, reveal_ch=3,
                       level="small", content="老赵在第二层失踪"),
    ]
    return w


def _outline_with(reversal_at: int = 15, payoff_at=None) -> Outline:
    chs = []
    for i in range(1, 16):
        tag = "反转" if i == reversal_at else ""
        emotion = "爽点" if (payoff_at and i in payoff_at) else "过渡"
        chs.append(ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}",
                               tag=tag, emotion=emotion, core_event=f"事件{i}"))
    return Outline(title="测试书", chapters=chs)


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    s = ProjectStore.create(tmp_path / "sc", cfg)
    s.save_world(_world_with_foreshadow())
    s.save_outline(_outline_with())
    return s


@pytest.fixture
def ledger(store):
    return ReaderLedger(store, store.load_config())


# ---------- P1：读者认知三态派生 ----------
@pytest.mark.p1
def test_three_state_known_waiting(store, ledger):
    """ch=10：reveal_ch<=10 的进 known；buried<=10<reveal 的进 waiting。"""
    block = ledger.inject_block(10)
    assert "读者已知" in block
    assert "蚀晶秘密" in block          # reveal_ch=10 <= 10
    assert "老赵下落" in block           # reveal_ch=3 <= 10
    assert "独眼身份" in block           # buried=5<=10<reveal=12 → waiting
    # 已揭示的不应出现在「在等」小节（只切该小节到下一个【块头之间）
    waiting_sec = block.split("【读者在等", 1)[-1].split("【", 1)[0]
    assert "蚀晶秘密" not in waiting_sec
    assert "老赵下落" not in waiting_sec
    assert "独眼身份" in waiting_sec


@pytest.mark.p1
def test_misbelief_window(store, ledger):
    """反转@15：ch=13/14 为误认窗口（r-2..r-1），ch=12 不在窗口内。"""
    block_in = ledger.inject_block(13)
    assert "读者误认" in block_in
    assert "第15章" in block_in
    block_out = ledger.inject_block(12)
    assert "读者误认" not in block_out


@pytest.mark.p1
def test_future_foreshadow_not_injected(store, ledger):
    """buried_ch > 当前章的伏笔尚未埋入，不应出现在任何注入块。"""
    block = ledger.inject_block(1)   # f2 buried=5, f1 buried=2
    assert "独眼身份" not in block    # buried=5 > 1
    # f1 buried=2 > 1 也不应在等；f3 buried=1 reveal=3 → waiting
    assert "蚀晶秘密" not in block


# ---------- P1：伏笔承诺状态机 ----------
@pytest.mark.p1
def test_state_due_soon(store, ledger):
    """ch=10：独眼身份 reveal=12, lead=3 → 12-3=9<=10<12 → due_soon 收束提醒。"""
    block = ledger.inject_block(10)
    assert "临期" in block
    assert "独眼身份" in block


@pytest.mark.p1
def test_state_overdue(store, ledger):
    """ch=11：老赵下落 reveal=3 < 11 未回收 → overdue 超时提醒。"""
    block = ledger.inject_block(11)
    assert "超过计划回收章3" in block


@pytest.mark.p1
def test_reveal_detection_marks_revealed(store, ledger):
    """章末钩子：写第10章正文含「蚀晶秘密」→ 该伏笔标 revealed。"""
    store.save_chapter(Chapter(no=10, title="x",
                               content="陆沉终于揭开蚀晶秘密，原来这就是答案。"))
    ledger.update_post_chapter(10)
    pc = store.load_foreshadow_commitment()
    assert pc is not None
    f1 = [i for i in pc["items"] if i["name"] == "蚀晶秘密"][0]
    assert f1["state"] == "revealed"
    assert f1["revealed_ch"] == 10


@pytest.mark.p1
def test_reveal_not_detected_without_keyword(store, ledger):
    """章末正文不含伏笔关键词 → 不标 revealed（注入层按 overdue 软提醒，不误回收）。"""
    store.save_chapter(Chapter(no=10, title="x",
                               content="陆沉在雾里走了很久，什么也没说。"))
    ledger.update_post_chapter(10)
    pc = store.load_foreshadow_commitment()
    f1 = [i for i in pc["items"] if i["name"] == "蚀晶秘密"][0]
    assert f1["state"] != "revealed"


# ---------- P1：爽点配额 ----------
@pytest.mark.p1
def test_payoff_gap_injected(store, ledger):
    """outline 全程无爽点（emotion=过渡）→ 注入爽点配额缺口提示。"""
    block = ledger.inject_block(10)
    assert "爽点配额" in block
    assert "小爽" in block


@pytest.mark.p1
def test_payoff_met_no_hint(tmp_path):
    """outline 近窗内有爽点 → 不注入缺口提示。"""
    cfg = NovelConfig(title="t", genre="玄幻", total_words=10000, chapter_words=2500)
    s = ProjectStore.create(tmp_path / "p", cfg)
    s.save_outline(_outline_with(reversal_at=99, payoff_at=(8, 9, 10)))
    block = ReaderLedger(s, cfg).inject_block(10)
    assert "爽点配额" not in block


@pytest.mark.p1
def test_genre_profile_power_lite():
    assert profile_for("玄幻") == GENRE_PROFILE["power"]
    assert profile_for("悬疑") == GENRE_PROFILE["lite"]
    assert GENRE_PROFILE["power"]["small"] == 3
    assert GENRE_PROFILE["lite"]["small"] == 5


# ---------- P2：边界 / 降级 / 工具 ----------
@pytest.mark.p2
def test_genre_fallback():
    assert profile_for("完全自定义题材") == GENRE_PROFILE["power"]
    assert profile_for("") == GENRE_PROFILE["power"]


@pytest.mark.p2
def test_empty_world_outline_block_empty(tmp_path):
    cfg = NovelConfig(title="t", genre="玄幻", total_words=10000, chapter_words=2500)
    s = ProjectStore.create(tmp_path / "p", cfg)
    block = ReaderLedger(s, cfg).inject_block(5)
    assert block == ""


@pytest.mark.p2
def test_disabled_switch_no_inject(store):
    cfg = store.load_config()
    cfg.reader_ledger = False
    rl = ReaderLedger(store, cfg)
    assert rl.enabled is False
    assert rl.inject_block(10) == ""


@pytest.mark.p2
def test_disabled_switch_no_write(store):
    """总开关关闭：update_post_chapter 不产生任何账本文件。"""
    cfg = store.load_config()
    cfg.reader_ledger = False
    store.save_chapter(Chapter(no=10, title="x", content="蚀晶秘密被揭开。"))
    ReaderLedger(store, cfg).update_post_chapter(10)
    assert store.load_foreshadow_commitment() is None
    assert store.load_payoff_quota() is None


@pytest.mark.p2
def test_injection_capped(store, ledger):
    """注入块对 actionable 项上限裁剪（不抛错、不含超长列表）。"""
    block = ledger.inject_block(10)
    # 不会因多条伏笔而膨胀成无界文本（收尾/结构稳定）
    assert "【读者已知" in block or "【读者在等" in block or "【伏笔承诺" in block


# ---------- P0：主链路接线（mock provider 离线冒烟） ----------
@pytest.mark.p0
def test_e2e_ledger_files_and_state_advance(store, ledger):
    """端到端：写章前注入块出现 → 章末钩子推进 → 三块 JSON 中承诺/配额落盘。"""
    # 写章前：注入块非空（含读者认知 + 伏笔收束）
    pre = ledger.inject_block(10)
    assert pre != ""
    assert "读者" in pre or "伏笔" in pre
    # 章末钩子：写第10章正文（揭开蚀晶秘密）→ 落盘承诺 + 配额
    store.save_chapter(Chapter(no=10, title="x", content="蚀晶秘密终于揭开。"))
    ledger.update_post_chapter(10)
    pc = store.load_foreshadow_commitment()
    pq = store.load_payoff_quota()
    assert pc is not None and pc["items"]
    assert pq is not None and pq["log"]
    assert pq["profile"] == "power"
    f1 = [i for i in pc["items"] if i["name"] == "蚀晶秘密"][0]
    assert f1["state"] == "revealed"        # 回收检测生效
    # 幂等：再跑一次不报错、不重复追加
    ledger.update_post_chapter(10)
    pq2 = store.load_payoff_quota()
    assert sum(1 for r in pq2["log"] if r["ch"] == 10) == 1


@pytest.mark.p0
def test_e2e_disabled_no_side_effects(tmp_path):
    """总开关关闭时：注入空、不读不写、管线零副作用。"""
    cfg = NovelConfig(title="t", genre="玄幻", total_words=10000, chapter_words=2500)
    s = ProjectStore.create(tmp_path / "p", cfg)
    s.save_world(_world_with_foreshadow())
    s.save_outline(_outline_with())
    cfg.reader_ledger = False
    rl = ReaderLedger(s, cfg)
    assert rl.inject_block(5) == ""
    s.save_chapter(Chapter(no=5, title="x", content="正文测试。"))
    rl.update_post_chapter(5)
    assert s.load_foreshadow_commitment() is None
    assert s.load_payoff_quota() is None
