# -*- coding: utf-8 -*-
"""REQ-R31：平台默认 LLM 从火山方舟切到 DeepSeek 的回归用例。

覆盖三处落点：
1. `LLMConfig` 默认值（后端生成链路）
2. `LLMReq` 默认值（新建项目接口）
3. 存量项目迁移工具（`tools/switch_llm_provider.py`）的判定与写盘幂等
"""
from __future__ import annotations

import json

from novel_gen.config import (
    DEFAULT_LLM_MODEL, DEFAULT_LLM_PROVIDER, LLMConfig,
    _apply_provider_defaults, validate_llm_config,
)
from webapp.core_models import LLMReq

from tools import switch_llm_provider as migrator


def test_default_llm_config_is_deepseek():
    cfg = LLMConfig()
    assert DEFAULT_LLM_PROVIDER == "deepseek"
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-flash"


def test_defaults_fill_deepseek_endpoint_and_env_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = _apply_provider_defaults(LLMConfig())
    assert cfg.provider == "deepseek"
    assert cfg.model == DEFAULT_LLM_MODEL
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.api_key == "sk-test"
    assert validate_llm_config(cfg) == []


def test_llm_req_defaults_are_deepseek():
    req = LLMReq()
    assert req.provider == "deepseek"
    assert req.model == "deepseek-flash"


def test_plan_migration_from_ark():
    data = {"llm": {"provider": "doubao", "model": "ep-20261108190349-55bnh",
                    "base_url": ""}}
    assert migrator.plan_migration(data, "deepseek", "deepseek-flash") == {
        "provider": "deepseek", "model": "deepseek-flash",
    }


def test_plan_migration_clears_ark_base_url_only():
    ark = {"llm": {"provider": "ark", "model": "ep-x",
                   "base_url": "https://ark.cn-beijing.volces.com/api/v3"}}
    assert migrator.plan_migration(ark, "deepseek", "deepseek-flash")["base_url"] == ""
    # 用户自填的第三方端点不能被清掉
    custom = {"llm": {"provider": "doubao", "model": "ep-x",
                      "base_url": "https://my-proxy.example.com/v1"}}
    assert "base_url" not in migrator.plan_migration(custom, "deepseek", "deepseek-flash")


def test_plan_migration_skips_mock_and_other_providers_and_is_idempotent():
    mock = {"llm": {"provider": "mock", "model": "mock-model"}}
    assert migrator.plan_migration(mock, "deepseek", "deepseek-flash") is None
    other = {"llm": {"provider": "moonshot", "model": "moonshot-v1-8k"}}
    assert migrator.plan_migration(other, "deepseek", "deepseek-flash") is None
    assert migrator.plan_migration(other, "deepseek", "deepseek-flash", force=True)
    done = {"llm": {"provider": "deepseek", "model": "deepseek-flash"}}
    assert migrator.plan_migration(done, "deepseek", "deepseek-flash") is None


def test_migrate_config_file_writes_backup_and_is_idempotent(tmp_path):
    path = tmp_path / "config.json"
    original = {"title": "测试书", "llm": {"provider": "doubao",
                                         "model": "ep-20261108190349-55bnh",
                                         "base_url": "", "thinking": "off"}}
    path.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")

    assert migrator.migrate_config_file(path, "deepseek", "deepseek-flash",
                                        dry_run=True, force=False) == "dry"
    assert json.loads(path.read_text(encoding="utf-8")) == original  # dry-run 不写盘

    assert migrator.migrate_config_file(path, "deepseek", "deepseek-flash",
                                        dry_run=False, force=False) == "changed"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm"]["provider"] == "deepseek"
    assert saved["llm"]["model"] == "deepseek-flash"
    assert saved["llm"]["thinking"] == "off" and saved["title"] == "测试书"
    backup = path.with_name(path.name + migrator._BACKUP_SUFFIX)
    assert json.loads(backup.read_text(encoding="utf-8")) == original

    # 再跑一次：幂等，不再改写也不再生成备份
    assert migrator.migrate_config_file(path, "deepseek", "deepseek-flash",
                                        dry_run=False, force=False) == "skip"
