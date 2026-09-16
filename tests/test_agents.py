# -*- coding: utf-8 -*-
"""Agent 角色拆分的单元测试（TDD）。

覆盖：
1. 每个 agent 的 role_name 正确、prompt 文件能加载
2. _system_with_role() 拼接顺序正确
3. skill 按角色过滤逻辑（用 mock，不真连数据库）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 项目根加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.agents import (
    BaseAgent, ResearcherAgent, ArchitectAgent, WriterAgent, ReviewerAgent,
)
from novel_gen.pipeline.agents.base import _PROMPTS_DIR


# ---------- fixtures ----------

@pytest.fixture
def fake_cfg():
    cfg = MagicMock()
    cfg.mode = "auto"
    cfg.skill_prompt_append = ""
    cfg.custom_prompt = ""
    cfg.high_quality_mode = False
    cfg.memory_window = 5
    return cfg


@pytest.fixture
def fake_llm():
    return MagicMock()


@pytest.fixture
def fake_store():
    store = MagicMock()
    store.dir = Path("/tmp/fake_project")
    return store


# ---------- 1. role_name 和 prompt 文件 ----------

@pytest.mark.parametrize("agent_cls,expected_role", [
    (ResearcherAgent, "researcher"),
    (ArchitectAgent, "architect"),
    (WriterAgent, "writer"),
    (ReviewerAgent, "reviewer"),
])
def test_agent_role_name(agent_cls, expected_role, fake_cfg, fake_llm, fake_store):
    """每个 agent 的 role_name 必须和类名对应。"""
    if agent_cls is WriterAgent:
        agent = agent_cls(fake_cfg, fake_llm, fake_store, MagicMock())
    else:
        agent = agent_cls(fake_cfg, fake_llm, fake_store)
    assert agent.role_name == expected_role


def test_prompt_files_exist():
    """四个角色的 prompt 文件都必须存在。"""
    for role in ("researcher", "architect", "writer", "reviewer"):
        p = _PROMPTS_DIR / f"{role}.md"
        assert p.exists(), f"缺少 prompts/{role}.md"
        assert p.stat().st_size > 50, f"{role}.md 内容太少，可能是空文件"


def test_role_prompt_loaded(fake_cfg, fake_llm, fake_store):
    """role_prompt 从文件加载，不是空字符串。"""
    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    prompt = agent.role_prompt
    assert "写手" in prompt or "Writer" in prompt
    assert "写作" in prompt or "铁律" in prompt


def test_role_prompt_cached(fake_cfg, fake_llm, fake_store):
    """role_prompt 懒加载，只读一次文件。"""
    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    p1 = agent.role_prompt
    p2 = agent.role_prompt
    assert p1 is p2  # 同一对象


# ---------- 2. _system_with_role 拼接顺序 ----------

def test_system_contains_role_prompt(fake_cfg, fake_llm, fake_store):
    """_system_with_role 必须包含角色 prompt。"""
    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    with patch.object(BaseAgent, "_role_skill_append", return_value=""):
        sys_text = agent._system_with_role()
    assert "写手" in sys_text


def test_system_contains_extra(fake_cfg, fake_llm, fake_store):
    """extra 参数必须拼进去。"""
    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    with patch.object(BaseAgent, "_role_skill_append", return_value=""):
        sys_text = agent._system_with_role(extra="临时附加要求：多用短句")
    assert "临时附加要求" in sys_text


# ---------- 3. skill 按角色过滤 ----------

def test_role_skill_filtered_by_role(fake_cfg, fake_llm, fake_store):
    """writer agent 只拿到 role='writer' 和 role='' 的 skill，不拿 reviewer 的。"""
    mock_skills_db = {
        ("writer",): [{"name": "短句流", "prompt_append": "多用短句"}],
        ("",): [{"name": "全局", "prompt_append": "不要暴力"}],
    }

    def fake_list(only_active=True, role=None):
        return mock_skills_db.get((role,), [])

    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    with patch("webapp.db.list_skills", side_effect=fake_list):
        block = agent._role_skill_append()
    assert "短句流" in block
    assert "不要暴力" in block
    # reviewer 的 skill 不应出现
    assert "审校专属" not in block


def test_role_skill_db_error_fallback(fake_cfg, fake_llm, fake_store):
    """数据库挂了也不能让 agent 崩，返回空字符串。"""
    agent = WriterAgent(fake_cfg, fake_llm, fake_store, MagicMock())
    with patch("webapp.db.list_skills", side_effect=RuntimeError("db down")):
        block = agent._role_skill_append()
    assert block == ""


# ---------- 4. manager 能实例化所有 agent ----------

def test_manager_has_all_agents():
    """NovelPipeline 实例化时必须有四个 agent 属性。"""
    from novel_gen.pipeline.manager import NovelPipeline
    # 不真的实例化（需要 DB），只检查类定义里 __init__ 有这些属性
    import inspect
    src = inspect.getsource(NovelPipeline.__init__)
    assert "ResearcherAgent" in src
    assert "ArchitectAgent" in src
    assert "WriterAgent" in src
    assert "ReviewerAgent" in src
