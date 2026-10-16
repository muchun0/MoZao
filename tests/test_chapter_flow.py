# -*- coding: utf-8 -*-
"""章节衔接质量回归测试（P1）。

背景：场景级生成路径（高质量模式/普通模式 ≥2000 字）缺少「承接上一章结尾」
约束——split_scenes_prompt 不知道上一章结尾、scene_prompt 只要求"不要写场景
过渡"、记忆上下文只注入近章 goal 而不注入大纲 transition（架构师设计的转场），
导致章节之间场景硬切、无铺垫转折（实测《万墟之上》第1章结尾在墟底石门，
第2章开头直接切到登记大厅）。

修复后断言：scene_prompt 首/后续场景均含承接硬规则；split_scenes_prompt 注入
prev_tail 并要求首场景承接；memory.build_context 注入本章 transition；_prev_tail
截取 300 字。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import templates
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, ChapterPlan, Outline, Volume


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "fc", cfg)


def _outline_with_transition() -> Outline:
    return Outline(
        title="测试书",
        intro="测试",
        core_conflict="测试",
        main_line="测试",
        volumes=[Volume(no=1, name="卷一", intro="测试", main_conflict="测试",
                        character_growth="测试", climax="测试", start_ch=1, end_ch=2)],
        chapters=[
            ChapterPlan(no=1, title="第一章", goal="第一目标", transition="从A地回到小屋，次日清早"),
            ChapterPlan(no=2, title="第二章", goal="第二目标", transition="从小屋出发前往登记大厅"),
        ],
    )


@pytest.mark.p1
def test_scene_prompt_first_scene_requires_continuation():
    """scene_prompt 首场景必须包含「从上一章结尾直接续写」硬规则。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 2, "第二章", 1,
        {"location": "登记大厅", "event": "排队", "emotion": "压抑",
         "anchors": [], "target_words": 1200},
        "前情记忆", "上一章结尾：他踏上第一级台阶……", "",
    )
    assert "必须从【上一章节结尾】" in user
    assert "禁止硬切换场" in user
    assert "不要写场景之间的过渡总结" not in user  # 旧表述已替换为强制承接


@pytest.mark.p1
def test_scene_prompt_later_scene_requires_continuation():
    """scene_prompt 后续场景必须自然承接上一场景结尾。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 2, "第二章", 2,
        {"location": "黑市", "event": "交易", "emotion": "紧张",
         "anchors": [], "target_words": 1200},
        "前情记忆", "上一场景结尾：他走出登记大厅……", "",
    )
    assert "自然承接上一场景结尾" in user
    assert "禁止硬切换场" in user


@pytest.mark.p1
def test_split_scenes_prompt_injects_prev_tail():
    """split_scenes_prompt 注入上一章结尾并要求首场景承接。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.split_scenes_prompt(
        cfg, 2, "第二章", "被拒绝登记", prev_tail="上一章结尾：他踏上第一级台阶……")
    assert "上一章结尾" in user
    assert "第一个场景必须承接上一章结尾" in user


@pytest.mark.p1
def test_memory_injects_chapter_transition(store):
    """记忆上下文注入本章 transition（架构师设计的转场衔接）。"""
    store.save_outline(_outline_with_transition())
    ctx = Memory(store, memory_window=5).build_context(
        None, up_to_chapter=2, chapter_goal="第二目标")
    assert "L3.5·本章转场衔接" in ctx
    assert "从小屋出发前往登记大厅" in ctx


@pytest.mark.p1
def test_prev_tail_length_300(store):
    """_prev_tail 截取上一章结尾 300 字作为衔接素材。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="承前启后。" * 200))
    writer = ChapterWriter(store.load_config(), None, store, Memory(store))
    tail = writer._prev_tail(2)
    assert len(tail) == 300
    assert "承前启后" in tail


@pytest.mark.p1
def test_scene_prompt_injects_core_event():
    """scene_prompt 注入本章核心事件，正文不得偏离大纲。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.scene_prompt(
        cfg, 1, "第一章", 1,
        {"location": "墟口", "event": "醒来", "emotion": "麻木",
         "anchors": [], "target_words": 1200},
        "前情", "", "",
        core_event="陆沉在墟口外碎石堆醒来，混入逃出的人群",
    )
    assert "【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】" in user
    assert "陆沉在墟口外碎石堆醒来" in user


