# -*- coding: utf-8 -*-
"""P2-1 few-shot 风格范文锚定测试。

覆盖：空范文不注入、非空范文注入 chapter/scene system prompt、配置持久化往返。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.templates import chapter_prompt, scene_prompt


@pytest.fixture
def cfg():
    c = NovelConfig(title="范文书", chapter_words=1200, total_words=3600)
    c.style_role_reference = "刀锋般的短句。冷冽的对话。人物从不解释自己的动机，行动即答案。"
    return c


@pytest.mark.p2
def test_empty_reference_no_injection():
    """未配置范文：system 不含参考文风块（行为与旧版一致）。"""
    c = NovelConfig(title="无范文", chapter_words=1200, total_words=3600)
    sys_s, _ = chapter_prompt(c, 1, "第一章", "goal", "", "mem", "")
    assert "参考文风样例" not in sys_s


@pytest.mark.p2
def test_reference_injected_into_chapter_prompt(cfg):
    """chapter_prompt：配置了范文则注入 system 且置于 skill 追加之前。"""
    sys_s, _ = chapter_prompt(cfg, 1, "第一章", "goal", "", "mem", "")
    assert "【参考文风样例（只模仿选词、句式、节奏与语感，严禁抄袭具体内容与情节）】" in sys_s
    assert "刀锋般的短句" in sys_s


@pytest.mark.p2
def test_reference_injected_into_scene_prompt(cfg):
    """scene_prompt：场景级生成同样注入范文（动态模块之后）。"""
    scene = {"location": "巷口", "event": "相遇", "emotion": "紧绷",
             "anchors": ["玉佩"], "target_words": 1000}
    sys_s, _ = scene_prompt(cfg, 2, "第二章", 1, scene, "mem", "", "")
    assert "【参考文风样例" in sys_s
    assert "刀锋般的短句" in sys_s


@pytest.mark.p2
def test_reference_persisted_in_config(cfg, tmp_path):
    """store 往返：style_role_reference 随 config.json 持久化。"""
    s = ProjectStore.create(tmp_path / "ref", cfg)
    loaded = s.load_config()
    assert loaded.style_role_reference == cfg.style_role_reference
    # 旧项目缺字段 → 默认空串，不报错
    import json
    d = json.loads(s.config_path().read_text(encoding="utf-8"))
    del d["style_role_reference"]
    s.config_path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert s.load_config().style_role_reference == ""


@pytest.mark.p2
def test_reference_clipped_to_3000_chars(cfg):
    """超长范文截断到 3000 字上限（防 prompt 膨胀）。"""
    c = NovelConfig(title="长范文", chapter_words=1200, total_words=3600)
    c.style_role_reference = "样。" * 5000
    sys_s, _ = chapter_prompt(c, 1, "第一章", "goal", "", "mem", "")
    assert "【参考文风样例" in sys_s
    # 注入内容截断为 3000 字符（"样。"×1500 = 3000 字符）
    assert "样。" * 1500 in sys_s
    assert ("样。" * 1501) not in sys_s
