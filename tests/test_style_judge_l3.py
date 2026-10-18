# -*- coding: utf-8 -*-
"""REQ-超长篇攻坚 S2.5 L3 风格裁判 专项测试。

覆盖：
- P1：style_judge_l3 默认开，最近章+风格原点齐备时触发 LLM 比对
- P1：漂移维度≥阈值 → 报告写入 verify_issues（触发再锚定信号）
- P1：漂移维度<阈值 → 不写入
- P2：开关关 → 不比对
- P2：最近章过短/无风格原点 → 跳过
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
from novel_gen.store.schemas import Chapter


def _long(txt, times=15):
    return txt * times


@pytest.fixture
def manager(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000,
                      chapter_words=1600, verify_every=5, style_judge_l3=True,
                      style_judge_drift_threshold=2)
    store = ProjectStore.create(tmp_path / "sc", cfg)
    from novel_gen.pipeline.manager import NovelPipeline
    llm = MagicMock()
    m = NovelPipeline(cfg, store, llm)
    m.store = store
    m.cfg = cfg
    m.llm = llm
    return m


class TestStyleJudgeL3:
    @pytest.mark.p1
    def test_drift_threshold_trigger(self, manager):
        mgr, store, cfg = manager, manager.store, manager.cfg
        store.save_chapter(Chapter(no=1, content=_long("少年敲铁三下，火星溅上旧砧。铁匠铺的炉火映着墙上的碎晶。"),
                                   summary="开篇"))
        store.save_chapter(Chapter(no=5, content=_long("他站在高台之上，俯瞰群臣俯首。天地之间唯我独尊，一股睥睨众生的气概。"),
                                   summary="后续"))
        mgr.llm.chat.return_value = '{"drifted":["句长","词汇","对话占比"],"summary":"句式模板化，词汇趋同"}'
        mgr._style_judge_l3(5, silent=True)
        issues = [i for i in store.load_verify_issues() if i.get("chapter") == 5]
        assert any("文风漂移-L3" in str(i.get("type", "")) for i in issues)

    @pytest.mark.p1
    def test_below_threshold_no_report(self, manager):
        mgr, store, cfg = manager, manager.store, manager.cfg
        store.save_chapter(Chapter(no=1, content=_long("少年敲铁三下，火星溅上旧砧。铁匠铺的炉火映着墙上的碎晶。"),
                                   summary="开篇"))
        store.save_chapter(Chapter(no=5, content=_long("少年继续敲铁，火星依旧溅上旧砧。铁匠铺的炉火映着墙上的碎晶。"),
                                   summary="后续"))
        mgr.llm.chat.return_value = '{"drifted":["隐喻密度"],"summary":"轻微差异"}'
        mgr._style_judge_l3(5, silent=True)
        issues = [i for i in store.load_verify_issues() if i.get("chapter") == 5]
        assert not any("文风漂移-L3" in str(i.get("type", "")) for i in issues)

    @pytest.mark.p2
    def test_off_skips(self, manager):
        mgr, store, cfg = manager, manager.store, manager.cfg
        cfg.style_judge_l3 = False
        store.save_chapter(Chapter(no=1, content=_long("少年敲铁三下。"), summary="开篇"))
        store.save_chapter(Chapter(no=5, content=_long("少年继续敲铁。"), summary="后续"))
        mgr.llm.chat.side_effect = AssertionError("不应调用 LLM")
        mgr._style_judge_l3(5, silent=True)   # 关闭 → 直接返回

    @pytest.mark.p2
    def test_no_origin_skips(self, manager):
        mgr, store, cfg = manager, manager.store, manager.cfg
        # 无开头章（风格原点缺失）→ 跳过
        store.save_chapter(Chapter(no=5, content=_long("少年继续敲铁。"), summary="后续"))
        mgr.llm.chat.side_effect = AssertionError("不应调用 LLM")
        mgr._style_judge_l3(5, silent=True)
