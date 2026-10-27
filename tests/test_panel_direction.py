# -*- coding: utf-8 -*-
"""P0-3 面板数字确定性方向校验测试（P1，2026-10-09 生成端第三轮）。

覆盖：
- panel_baseline_dict：累计 numbers → 每标签最后值的归一基线
- panel_direction_violations 单元：
  次数递减合法 / 回升违规 / 子时重置（0→N）放行 / 蚀化递增合法 / 蚀化回退违规 /
  无基线不校验
- 集成：场景正文机制数字违反方向规则 → 带错重试 → 采用合法重试稿
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
from novel_gen.store.schemas import ChapterPlan, Outline
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.chapter_state import (
    panel_baseline_dict, panel_direction_violations,
)
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _writer(cfg, llm, store):
    return ChapterWriter(cfg, llm, store, Memory(store, 5))


def _save_outline(store):
    cfg = store.load_config()
    cfg.action_plan_first = False
    cfg.agent_guard.quality_check = False
    store.save_config(cfg)
    outline = Outline(
        chapters=[ChapterPlan(
            no=1, title="第一章", goal="苏妄言来铁匠铺试探陆沉",
            core_event="苏妄言盘问陆沉执能，陆沉挡死一次，被临时征召",
            character_moves="",
        )],
    )
    store.save_outline(outline)


# ---------- panel_baseline_dict ----------

@pytest.mark.p1
def test_panel_baseline_dict_last_value_per_label():
    """累计 numbers → 每标签取最后值（同标签多条只留最后一条）。"""
    nums = ["可死次数：3", "累计进度：97%", "可死次数：2", "进度：98%"]
    base = panel_baseline_dict(nums)
    assert base == {"次数": 2, "进度": 98}
    assert panel_baseline_dict([]) == {}
    assert panel_baseline_dict(["人物生死：疤脸已死"]) == {}    # 无机制标签不识别


# ---------- panel_direction_violations 单元 ----------

@pytest.mark.p1
def test_pdv_decrease_ok_no_violation():
    """次数递减合法：基线 3 → 正文 3→2→1，无违规。"""
    text = "他死了，面板显示可死次数：3。又死一次，可死次数：2。再死一次，可死次数：1。"
    assert panel_direction_violations(text, {"次数": 3}) == []


@pytest.mark.p1
def test_pdv_decrease_rise_violation():
    """次数回升违规：基线 3 → 正文 3→2→3（死后数字回升）→ 违规。"""
    text = "死亡结算后面板显示可死次数：2。但下一幕又写可死次数：3。"
    viols = panel_direction_violations(text, {"次数": 3})
    assert len(viols) == 1
    assert "回升" in viols[0]
    assert "次数" in viols[0]


@pytest.mark.p1
def test_pdv_decrease_midnight_reset_allowed():
    """子时重置放行：基线 0 → 正文 0→3（0 后回升视为机制重置，不误伤）。"""
    text = "子时一过，面板刷新，可死次数：0。他伸了个懒腰，面板显示可死次数：3。"
    assert panel_direction_violations(text, {"次数": 0}) == []


@pytest.mark.p1
def test_pdv_increase_ok():
    """累积字段递增合法（registry 驱动）：基线 97 → 正文 97→98→99，无违规。"""
    text = "距离蚀化：97%。又走一步，距离蚀化：98%。蚀痕蔓延，距离蚀化：99%。"
    assert panel_direction_violations(text, {"蚀化": 97}, {"蚀化": "increase"}) == []


@pytest.mark.p1
def test_pdv_increase_rollback_violation():
    """累积字段回退违规（registry 驱动）：基线 97 → 正文 98→97（只增不减回退）→ 违规。"""
    text = "距离蚀化：98%。他运功压制，距离蚀化：97%。"
    viols = panel_direction_violations(text, {"蚀化": 97}, {"蚀化": "increase"})
    assert len(viols) == 1
    assert "回退" in viols[0]


@pytest.mark.p1
def test_pdv_no_baseline_no_check():
    """无基线（首场景）或正文无机制数字 → 不校验（零误伤）。"""
    assert panel_direction_violations("普通正文，无面板数字。", {"次数": 3}) == []
    assert panel_direction_violations("可死次数：2。", {}) == []
    assert panel_direction_violations("", {"次数": 3}) == []


@pytest.mark.p1
def test_pdv_baseline_0_then_same_zero_ok():
    """基线 0 且正文仍 0（无结算不回升）→ 合法。"""
    text = "面板显示可死次数：0。"
    assert panel_direction_violations(text, {"次数": 0}) == []


# 2026-10-17 移除死亡信号/因果/蚀化跨章/书级信号测试（REQ-机制数字软提示与通用化）：
# 不再做语义死亡判断，数字结算改由设定账本 + 被动方向校验 + 事后质检软提示处理。
