# -*- coding: utf-8 -*-
"""双锚风格锚定（REQ-双锚风格锚定）回归测试。

覆盖：
- Chapter.user_edited 序列化（新增字段，旧数据兼容）
- extract_personal_anchor：只抽人工编辑章、限段限长、无人工章返回空
- build_teardown_anchor：styles/takeaways/excerpts 组装、excerpts 限段限长、空输入
- wrap_style_anchor：空文本不注入
- ProjectStore.teardown_style_anchor 读写 roundtrip
- templates 三路径注入：scene/chapter 题材锚（含开关控制）；expand 风格范文+个人锚+题材锚
- webapp._inject_teardown_style_anchor：查库组装写文件、开关关写空、异常静默
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import templates
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.pipeline.style_anchor import (
    PERSONAL_ANCHOR_HEADER, TEARDOWN_ANCHOR_HEADER,
    build_teardown_anchor, extract_personal_anchor, wrap_style_anchor,
)
from novel_gen.pipeline.templates import (
    chapter_prompt, expand_chapter_prompt, scene_prompt,
)
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from webapp import core


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试", chapter_words=2500, total_words=10000, genre="玄幻")
    return ProjectStore.create(tmp_path / "p", cfg)


def _ch(no: int, content: str, user_edited: bool = False, source: str = "ai") -> Chapter:
    return Chapter(no=no, title=f"第{no}章", content=content, source=source,
                   user_edited=user_edited)


# ---------- Chapter.user_edited 序列化 ----------

@pytest.mark.p1
def test_chapter_user_edited_roundtrip():
    ch = _ch(1, "正文A", user_edited=True)
    d = ch.to_dict()
    assert d["user_edited"] is True
    back = Chapter.from_dict(d)
    assert back.user_edited is True


@pytest.mark.p1
def test_chapter_user_edited_old_data_default_false():
    d = {"no": 1, "title": "旧章", "content": "旧数据", "summary": "", "goal": "", "source": "ai"}
    assert Chapter.from_dict(d).user_edited is False


# ---------- extract_personal_anchor ----------

@pytest.mark.p1
def test_extract_only_edited_chapters():
    chapters = [
        _ch(1, "AI 生成的原文，未编辑" * 10),
        _ch(2, "用户亲自修改过的段落" * 20, user_edited=True),
        _ch(3, "另一段人工精修文字" * 20, user_edited=True),
    ]
    out = extract_personal_anchor(chapters)
    assert "用户亲自修改过" in out
    assert "另一段人工精修文字" in out
    # 未编辑章内容不出现
    assert "AI 生成的原文" not in out


@pytest.mark.p1
def test_extract_no_edited_returns_empty():
    chapters = [_ch(1, "AI 生成" * 50), _ch(2, "AI 生成" * 50)]
    assert extract_personal_anchor(chapters) == ""


@pytest.mark.p1
def test_extract_empty_list_returns_empty():
    assert extract_personal_anchor([]) == ""
    assert extract_personal_anchor(None) == ""


@pytest.mark.p1
def test_extract_respects_total_limit():
    long_content = "人工精修段落。" * 500  # 远超上限
    chapters = [_ch(1, long_content, user_edited=True)]
    out = extract_personal_anchor(chapters, max_total=400)
    assert len(out) <= 400
    # 每章最多 2 段
    assert out.count("\n\n") <= 1


# ---------- build_teardown_anchor ----------

@pytest.mark.p1
def test_build_teardown_anchor_assembles_fields():
    out = build_teardown_anchor(
        styles=["短句快节奏+口语化吐槽", "对话多旁白少"],
        takeaways=["每300字一个小钩子", "章末必断章"],
        excerpts=["原文片段一" * 30, "原文片段二" * 30],
    )
    assert "文风特点" in out
    assert "短句快节奏+口语化吐槽" in out
    assert "可借鉴手法" in out
    assert "每300字一个小钩子" in out
    assert "原文片段" in out
    # 单段截到 100 字
    for line in out.split("\n"):
        if line.startswith("· 原文片段"):
            assert len(line) - len("· ") <= 100


@pytest.mark.p1
def test_build_teardown_anchor_excerpts_limited_to_two():
    excerpts = [f"片段{i}" * 50 for i in range(5)]
    out = build_teardown_anchor(styles=[], takeaways=[], excerpts=excerpts)
    # 只保留前 2 段引用
    excerpt_lines = [l for l in out.split("\n") if l.startswith("· 片段")]
    assert len(excerpt_lines) == 2
    assert "片段2" not in out and "片段3" not in out and "片段4" not in out


@pytest.mark.p1
def test_build_teardown_anchor_empty_returns_empty():
    assert build_teardown_anchor(None, None, None) == ""
    assert build_teardown_anchor([], [], []) == ""


@pytest.mark.p1
def test_wrap_style_anchor_empty_returns_empty():
    assert wrap_style_anchor(PERSONAL_ANCHOR_HEADER, "") == ""
    assert wrap_style_anchor(PERSONAL_ANCHOR_HEADER, "   ") == ""


# ---------- store 读写 ----------

@pytest.mark.p1
def test_store_teardown_style_anchor_roundtrip(store):
    store.save_teardown_style_anchor("题材锚A")
    assert store.load_teardown_style_anchor() == "题材锚A"
    store.save_teardown_style_anchor("")
    assert store.load_teardown_style_anchor() == ""
    # 文件不存在时不抛异常返回空串
    import shutil
    (store.dir / "teardown_style_anchor.txt").unlink(missing_ok=True)
    assert store.load_teardown_style_anchor() == ""


# ---------- templates 注入 ----------

def _scene():
    return {"anchors": [], "event": "测试事件", "final_state": "", "target_words": 300}


@pytest.mark.p1
def test_scene_prompt_injects_teardown_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = scene_prompt(
        cfg, 1, "第一章", 1, _scene(), "记忆", "", "",
        teardown_anchor="题材锚文本XYZ",
    )
    assert TEARDOWN_ANCHOR_HEADER in sys_p
    assert "题材锚文本XYZ" in sys_p


@pytest.mark.p1
def test_scene_prompt_injects_personal_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = scene_prompt(
        cfg, 1, "第一章", 1, _scene(), "记忆", "", "",
        personal_anchor="本人旧稿片段XYZ",
    )
    assert PERSONAL_ANCHOR_HEADER in sys_p
    assert "本人旧稿片段XYZ" in sys_p


@pytest.mark.p1
def test_scene_prompt_skips_empty_personal_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = scene_prompt(cfg, 1, "第一章", 1, _scene(), "记忆", "", "")
    assert PERSONAL_ANCHOR_HEADER not in sys_p


@pytest.mark.p1
def test_scene_prompt_skips_empty_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = scene_prompt(cfg, 1, "第一章", 1, _scene(), "记忆", "", "")
    assert TEARDOWN_ANCHOR_HEADER not in sys_p


@pytest.mark.p1
def test_scene_prompt_skips_when_flag_off():
    cfg = NovelConfig(title="测试", total_words=10000)
    cfg.teardown_style_anchor = False
    sys_p, _ = scene_prompt(
        cfg, 1, "第一章", 1, _scene(), "记忆", "", "",
        teardown_anchor="题材锚文本XYZ",
    )
    assert TEARDOWN_ANCHOR_HEADER not in sys_p
    assert "题材锚文本XYZ" not in sys_p


@pytest.mark.p1
def test_chapter_prompt_injects_teardown_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = chapter_prompt(
        cfg, 1, "第一章", "目标", "调研", "记忆", "上一章尾",
        teardown_anchor="题材锚文本ABC",
    )
    assert TEARDOWN_ANCHOR_HEADER in sys_p
    assert "题材锚文本ABC" in sys_p


@pytest.mark.p1
def test_chapter_prompt_injects_personal_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = chapter_prompt(
        cfg, 1, "第一章", "目标", "调研", "记忆", "上一章尾",
        personal_anchor="本人旧稿片段ABC",
    )
    assert PERSONAL_ANCHOR_HEADER in sys_p
    assert "本人旧稿片段ABC" in sys_p


@pytest.mark.p1
def test_chapter_writer_personal_anchor_lazy(store):
    """ChapterWriter 懒提取个人旧稿锚：无人工编辑章返回空，有则只含 user_edited 章，
    二次调用走缓存（内容不变）。"""
    writer = ChapterWriter(store.load_config(), None, store, Memory(store, 5))
    assert writer._personal_anchor() == ""

    store.save_chapter(_ch(1, "用户精修段落。" * 30, user_edited=True))
    store.save_chapter(_ch(2, "AI 原文未改。" * 30))
    writer2 = ChapterWriter(store.load_config(), None, store, Memory(store, 5))
    out = writer2._personal_anchor()
    assert "用户精修段落" in out
    assert "AI 原文未改" not in out
    # 懒缓存：再调返回相同内容
    assert writer2._personal_anchor() == out


@pytest.mark.p1
def test_chapter_prompt_skips_empty_anchor():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = chapter_prompt(cfg, 1, "第一章", "目标", "调研", "记忆", "上一章尾")
    assert TEARDOWN_ANCHOR_HEADER not in sys_p


@pytest.mark.p1
def test_expand_chapter_prompt_injects_all_anchors():
    cfg = NovelConfig(title="测试", total_words=10000)
    cfg.style_role_reference = "风格角色范文文本"
    sys_p, _ = expand_chapter_prompt(
        cfg, 1, "第一章", "正文", 3000,
        personal_anchor="个人旧稿片段",
        teardown_anchor="题材锚文本",
    )
    # 补上了此前缺失的风格范文锚
    assert "【参考文风样例" in sys_p
    assert "风格角色范文文本" in sys_p
    assert PERSONAL_ANCHOR_HEADER in sys_p
    assert "个人旧稿片段" in sys_p
    assert TEARDOWN_ANCHOR_HEADER in sys_p
    assert "题材锚文本" in sys_p


@pytest.mark.p1
def test_expand_chapter_prompt_skips_missing_anchors():
    cfg = NovelConfig(title="测试", total_words=10000)
    sys_p, _ = expand_chapter_prompt(cfg, 1, "第一章", "正文", 3000)
    assert PERSONAL_ANCHOR_HEADER not in sys_p
    assert TEARDOWN_ANCHOR_HEADER not in sys_p
    assert "【参考文风样例" not in sys_p  # style_role_reference 为空


# ---------- webapp._inject_teardown_style_anchor ----------

def _fake_db(monkeypatch, items=None):
    fake = MagicMock()
    fake.list_teardowns.return_value = {"items": items or []}
    fake.get_teardown.side_effect = lambda tid: ({ "id": tid, "content": None } if tid else None)
    monkeypatch.setattr(core.db, "list_teardowns", fake.list_teardowns)
    monkeypatch.setattr(core.db, "get_teardown", fake.get_teardown)
    return fake


@pytest.mark.p1
def test_inject_writes_anchor_file(store, monkeypatch):
    fake = MagicMock()
    fake.list_teardowns.return_value = {"items": [{"id": 1}, {"id": 2}]}
    fake.get_teardown.side_effect = lambda tid: {
        1: {"id": 1, "content": {
            "styles": ["短句快节奏", "口语化吐槽"],
            "takeaways": ["章末必断章"],
            "excerpts": ["原文片段引用" * 40],
        }},
        2: {"id": 2, "content": "{}"},
    }[tid]
    monkeypatch.setattr(core.db, "list_teardowns", fake.list_teardowns)
    monkeypatch.setattr(core.db, "get_teardown", fake.get_teardown)

    cfg = store.load_config()
    core._inject_teardown_style_anchor(store, cfg)
    out = store.load_teardown_style_anchor()
    assert "短句快节奏" in out
    assert "章末必断章" in out
    assert "原文片段引用" in out


@pytest.mark.p1
def test_inject_flag_off_writes_empty(store, monkeypatch):
    fake = _fake_db(monkeypatch, items=[{"id": 1}])
    cfg = store.load_config()
    cfg.teardown_style_anchor = False
    core._inject_teardown_style_anchor(store, cfg)
    assert store.load_teardown_style_anchor() == ""
    fake.list_teardowns.assert_not_called()


@pytest.mark.p1
def test_inject_no_items_writes_empty(store, monkeypatch):
    _fake_db(monkeypatch, items=[])
    cfg = store.load_config()
    core._inject_teardown_style_anchor(store, cfg)
    assert store.load_teardown_style_anchor() == ""


@pytest.mark.p1
def test_inject_db_error_silent(store, monkeypatch):
    fake = MagicMock()
    fake.list_teardowns.side_effect = RuntimeError("db down")
    monkeypatch.setattr(core.db, "list_teardowns", fake.list_teardowns)
    cfg = store.load_config()
    # 异常静默降级，不抛
    core._inject_teardown_style_anchor(store, cfg)
    assert store.load_teardown_style_anchor() == ""


@pytest.mark.p1
def test_templates_module_has_expand_prompt():
    """保证测试引用的模板函数都存在（防止改名后测试假绿）。"""
    assert hasattr(templates, "scene_prompt")
    assert hasattr(templates, "chapter_prompt")
    assert hasattr(templates, "expand_chapter_prompt")