@pytest.mark.p1
def test_split_scenes_prompt_injects_core_event():
    """split_scenes_prompt 注入核心事件，场景拆分必须覆盖。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.split_scenes_prompt(
        cfg, 1, "第一章", "目标", core_event="灰卒咬穿喉咙，墟口外复活")
    assert "【本章核心事件" in user
    assert "灰卒咬穿喉咙，墟口外复活" in user


@pytest.mark.p1
def test_chapter_prompt_injects_core_event():
    """chapter_prompt（整章路径）注入核心事件。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="跟随临时队下墟，灰卒群溃散",
    )
    assert "【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】" in user
    assert "跟随临时队下墟，灰卒群溃散" in user


@pytest.mark.p1
def test_chapter_prompt_injects_events_sequence():
    """2026-10-01 拆场景失败回退整章：chapter_events（拆场景救回的 events）注入
    事件序列 + 每事件只写一遍纪律——拆场景失败不再丢事件/让模型自发挥重复。"""
    cfg = NovelConfig(title="测试", total_words=10000, genre="玄幻")
    _, user = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="死亡-复活",
        chapter_events="事件1（事件1）：陆沉被灰卒咬穿喉咙，面板 3/3→2/3，复活｜结束时机制状态：面板 2/3，蚀痕越肘弯\n事件2（事件2）：陆沉在碎石坡救下被三只赤尉围住的苏妄言｜结束时机制状态：赤尉退走",
    )
    assert "【本章事件序列" in user
    assert "每事件只写一遍" in user
    assert "陆沉在碎石坡救下被三只赤尉围住的苏妄言" in user
    assert "禁止任何事件重复出现" in user
    # 无 events 时不注入该块，但通用防重复纪律始终存在
    _, user2 = templates.chapter_prompt(
        cfg, chapter_no=1, chapter_title="第一章", chapter_goal="目标",
        research_block="", memory_context="", prev_tail="",
        chapter_core_event="死亡-复活",
    )
    assert "【本章事件序列" not in user2
    assert "3.5 本章每个事件/冲突场面" in user2 and "只允许完整发生一次" in user2


# ---------- 方案4：质检未通过落盘分流（2026-10-07） ----------

@pytest.mark.p1
def test_write_keeps_previous_when_quality_fails(tmp_path, monkeypatch):
    """方案4（落盘分流）+ 版本质量：质检未通过且该章已有旧版 → write() 保留旧版正文
    为当前选中，不把坏章覆盖上去；但本轮重写候选稿以「候选版本」入库（promote=False），
    用户可在版本弹窗查看/选择/对比（重写稿不再被静默丢弃）。"""
    from unittest.mock import MagicMock
    from novel_gen.pipeline.quality import QualityChecker

    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    cfg.high_quality_mode = False          # 走普通整章路径（少 LLM 调用）
    cfg.scene_split = False                # 强制整章生成，避免场景级多次 LLM
    cfg.agent_guard.quality_check = True
    store = ProjectStore.create(tmp_path / "pq", cfg)
    old = Chapter(no=1, title="第一章", content="旧版正文。" * 300, summary="旧版摘要")
    store.save_chapter(old)

    llm = MagicMock()
    # write() 整章生成 + 可能的字数兜底：返回足够长且含摘要的正文
    llm.chat.side_effect = lambda *a, **k: "新版坏文。" * 500 + "\n\n【摘要】新版摘要"

    # monkeypatch 模块4：ensure_quality 返回 passed=False（坏章）+ 版本日志
    # （初始稿 + 每轮重写稿都带质检问题，供 stages 逐版落库）
    def _fake_ensure(self, chapter_no, plan_title, plan_goal, content, summary,
                     silent=False, baseline=None, core_event=""):
        rewrite_text = "重写候选稿。" * 400 + "\n\n【摘要】重写摘要"
        return (Chapter(no=chapter_no, title=plan_title, content=content, summary=summary),
                {
                    "passed": False, "score": 75, "issues": [],
                    "versions": [
                        {"round": 0, "content": content, "summary": summary,
                         "auto_revised": False,
                         "quality": {"passed": False, "score": 75, "issue_count": 0,
                                     "auto_revised": False, "issues": []}},
                        {"round": 1, "content": rewrite_text, "summary": "重写摘要",
                         "auto_revised": True,
                         "quality": {"passed": False, "score": 60, "issue_count": 1,
                                     "auto_revised": True,
                                     "issues": [{"level": "中改", "type": "结构-字数偏少",
                                                 "detail": "字数不足"}]}},
                    ],
                })
    monkeypatch.setattr(QualityChecker, "ensure_quality", _fake_ensure)

    writer = ChapterWriter(cfg, llm, store, Memory(store))
    ch = writer.write(1, "第一章", "目标", None, silent=True)
    # 返回与当前选中的都是旧版正文，坏章未覆盖 HEAD
    assert ch.content == old.content
    assert store.load_chapter(1).content == old.content
    # 版本库：v1=旧版(legacy/ai)选中，v2=初始稿候选，v3=重写候选稿（都保留）
    v = store.list_versions(1)
    assert len(v) >= 3
    assert v[0]["selected"] is True
    # 重写候选稿可单独加载（用户能查看/选择），不再是"看不到、选择不了"
    cand = [x for x in v if x["v"] != v[0]["v"]]
    assert any(x["quality"]["auto_revised"] for x in cand)
    assert any(x["quality"]["score"] == 60 for x in cand)


