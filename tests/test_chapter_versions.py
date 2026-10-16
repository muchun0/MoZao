# -*- coding: utf-8 -*-
"""章节版本化（REQ-章节版本化）专项测试。

覆盖（按 REQ §5 验收标准）：
- P0：内容不同追加版本不覆盖、内容相同幂等不追加、select 回退后 load_chapter 返回该版本
      且 selected 更新、版本上限淘汰最旧且 HEAD 不淘汰
- P1：list_versions 字段完整、source=user 人工编辑版本
- P2：存量单版本章节向后兼容（首次保存建版本库保留旧版为 legacy）、select 不存在版本返回 None

注意：纯 store 操作，不依赖真实 LLM；占位正文避开节拍词（§11.8）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                 # noqa: E402
from novel_gen.store.project import ProjectStore          # noqa: E402
from novel_gen.store.schemas import Chapter               # noqa: E402


# ---------- fixtures ----------
@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _ch(no: int, content: str, source: str = "ai") -> Chapter:
    return Chapter(no=no, title=f"第{no}章", content=content, summary="摘要", source=source)


# ---------- P0 ----------
@pytest.mark.p0
def test_append_version_not_overwrite(store):
    store.save_chapter(_ch(1, "v1 正文"))
    store.save_chapter(_ch(1, "v2 正文"))
    v = store.list_versions(1)
    assert [x["v"] for x in v] == [1, 2]
    # 最新为当前选中
    assert v[-1]["selected"] is True
    assert v[0]["selected"] is False
    # 旧版文件仍保留，可单独加载（不覆盖）
    assert store.load_chapter_version(1, 1).content == "v1 正文"
    assert store.load_chapter(1).content == "v2 正文"


@pytest.mark.p0
def test_idempotent_same_content(store):
    store.save_chapter(_ch(1, "正文"))
    r = store.save_chapter(_ch(1, "正文"))  # 内容与 HEAD 相同
    assert r is None
    assert len(store.list_versions(1)) == 1


@pytest.mark.p0
def test_select_version_rollback(store):
    store.save_chapter(_ch(1, "v1 正文"))
    store.save_chapter(_ch(1, "v2 正文"))
    ch = store.select_version(1, 1)
    assert ch is not None and ch.content == "v1 正文"
    assert store.load_chapter(1).content == "v1 正文"  # HEAD 已切换
    v = store.list_versions(1)
    assert v[0]["selected"] is True
    assert v[1]["selected"] is False


@pytest.mark.p0
def test_version_limit_evicts_oldest(store):
    # 构造超过默认上限（15）的版本，验证淘汰最旧、HEAD 不淘汰
    for i in range(1, 18):
        store.save_chapter(_ch(1, f"正文{i:03d}"))
    v = store.list_versions(1)
    assert len(v) == 15
    # 最新版本保留且为 selected，最旧 1、2 被淘汰
    assert v[-1]["v"] == 17
    assert v[-1]["selected"] is True
    assert v[0]["v"] == 3
    assert store.load_chapter_version(1, 1) is None  # 已淘汰文件删除
    assert store.load_chapter(1).content == "正文017"  # HEAD 不淘汰


# ---------- P1 ----------
@pytest.mark.p1
def test_list_versions_fields(store):
    store.save_chapter(_ch(1, "v1 正文"))
    store.save_chapter(_ch(1, "v2 正文"), source="rewrite")
    v = store.list_versions(1)
    assert len(v) == 2
    assert v[0]["source"] == "ai"
    assert v[1]["source"] == "rewrite"
    assert v[0]["words"] == len("v1 正文")
    assert v[0]["ts"] is not None


@pytest.mark.p1
def test_edit_source_user(store):
    store.save_chapter(_ch(1, "v1 正文"))
    store.save_chapter(_ch(1, "人工修订"), source="user")
    v = store.list_versions(1)
    assert v[-1]["source"] == "user"
    assert v[-1]["selected"] is True


# ---------- P2 ----------
@pytest.mark.p2
def test_legacy_compat_first_version(store):
    # 模拟存量单版本项目：直接写 HEAD（无版本索引），再保存新内容
    store._write_json(store.chapter_path(1), _ch(1, "旧版正文").to_dict())
    store.save_chapter(_ch(1, "新版正文"))
    v = store.list_versions(1)
    # 旧 HEAD 存档为 v1(legacy)，新内容为 v2 并选中
    assert [x["v"] for x in v] == [1, 2]
    assert v[0]["source"] == "legacy"
    assert v[-1]["selected"] is True
    assert store.load_chapter_version(1, 1).content == "旧版正文"
    assert store.load_chapter(1).content == "新版正文"


@pytest.mark.p2
def test_select_nonexistent_version_returns_none(store):
    store.save_chapter(_ch(1, "v1 正文"))
    assert store.select_version(1, 99) is None
    assert store.load_chapter(1).content == "v1 正文"  # 不受影响
