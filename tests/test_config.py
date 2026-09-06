# -*- coding: utf-8 -*-
"""配置层测试。"""
import os

import pytest

from novel_gen.config import (
    LLMConfig,
    NovelConfig,
    load_config,
    validate_config,
)


def test_num_chapters_computed():
    cfg = NovelConfig(total_words=100_000, chapter_words=2500)
    assert cfg.num_chapters == 40

    cfg2 = NovelConfig(total_words=10_000, chapter_words=3000)
    assert cfg2.num_chapters == 4  # 向上取整

    cfg3 = NovelConfig(total_words=500, chapter_words=1000)
    assert cfg3.num_chapters == 1  # 至少 1 章


def test_load_config_from_yaml(tmp_path):
    y = tmp_path / "config.yaml"
    y.write_text(
        """
title: 测试之书
genre: 科幻
total_words: 20000
chapter_words: 2000
llm:
  provider: deepseek
  temperature: 0.6
""",
        encoding="utf-8",
    )
    cfg = load_config(y)
    assert cfg.title == "测试之书"
    assert cfg.genre == "科幻"
    assert cfg.total_words == 20000
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"          # 默认模型补齐
    assert cfg.llm.base_url == "https://api.deepseek.com"
    assert cfg.llm.temperature == 0.6
    assert cfg.num_chapters == 10


def test_load_config_missing_returns_default(tmp_path):
    cfg = load_config(tmp_path / "nonexist.yaml")
    assert cfg.title == "未命名小说"
    assert cfg.llm.provider == "deepseek"


def test_validate_mock_ok():
    cfg = NovelConfig(llm=LLMConfig(provider="mock"))
    assert validate_config(cfg) == []


def test_validate_deepseek_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cfg = NovelConfig(llm=LLMConfig(provider="deepseek", api_key=""))
    issues = validate_config(cfg)
    assert any("api_key" in i for i in issues)


def test_validate_env_key_satisfies(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-test")
    y = tmp_path / "config.yaml"
    y.write_text("llm:\n  provider: deepseek\n", encoding="utf-8")
    cfg = load_config(y)  # load_config 会回填环境变量中的 key
    assert cfg.llm.api_key == "sk-env-test"
    assert validate_config(cfg) == []
