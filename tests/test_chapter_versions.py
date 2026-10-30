# -*- coding: utf-8 -*-
"""章节版本化（REQ-章节版本化）专项测试。

覆盖（按 REQ §5 验收标准）：
- P0：内容不同追加版本不覆盖、内容相同幂等不追加、select 选择旧版后 load_chapter 返回该版本
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
def test_select_version(store):
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


# ---------- 版本质量 / 候选版本 / 自动选分 / 删除（重写版本落库闭环） ----------
@pytest.mark.p0
def test_save_candidate_version_does_not_promote(store):
    """promote=False：新稿只追加为候选版本，不顶替当前选中（HEAD/selected 不变）。"""
    store.save_chapter(_ch(1, "v1 正文"))
    r = store.save_chapter(_ch(1, "重写稿"), source="rewrite", promote=False)
    assert r == 2
    # HEAD 仍为 v1，selected 仍为 v1
    assert store.load_chapter(1).content == "v1 正文"
    v = store.list_versions(1)
    assert v[0]["selected"] is True
    assert v[1]["selected"] is False
    assert v[1]["source"] == "rewrite"
    # 候选版本可单独加载（用户可查看/选择）
    assert store.load_chapter_version(1, 2).content == "重写稿"


@pytest.mark.p0
def test_quality_recorded_and_returned(store):
    """quality 写入该版本 history，list_versions 透传（版本弹窗改稿建议数据源）。"""
    q = {"passed": False, "score": 42, "issue_count": 3,
         "auto_revised": True,
         "issues": [{"level": "大改", "type": "结构-字数严重不足", "detail": "不足 30%"}]}
    store.save_chapter(_ch(1, "v1 正文"), quality={"passed": True, "score": 90, "issue_count": 0,
                                                  "auto_revised": False, "issues": []})
    store.save_chapter(_ch(1, "重写稿"), source="rewrite", quality=q)
    v = store.list_versions(1)
    assert v[0]["quality"]["score"] == 90
    assert v[0]["quality"]["passed"] is True
    assert v[1]["quality"]["score"] == 42
    assert v[1]["quality"]["issue_count"] == 3
    assert v[1]["quality"]["issues"][0]["detail"] == "不足 30%"


@pytest.mark.p1
def test_auto_select_best_on_generation(store):
    """未被用户管理时：保存后自动把质检评分最高的版本设为当前选中（首次生成展示最好的一版）。"""
    store.save_chapter(_ch(1, "初稿低分"), quality={"passed": False, "score": 40, "issue_count": 2,
                                                   "auto_revised": False, "issues": []})
    store.save_chapter(_ch(1, "重写高分"), source="rewrite", quality={"passed": True, "score": 92,
                                                                     "issue_count": 0,
                                                                     "auto_revised": True, "issues": []})
    v = store.list_versions(1)
    # 选中应切到评分最高的 v2（92 > 40），HEAD 也同步
    assert v[1]["selected"] is True
    assert store.load_chapter(1).content == "重写高分"


@pytest.mark.p1
def test_user_managed_stops_auto_select(store):
    """用户手动选择后置 user_managed：再生成时不自动按评分切走用户所选版本。"""
    store.save_chapter(_ch(1, "初稿低分"), quality={"passed": False, "score": 40, "issue_count": 2,
                                                   "auto_revised": False, "issues": []})
    store.save_chapter(_ch(1, "重写高分"), source="rewrite", quality={"passed": True, "score": 92,
                                                                     "issue_count": 0,
                                                                     "auto_revised": True, "issues": []})
    # 用户手动选择 v1（比当前旧）→ 置 user_managed
    store.select_version(1, 1)
    assert store.load_chapter(1).content == "初稿低分"
    # 再次生成更低分版本：user_managed 下不自动选分，HEAD 保持本轮新稿
    # （若无 user_managed，自动选分会切回 92 分的 v2）
    store.save_chapter(_ch(1, "再次重写"), source="rewrite", quality={"passed": False, "score": 50,
                                                                    "issue_count": 1,
                                                                    "auto_revised": True, "issues": []})
    v = store.list_versions(1)
    assert v[-1]["selected"] is True  # 本轮新稿成为 HEAD，不被自动选分干扰
    assert store.load_chapter(1).content == "再次重写"
    # 用户之前选择的 v1 仍保留在版本库中可再次选择
    assert [x["v"] for x in v] == [1, 2, 3]


@pytest.mark.p1
def test_user_edit_marks_managed(store):
    """source=user 人工编辑保存 → 置 user_managed，后续自动生成不自动选分。"""
    store.save_chapter(_ch(1, "v1 正文"), quality={"passed": True, "score": 80, "issue_count": 0,
                                                  "auto_revised": False, "issues": []})
    store.save_chapter(_ch(1, "人工修订"), source="user")
    # 再生成 95 分新稿：若无 user_managed 会自动切到 v1(80)→ 有 user_managed 则新稿成为 HEAD
    store.save_chapter(_ch(1, "新ai稿"), quality={"passed": True, "score": 95, "issue_count": 0,
                                                 "auto_revised": False, "issues": []})
    v = store.list_versions(1)
    assert v[-1]["selected"] is True  # 新稿成为 HEAD
    assert v[-1]["source"] == "ai"


@pytest.mark.p2
def test_delete_version(store):
    """删除历史版本：当前选中（selected/HEAD）不可删，其余删除后索引与文件移除。"""
    store.save_chapter(_ch(1, "v1 正文"))
    store.save_chapter(_ch(1, "v2 正文"))
    store.save_chapter(_ch(1, "v3 正文"))
    # 删除非选中 v1
    assert store.delete_version(1, 1) is True
    assert store.load_chapter_version(1, 1) is None
    v = store.list_versions(1)
    assert [x["v"] for x in v] == [2, 3]
    # 删除当前选中 v3 被拒绝
    assert store.delete_version(1, 3) is False
    assert store.load_chapter(1).content == "v3 正文"
    # 删除不存在的版本返回 False
    assert store.delete_version(1, 99) is False


@pytest.mark.p2
def test_auto_select_best_skips_legacy_without_quality(store):
    """存量 legacy 版本无 quality.score → 不参与自动选分，避免误切（老项目重写安全）。"""
    store._write_json(store.chapter_path(1), _ch(1, "旧版正文").to_dict())
    store.save_chapter(_ch(1, "新稿"), source="rewrite", quality={"passed": True, "score": 85,
                                                                 "issue_count": 0,
                                                                 "auto_revised": True, "issues": []})
    v = store.list_versions(1)
    # v1 legacy 无 quality，自动选分只在有 score 的版本中选 → v2 选中
    assert v[-1]["selected"] is True
    assert v[-1]["source"] == "rewrite"