@pytest.mark.p1
def test_write_versions_all_saved_when_passed(tmp_path, monkeypatch):
    """质检通过：初始稿 + 每轮重写稿逐版落库，且未用户管理时自动选中评分最高版本。"""
    from unittest.mock import MagicMock
    from novel_gen.pipeline.quality import QualityChecker

    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    cfg.high_quality_mode = False
    cfg.scene_split = False
    cfg.agent_guard.quality_check = True
    store = ProjectStore.create(tmp_path / "pq3", cfg)

    llm = MagicMock()
    llm.chat.side_effect = lambda *a, **k: "优质正文。" * 500 + "\n\n【摘要】好摘要"

    def _fake_ensure(self, chapter_no, plan_title, plan_goal, content, summary,
                     silent=False, baseline=None, core_event=""):
        # 真实 ensure_quality：返回的 chapter 即最终采用版本（与 versions[-1] 一致）
        final_text = "重写优选稿。" * 450 + "\n\n【摘要】优摘要"
        final_ch = Chapter(no=chapter_no, title=plan_title, content=final_text,
                           summary="优摘要")
        return (final_ch,
                {
                    "passed": True, "score": 98, "issues": [],
                    "versions": [
                        {"round": 0, "content": content, "summary": summary,
                         "auto_revised": False,
                         "quality": {"passed": True, "score": 95, "issue_count": 0,
                                     "auto_revised": False, "issues": []}},
                        {"round": 1, "content": final_text, "summary": "优摘要",
                         "auto_revised": True,
                         "quality": {"passed": True, "score": 98, "issue_count": 0,
                                     "auto_revised": True, "issues": []}},
                    ],
                })
    monkeypatch.setattr(QualityChecker, "ensure_quality", _fake_ensure)

    writer = ChapterWriter(cfg, llm, store, Memory(store))
    ch = writer.write(1, "第一章", "目标", None, silent=True)
    v = store.list_versions(1)
    assert len(v) == 2
    # 自动选分：评分最高的 v2(98) 为当前选中，HEAD 同步为该版本内容
    assert v[-1]["selected"] is True
    assert v[-1]["quality"]["score"] == 98
    assert "重写优选稿" in store.load_chapter(1).content
    assert "重写优选稿" in ch.content


@pytest.mark.p1
def test_write_saves_new_when_quality_fails_no_previous(tmp_path, monkeypatch):
    """方案4（落盘分流）：质检未通过但该章【无旧版】→ 仍落盘新稿
    （无可用旧版可保留，落盘供前端红标提示，不静默丢弃生成内容）。"""
    from unittest.mock import MagicMock
    from novel_gen.pipeline.quality import QualityChecker

    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    cfg.high_quality_mode = False
    cfg.scene_split = False
    cfg.agent_guard.quality_check = True
    store = ProjectStore.create(tmp_path / "pq2", cfg)

    llm = MagicMock()
    llm.chat.side_effect = lambda *a, **k: "新版坏文。" * 500 + "\n\n【摘要】新版摘要"

    def _fake_ensure(self, chapter_no, plan_title, plan_goal, content, summary,
                     silent=False, baseline=None, core_event=""):
        return (Chapter(no=chapter_no, title=plan_title, content=content, summary=summary),
                {"passed": False, "score": 75, "issues": []})
    monkeypatch.setattr(QualityChecker, "ensure_quality", _fake_ensure)

    writer = ChapterWriter(cfg, llm, store, Memory(store))
    ch = writer.write(1, "第一章", "目标", None, silent=True)
    # 无旧版 → 新稿落盘（后续前端按 review.json 的 passed=false 显示红标）
    assert ch.content.startswith("新版坏文")
    assert store.load_chapter(1) is not None
    assert store.load_chapter(1).content.startswith("新版坏文")
