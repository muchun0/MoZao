# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S1.1 伏笔原文片段存档 专项测试。

覆盖：
- P1：埋设章落盘归档 original_excerpt（commitment items 带原文句）
- P1：due_soon 章 inject_block 附【到期伏笔原文】块（仅开关开启时）
- P1：foreshadow_original_text=False 时不附原文块（向后兼容）
- P2：_extract_excerpt 命中关键字句 / 章首兜底 / 预算截断

占位正文避开节拍词（§11.8）；不依赖真实 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import World, Chapter, ForeshadowItem
from novel_gen.pipeline.reader_ledger import ReaderLedger, _extract_excerpt


def _world() -> World:
    w = World()
    w.foreshadowing = [
        ForeshadowItem(id="f1", name="蚀晶秘密", buried_ch=2, reveal_ch=10,
                       level="medium", content="蚀晶是复活钥匙"),
    ]
    return w


def _chapter_content() -> str:
    return ("少年推门而入，铁箱内躺着一枚蚀晶，泛着幽蓝冷光。"
            "他想起师父说过蚀晶是复活钥匙，心头一沉。"
            "窗外暮色四合，远处传来狼嚎。")


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    s = ProjectStore.create(tmp_path / "sc", cfg)
    s.save_world(_world())
    return s


def _mk_cfg(open_text: bool):
    return NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                       chapter_words=2500, foreshadow_original_text=open_text)


class TestArchiveOriginalExcerpt:
    @pytest.mark.p1
    def test_buried_chapter_archives_excerpt(self, store):
        """埋设章(ch2)写完后，commitment 中该伏笔的 original_excerpt 非空且含关键句。"""
        cfg = _mk_cfg(True)
        store.save_config(cfg)
        ledger = ReaderLedger(store, cfg)
        store.save_chapter(Chapter(no=2, title="x", content=_chapter_content()))
        ledger.update_post_chapter(2)
        pc = store.load_foreshadow_commitment()
        items = pc.get("items", [])
        it = next((x for x in items if x.get("name") == "蚀晶秘密"), None)
        assert it is not None
        assert it.get("original_excerpt")
        assert "蚀晶" in it["original_excerpt"]

    @pytest.mark.p1
    def test_archive_idempotent(self, store):
        """同一章重复 update 不重复覆盖（幂等）。"""
        cfg = _mk_cfg(True)
        store.save_config(cfg)
        ledger = ReaderLedger(store, cfg)
        store.save_chapter(Chapter(no=2, title="x", content=_chapter_content()))
        ledger.update_post_chapter(2)
        first = store.load_foreshadow_commitment()["items"][0]["original_excerpt"]
        store.save_chapter(Chapter(no=2, title="x", content=_chapter_content()))
        ledger.update_post_chapter(2)
        second = store.load_foreshadow_commitment()["items"][0]["original_excerpt"]
        assert second == first

    @pytest.mark.p1
    def test_open_text_off_no_archive(self, store):
        """开关 False 时不归档（向后兼容，不写 original_excerpt）。"""
        cfg = _mk_cfg(False)
        store.save_config(cfg)
        ledger = ReaderLedger(store, cfg)
        store.save_chapter(Chapter(no=2, title="x", content=_chapter_content()))
        ledger.update_post_chapter(2)
        pc = store.load_foreshadow_commitment() or {}
        if pc.get("items"):
            assert not (pc["items"][0].get("original_excerpt") or "")


class TestInjectOriginalBlock:
    def _ledger(self, tmp_path, open_text):
        cfg = _mk_cfg(open_text)
        s = ProjectStore.create(tmp_path / "sc2", cfg)
        s.save_config(cfg)
        s.save_world(_world())
        # 埋设章先落盘归档，才有原文句可注入
        s.save_chapter(Chapter(no=2, title="x", content=_chapter_content()))
        ReaderLedger(s, cfg).update_post_chapter(2)
        return s, ReaderLedger(s, cfg)

    @pytest.mark.p1
    def test_due_soon_has_original_block(self, tmp_path):
        """foreshadow_original_text=True 时，due_soon 章 inject_block 附【到期伏笔原文】块。"""
        s, ledger = self._ledger(tmp_path, True)
        block = ledger.inject_block(7)   # reveal_ch=10, lead=3 → ch7-9 due_soon
        assert "到期伏笔原文" in block
        assert "蚀晶" in block

    @pytest.mark.p1
    def test_open_text_off_no_original_block(self, tmp_path):
        """foreshadow_original_text=False 时，due_soon 章不带【到期伏笔原文】块。"""
        s, ledger = self._ledger(tmp_path, False)
        block = ledger.inject_block(7)
        assert "到期伏笔原文" not in block

    @pytest.mark.p1
    def test_non_due_no_block(self, tmp_path):
        """非 due/overdue 章（ch2 未临期）不注入原文块。"""
        s, ledger = self._ledger(tmp_path, True)
        block = ledger.inject_block(3)
        assert "伏笔承诺·收束提醒" not in block
        assert "到期伏笔原文" not in block


class TestExtractExcerpt:
    @pytest.mark.p2
    def test_hits_keyword_sentence(self):
        """含伏笔关键字的句子被优先抽取。"""
        fs = ForeshadowItem(name="蚀晶", content="蚀晶是复活钥匙", buried_ch=2, reveal_ch=10)
        ex = _extract_excerpt(_chapter_content(), fs)
        assert "蚀晶" in ex
        assert "铁箱" in ex or "师父" in ex   # 命中含关键字的句子

    @pytest.mark.p2
    def test_fallback_to_opening(self):
        """无关键字命中时取章首句兜底。"""
        fs = ForeshadowItem(name="无关物", content="无", buried_ch=2, reveal_ch=10)
        ex = _extract_excerpt(_chapter_content(), fs)
        assert ex
        assert "少年推门而入" in ex

    @pytest.mark.p2
    def test_budget_truncated(self):
        """超预算截断到 max_chars。"""
        fs = ForeshadowItem(name="蚀晶", content="蚀晶是复活钥匙", buried_ch=2, reveal_ch=10)
        ex = _extract_excerpt(_chapter_content(), fs, max_chars=10)
        assert len(ex) <= 10
