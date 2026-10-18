# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S2.3 固定锚+滚动锚双轨 专项测试。

覆盖：
- P1：fixed_style_anchor 默认关 → 固定锚空串（不注入）
- P1：开启且开头章有正文 → 固定锚含开头原文片段
- P1：开启但开头章无正文/过短 → 空串（空源跳过）
- P1：滚动锚独立（S2.2）仍生效，双轨合并注入非空
- P2：fixed_style_anchor 关闭时两路径不注入（行为兼容，默认路径零变化）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                      chapter_words=1600, style_reanchor_every=5)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _mk_writer(store):
    from unittest.mock import MagicMock
    cfg = store.load_config()
    llm = MagicMock()
    w = ChapterWriter(cfg, llm, store, MagicMock())
    return w, cfg


class TestFixedStyleAnchor:
    @pytest.mark.p1
    def test_default_off_empty(self, store):
        w, cfg = _mk_writer(store)
        assert w._fixed_style_anchor() == ""   # 默认关不注入

    @pytest.mark.p1
    def test_on_with_opening_content(self, store):
        long_content = ("少年敲铁三下，火星溅上旧砧。铁匠铺的炉火映着墙上的碎晶，他数了数今日还能死两次。"
                        "老者没有说话，只把那半块碎晶推过桌面，推到他够得着的地方。炉烟熏得他眼睛发涩，"
                        "他想起父亲死前的那个雨夜，也是这样一盏忽明忽暗的灯，也是这样一句没说完的话。"
                        "墙角的锁链忽然响了一声，像有谁在暗处等着他记住什么。他把碎晶收进怀里，转身走进雨里，"
                        "身后的门没有关。这一晚他睡在桥洞下，梦见自己变成了那把钥匙，插进一扇锈死的心门。"
                        "第二天天亮，他揣着碎晶去找镇上的瘸腿铁匠，铁匠只看了一眼就脸色发白，"
                        "说这东西不该出现在活人的手里。他把碎晶埋在铁匠铺后院的槐树下，每日借口打铁去看一眼，"
                        "直到第七天的夜里，槐树下传来了敲门声，一声接一声，像在数他的心跳。")
        store.save_chapter(Chapter(no=1, content=long_content, summary="开篇"))
        w, cfg = _mk_writer(store)
        cfg.fixed_style_anchor = True
        fa = w._fixed_style_anchor()
        assert "开头第1章原文" in fa
        assert "少年敲铁" in fa

    @pytest.mark.p1
    def test_on_empty_opening_skips(self, store):
        # 无开头章正文 → 空串（空源跳过）
        w, cfg = _mk_writer(store)
        cfg.fixed_style_anchor = True
        assert w._fixed_style_anchor() == ""

    @pytest.mark.p2
    def test_on_short_opening_skips(self, store):
        store.save_chapter(Chapter(no=1, content="太短了。", summary="开篇"))
        w, cfg = _mk_writer(store)
        cfg.fixed_style_anchor = True
        assert w._fixed_style_anchor() == ""


class TestRollingDual:
    @pytest.mark.p1
    def test_rolling_still_works(self, store):
        # 滚动锚（S2.2）在固定锚关时仍独立生效（双轨之一）
        w, cfg = _mk_writer(store)
        cfg.style_reanchor_every = 5
        # 第5章为周期命中章，book_memory goods 空 → 空串（降级安全）
        assert w._rolling_style_anchor(5) == ""
