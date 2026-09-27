# -*- coding: utf-8 -*-
"""功能开关注册表测试（P2）。

覆盖：注册表与 NovelConfig 字段一致（单一事实来源）、save/load 注册表驱动往返、
旧项目 config.json 缺开关字段时用默认值、模块分组。
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import (
    FEATURE_FLAGS, NovelConfig, feature_default, feature_modules, list_feature_flags,
)
from novel_gen.store.project import ProjectStore


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "ff", cfg)


def test_registry_matches_novel_config_fields():
    """每个非 guard 开关都在 NovelConfig 有同名字段，且默认值与注册表一致。"""
    cfg = NovelConfig()
    for f in FEATURE_FLAGS:
        if f.module == "guard":
            continue
        assert hasattr(cfg, f.key), f"NovelConfig 缺少字段: {f.key}"
        assert getattr(cfg, f.key) == f.default, f"{f.key} 默认值与注册表不一致"


def test_feature_default_unknown_key_raises():
    with pytest.raises(KeyError):
        feature_default("not_a_real_flag")


def test_feature_modules_grouping():
    mods = feature_modules()
    assert mods == ["memory", "writing", "review", "guard"]
    for f in list_feature_flags("memory"):
        assert f.module == "memory"
    assert list_feature_flags("guard")  # guard 子项注册存在


def test_save_load_roundtrip(store):
    """注册表驱动：改开关值 → save → load 一致（自动落盘，无需手写持久化）。"""
    cfg = store.load_config()
    cfg.memory_direct_read_chapters = 2
    cfg.character_state_tracking = False
    cfg.de_ai = False
    cfg.high_quality_mode = True
    cfg.pacing_density = "high"
    store.save_config(cfg)
    cfg2 = store.load_config()
    assert cfg2.memory_direct_read_chapters == 2
    assert cfg2.character_state_tracking is False
    assert cfg2.de_ai is False
    assert cfg2.high_quality_mode is True
    assert cfg2.pacing_density == "high"


def test_legacy_config_without_feature_fields(store, tmp_path):
    """旧项目 config.json 缺开关字段 → load 自动用注册表默认值（向前兼容）。"""
    import json
    d = json.loads((store.dir / "config.json").read_text(encoding="utf-8"))
    for k in ("character_state_tracking", "memory_direct_read_chapters", "de_ai",
              "high_quality_mode", "pacing_density"):
        d.pop(k, None)
    (store.dir / "config.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    cfg = store.load_config()
    assert cfg.character_state_tracking is True
    assert cfg.memory_direct_read_chapters == 1
    assert cfg.de_ai is True
    assert cfg.high_quality_mode is False
    assert cfg.pacing_density == "medium"


def test_module_main_prints_registry():
    """python -m novel_gen.config 按模块打印开关清单（集中查看入口）。"""
    r = subprocess.run(
        [sys.executable, "-m", "novel_gen.config"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = r.stdout
    assert "[memory]" in out and "[writing]" in out and "[guard]" in out
    assert "memory_direct_read_chapters" in out
