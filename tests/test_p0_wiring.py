# -*- coding: utf-8 -*-
"""P0 流水线接线回归（P1，多Agent协作分层优化补接线）。

覆盖：
- P0-2 STAGE_BY_ROLE 补 theme_character/world_rules/pacing 三 key、清 validator 死配置；
  architect 编排时把细分 Agent 的 persona 注入到对应生成阶段。
- P0-1 verify_combined_prompt 注入 ReaderAgent 角色底色（不改变输出 schema，单次调用）。
- P0-3 拆书库回流素材：ProjectStore 读写 + ResearchEngine 并入调研素材。
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
from novel_gen.pipeline.skills import STAGE_BY_ROLE, role_default_stages
from novel_gen.pipeline.templates import verify_combined_prompt
from novel_gen.pipeline.agents.architect import ArchitectAgent
from novel_gen.research.research import ResearchEngine


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.mark.p1
def test_stage_by_role_has_three_subagents_no_validator():
    """P0-2：细分 Agent 在 STAGE_BY_ROLE 登记，validator 死配置移除。"""
    for role in ("theme_character", "world_rules", "pacing"):
        assert role in STAGE_BY_ROLE, f"{role} 缺失"
        assert STAGE_BY_ROLE[role], f"{role} 阶段为空"
    assert "validator" not in STAGE_BY_ROLE, "validator 死配置应移除（非 BaseAgent）"
    assert role_default_stages("theme_character") == ["world"]
    assert role_default_stages("world_rules") == ["world"]
    assert role_default_stages("pacing") == ["outline", "world"]


@pytest.mark.p1
def test_architect_injects_subagent_persona(store):
    """P0-2：architect 把 theme/world_rules persona 注入 world_gen，pacing 注入 outline_gen，
    使细分 Agent 的角色 prompt（含专属技能）真正进入 LLM 生成阶段。"""
    cfg = store.load_config()
    llm = MagicMock()
    arch = ArchitectAgent(cfg, llm, store)
    world_pp = arch._world_gen.role_prompt
    outline_pp = arch._outline_gen.role_prompt
    assert world_pp, "world_gen role_prompt 不应为空"
    assert outline_pp, "outline_gen role_prompt 不应为空"
    assert "主题与人物 Agent" in world_pp        # theme_character.md
    assert "世界观与规则 Agent" in world_pp       # world_rules.md
    assert "节奏与伏笔 Agent" in outline_pp      # pacing.md


@pytest.mark.p1
def test_verify_combined_reader_persona(store):
    """P0-1：传 reader_persona 时注入读者角色要求；不传时保持原行为（无该块）。"""
    cfg = store.load_config()
    _s, user_s = verify_combined_prompt(
        cfg, 3, ["a"], "w", "c",
        reader_persona="【reader角色】你只查读者会不会爽、会不会弃书",
    )
    assert "读者视角检查要求" in user_s
    assert "【reader角色】" in user_s
    assert "命中弃书红线" in user_s
    assert "2. reader" in user_s
    # 不带 persona：不注入读者角色块，输出 schema 不变
    _s2, user2 = verify_combined_prompt(cfg, 3, ["a"], "w", "c")
    assert "读者视角检查要求" not in user2
    assert "2. reader" in user2
    assert '"reader"' in user2


@pytest.mark.p2
def test_teardown_material_store_roundtrip(store):
    """P0-3：回流素材写入/读取幂等，空串读取为空。"""
    store.save_teardown_material("素材A")
    assert store.load_teardown_material() == "素材A"
    store.save_teardown_material("")
    assert store.load_teardown_material() == ""


@pytest.mark.p1
def test_research_material_includes_teardown(store):
    """P0-3：ResearchEngine 把拆书库回流素材并入调研素材。"""
    store.save_teardown_material("拆书回流：爽点密度要高、章末留钩子")
    cfg = store.load_config()
    llm = MagicMock()
    engine = ResearchEngine(cfg, llm, store)
    text = engine._material_text([])  # 无内置/联网素材时也并入回流素材
    assert "拆书库回流素材" in text
    assert "拆书回流：爽点密度要高" in text
