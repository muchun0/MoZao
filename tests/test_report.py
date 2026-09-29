# -*- coding: utf-8 -*-
"""成果整合 report.py 4 份设计文档 —— P2 用例。

覆盖（REQ-多Agent协作分层优化，阶段E）：
1. build_reports 生成 4 份文档且内容含关键章节
2. write_reports 落盘到 design/ 目录（临时目录）
3. 空输入不崩（缺省渲染）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.issue_triage import IssueItem
from novel_gen.pipeline.report import build_reports, write_reports
from novel_gen.store.schemas import Outline, World


@pytest.fixture
def world():
    return World.from_dict({
        "world_view": "灵气复苏",
        "theme_proposition": "失去一切后重新定义自我",
        "conflict_matrix": [{"level": "personal", "description": "a"},
                            {"level": "interpersonal", "description": "b"},
                            {"level": "world", "description": "c"}],
        "protagonist_arc": {"belief": "复仇", "flaw": "偏执",
                            "change_nodes": ["放过仇人", "重建"], "final_insight": "活着"},
        "goldfinger_rules": {"trigger": "t", "upper_limit": "u", "lower_limit": "l",
                             "cost": "c", "growth_path": "g"},
        "setting_taboos": ["复活无代价"],
        "foreshadowing": [{"id": "f1", "name": "项链之谜", "buried_ch": 3, "reveal_ch": 50,
                           "level": "large", "content": "来历"}],
    })


@pytest.fixture
def outline():
    return Outline.from_dict({
        "title": "重生之逆流", "core_conflict": "对抗制度", "main_line": "成长",
        "volumes": [{"no": 1, "name": "起", "start_ch": 1, "end_ch": 30, "main_conflict": "c",
                     "climax": "k", "character_growth": "g"}],
        "chapters": [{"no": 1}],
        "key_nodes": [{"kind": "激励事件", "chapter_no": 1, "description": "穿越"}],
        "volume_pacing": [{"volume_no": 1, "payoff_density": "high", "climax_ch": 30,
                           "transition_range": "1-30"}],
    })


@pytest.mark.p2
def test_build_reports_four_docs(world, outline):
    issues = [IssueItem("fatal", "foreshadow", "伏笔只埋不收", "补 reveal_ch")]
    arb = [{"stage": "bottom_anchors", "fatal": [{"desc": "金手指缺失"}], "note": "仲裁放行"}]
    docs = build_reports(world, outline, issues, arb)
    assert set(docs) == {"core-settings.md", "skeleton-outline.md",
                         "pacing-manual.md", "quality-report.md"}
    assert "核心主题命题" in docs["core-settings.md"]
    assert "金手指规则说明书" in docs["core-settings.md"]
    assert "关键节点时间线" in docs["skeleton-outline.md"]
    assert "伏笔回收总表" in docs["pacing-manual.md"]
    assert "问题分级清单" in docs["quality-report.md"]
    assert "致命" in docs["quality-report.md"]


@pytest.mark.p2
def test_write_reports_to_design_dir(world, outline, tmp_path):
    class FakeStore:
        def __init__(self, d):
            self.dir = d

    store = FakeStore(tmp_path)
    paths = write_reports(store, world, outline)
    assert len(paths) == 4
    assert (tmp_path / "design" / "core-settings.md").exists()
    content = (tmp_path / "design" / "core-settings.md").read_text(encoding="utf-8")
    assert "《核心设定册》" in content


@pytest.mark.p2
def test_build_reports_empty_inputs():
    # 空 World / Outline 不崩
    docs = build_reports(World(), Outline())
    assert len(docs) == 4
    assert "（未设定）" in docs["core-settings.md"]
    assert "（未设定）" in docs["skeleton-outline.md"]


@pytest.mark.p2
def test_write_reports_never_raises(world, outline):
    # store 无 dir 属性 → 降级返回 {}，不抛错
    paths = write_reports(object(), world, outline)
    assert paths == {}


@pytest.mark.p2
def test_manager_write_design_reports(tmp_path):
    """manager._write_design_reports：在真实 pipeline 上把 world+outline 落盘 design/。"""
    from unittest.mock import MagicMock

    from novel_gen.config import NovelConfig
    from novel_gen.pipeline.manager import NovelPipeline
    from novel_gen.store.project import ProjectStore
    from novel_gen.store.schemas import (ArcCard, ChapterPlan, ForeshadowItem,
                                         GoldfingerRules, Volume)

    cfg = NovelConfig(title="报告书", chapter_words=200, total_words=600)
    cfg.llm.provider = "mock"
    store = ProjectStore.create(tmp_path / "rep", cfg)
    store.save_outline(Outline(
        title="报告书", core_conflict="c", main_line="m",
        volumes=[Volume(no=1, name="卷一", intro="i", main_conflict="mc",
                        character_growth="cg", climax="cl", start_ch=1, end_ch=1)],
        chapters=[ChapterPlan(no=1, title="第一章", goal="g", transition="t")],
    ))
    store.save_world(World(
        world_view="w", theme_proposition="tp",
        protagonist_arc=ArcCard(belief="b", flaw="f", change_nodes=["a", "b"], final_insight="fi"),
        goldfinger_rules=GoldfingerRules(trigger="t", upper_limit="u", lower_limit="l",
                                         cost="c", growth_path="g"),
        foreshadowing=[ForeshadowItem(name="谜", buried_ch=1, reveal_ch=1, level="small")],
    ))
    pipe = NovelPipeline(cfg, store, MagicMock())
    pipe._write_design_reports(silent=True)
    design = store.dir / "design"
    assert (design / "core-settings.md").exists()
    assert (design / "skeleton-outline.md").exists()
    assert (design / "pacing-manual.md").exists()
    assert (design / "quality-report.md").exists()
    assert "tp" in (design / "core-settings.md").read_text(encoding="utf-8")
