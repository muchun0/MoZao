# -*- coding: utf-8 -*-
"""揭示执行跟踪测试（REQ-揭示执行跟踪）：world_reveals 兑现校验闭环。

覆盖：
- P0 提前剧透：真相锚定词在 from_ch 之前正文出现 → check_reveal_execution 判 fatal
- P0 到点未揭示：from_ch ≤ 已写章且正文从未命中 → 判 major
- P1 正常揭示：from_ch 章命中 → 无问题项
- P1 旧数据兼容：无 keywords 的 reveal 不参与确定性检测、不误报；from_dict 兼容
- P1 status 累积：manager._update_reveal_status 多章累积 hit_ch（去重、覆盖保存）
- P2 开关注册：FEATURE_FLAGS / NovelConfig 默认 True；空输入安全返回
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import FEATURE_FLAGS, LLMConfig, NovelConfig, feature_default
from novel_gen.llm.factory import create_llm
from novel_gen.pipeline.chapter_state import detect_reveal_hits
from novel_gen.pipeline.manager import NovelPipeline
from novel_gen.pipeline.validator import check_reveal_execution
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, World, WorldReveal


def _reveal(**kw) -> WorldReveal:
    base = dict(from_ch=10, content="蚀痕的真正来源是上古神魔的封印裂缝。",
                foreshadow_ch=7, hint="旧痕在月圆夜发烫。", keywords=["封印裂缝", "上古神魔"])
    base.update(kw)
    return WorldReveal(**base)


def _world_with(*reveals: WorldReveal) -> World:
    return World(world_view="全量总纲：可死次数系统。", world_reveals=list(reveals))


def _status(from_ch: int, hit_ch: list[int], content: str = "蚀痕的真正来源是上古神魔的封印裂缝。") -> dict:
    return {"0": {"from_ch": from_ch, "content": content[:40], "hit_ch": hit_ch}}


# ---------- P0：提前剧透 / 到点未揭示 ----------

@pytest.mark.p0
def test_early_leak_detected_fatal():
    world = _world_with(_reveal())
    # 正文在第 5 章（< from_ch=10）写出真相锚定词 → 提前剧透
    status = _status(10, [5])
    issues = check_reveal_execution(world, up_to_chapter=12, status=status)
    assert issues and issues[0].severity == "fatal"
    assert "提前剧透" in issues[0].desc
    assert issues[0].chapter_no == 5


@pytest.mark.p0
def test_unfulfilled_at_deadline_major():
    world = _world_with(_reveal())
    # from_ch=10 已过（已写至 12 章），正文从未命中 → 到点未揭示
    status = _status(10, [])
    issues = check_reveal_execution(world, up_to_chapter=12, status=status)
    assert issues and issues[0].severity == "major"
    assert "到点未揭示" in issues[0].desc


# ---------- P1：正常揭示 / 旧数据兼容 / 状态累积 ----------

@pytest.mark.p1
def test_fulfilled_no_issue():
    world = _world_with(_reveal())
    status = _status(10, [10, 11])  # from_ch=10 章起命中
    assert check_reveal_execution(world, up_to_chapter=12, status=status) == []


@pytest.mark.p1
def test_no_keywords_skipped_no_false_positive():
    # 旧数据：无 keywords → 确定性检测跳过，不判提前剧透、不判未揭示
    world = _world_with(WorldReveal(from_ch=10, content="真相A", foreshadow_ch=7, hint="h"))
    assert detect_reveal_hits("正文包含真相A", world.world_reveals[0]) is False
    status = _status(10, [])  # 虽有 status 但 reveal 无 keywords → 校验不报
    assert check_reveal_execution(world, up_to_chapter=12, status=status) == []


@pytest.mark.p1
def test_detect_reveal_hits_keyword_match():
    r = _reveal()
    assert detect_reveal_hits("他摸到蚀痕下的封印裂缝，微微发烫。", r) is True
    assert detect_reveal_hits("旧痕在月圆夜发烫，像有什么要钻出来。", r) is False  # hint 词不算真相


@pytest.mark.p1
def test_reveal_from_dict_compat_old_json():
    # 旧 JSON（无 keywords 字段）→ keywords 默认空、不报错
    r = WorldReveal.from_dict({"from_ch": 15, "content": "x", "foreshadow_ch": 12, "hint": "h"})
    assert r.keywords == []
    # roundtrip：to_dict 后 keywords 保留
    r2 = WorldReveal.from_dict(_reveal().to_dict())
    assert r2.keywords == ["封印裂缝", "上古神魔"]


@pytest.mark.p1
def test_manager_status_accumulate(tmp_path):
    """manager._update_reveal_status 逐章累积 hit_ch：第 9 章未命中、第 10 章命中。"""
    store = ProjectStore.create(tmp_path / "p", NovelConfig(llm=LLMConfig(provider="mock")))
    world = _world_with(_reveal(from_ch=10, keywords=["封印裂缝"]))
    store.save_world(world)
    store.save_chapter(Chapter(no=9, title="铺垫", content="旧痕发烫，他隐约觉得不对劲。"))
    store.save_chapter(Chapter(no=10, title="揭示", content="他终于看清：那是上古封印裂缝的源头。"))

    cfg = NovelConfig(llm=LLMConfig(provider="mock"))
    pipe = NovelPipeline(cfg, store, create_llm(cfg.llm))
    pipe._update_reveal_status(9)
    pipe._update_reveal_status(10)

    status = store.load_reveal_status()
    assert "0" in status
    assert status["0"]["hit_ch"] == [10]  # 第 9 章未命中，第 10 章命中
    assert status["0"]["from_ch"] == 10
    # 幂等：同章重复调用不重复追加
    pipe._update_reveal_status(10)
    assert store.load_reveal_status()["0"]["hit_ch"] == [10]


# ---------- P2：开关 / 空输入 ----------

@pytest.mark.p2
def test_feature_flag_registered_and_default_on():
    assert feature_default("reveal_tracking") is True
    assert any(f.key == "reveal_tracking" and f.module == "memory" for f in FEATURE_FLAGS)
    assert NovelConfig().reveal_tracking is True


@pytest.mark.p2
def test_check_empty_input_safe():
    assert check_reveal_execution(None, 0, {}) == []
    assert check_reveal_execution(World(), 0, {}) == []
    assert check_reveal_execution(_world_with(_reveal()), 0, {}) == []  # 无 status 不报
