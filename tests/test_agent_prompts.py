# -*- coding: utf-8 -*-
"""Agent 人设提示词版本化测试（P1，TECH-Agent用户化与版本化 §3.4 / §14）。

覆盖：默认模板懒快照 v1、用户保存递增版本、prompt_version 格式 <agent>@v<n>、
历史版本不可变、非法 agent / 空内容拒绝。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from webapp import agent_prompts


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", total_words=10000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


@pytest.mark.p1
def test_default_prompt_lazy_snapshot_v1(store):
    """无覆盖时首次读取把系统默认人设懒快照为 v1（author=default）。"""
    text, version, sha = agent_prompts.get_prompt_meta(store, "writer.chapter")
    assert version == "writer.chapter@v1"
    assert len(sha) == 64
    assert text.strip()
    agent_dir = store.dir / "agent_prompts" / "writer_chapter"
    meta = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["current"] == 1
    assert meta["versions"][0]["author"] == "default"
    assert (agent_dir / "v1.md").read_text(encoding="utf-8") == text


@pytest.mark.p1
def test_save_user_prompt_increments_version(store):
    """用户保存 → v2（author=user），默认 v1 原文不可变。"""
    _, v1, sha1 = agent_prompts.get_prompt_meta(store, "architect.outline")
    version2, sha2 = agent_prompts.save_user_prompt(store, "architect.outline", "我的大纲人设")
    assert version2 == "architect.outline@v2"
    assert sha2 != sha1
    text, version, sha = agent_prompts.get_prompt_meta(store, "architect.outline")
    assert (text, version, sha) == ("我的大纲人设", "architect.outline@v2", sha2)
    # v1 仍是默认人设原文（不可变版本，供 run 侧写反查）
    agent_dir = store.dir / "agent_prompts" / "architect_outline"
    assert "大纲架构师" in (agent_dir / "v1.md").read_text(encoding="utf-8")
    meta = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
    assert [v["author"] for v in meta["versions"]] == ["default", "user"]


@pytest.mark.p1
def test_prompt_version_sha_matches_content(store):
    """prompt_sha256 与版本文件内容一致（留痕可反查）。"""
    import hashlib
    text, _, sha = agent_prompts.get_prompt_meta(store, "researcher")
    assert sha == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.p1
def test_invalid_agent_rejected(store):
    with pytest.raises(ValueError):
        agent_prompts.get_prompt_meta(store, "not.an.agent")
    with pytest.raises(ValueError):
        agent_prompts.save_user_prompt(store, "not.an.agent", "x")


@pytest.mark.p1
def test_empty_prompt_rejected(store):
    with pytest.raises(ValueError):
        agent_prompts.save_user_prompt(store, "writer.chapter", "   ")


@pytest.mark.p1
def test_all_editable_agents_have_default(store):
    """白名单内每个 agent 都有默认人设（懒快照均可成功）。"""
    for agent in agent_prompts.EDITABLE_AGENTS:
        text, version, _ = agent_prompts.get_prompt_meta(store, agent)
        assert text.strip() and version == f"{agent}@v1"
