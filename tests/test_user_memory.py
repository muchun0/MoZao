# -*- coding: utf-8 -*-
"""用户级记忆库（跨书创作偏好继承）专项测试（P1，docs/TECH-用户级记忆库.md）。

覆盖：
- 显式输入沉淀：题材/避雷/节奏入账、同项目幂等
- 编辑信号沉淀：文风锚去重与上限淘汰
- 书级→用户级蒸馏：全量重算幂等、跨书纪律上卷（≥2 本才进注入）、
  单书不污染、贡献更新后旧贡献正确移除
- 继承快照：题材匹配过滤、Top-N 裁剪、空账本 None、uid 归属
- novel_gen 读侧注入：注入块组装、uid 非法/开关关闭零注入
- 隔离：uid 目录分区互不可见；clear/summary 兜底
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.store.project import ProjectStore
from webapp.user_memory import UserMemoryService


@pytest.fixture
def env(tmp_path, monkeypatch):
    """数据根指向临时目录，避免污染真实 data/（DATA_DIR 每次调用实时读取）。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _cfg(**kw):
    kw.setdefault("title", "测试书")
    kw.setdefault("chapter_words", 2500)
    kw.setdefault("total_words", 10000)
    return NovelConfig(**kw)


def _write_book_memory(pid: str, pitfalls: list[dict], chapters=None):
    """直接写 projects/<pid>/book_memory.json（distill 只读该文件，够用）。"""
    from novel_gen.utils.paths import get_projects_dir
    d = get_projects_dir() / pid
    d.mkdir(parents=True, exist_ok=True)
    taste = {"chapters": chapters or []}
    with open(d / "book_memory.json", "w", encoding="utf-8") as f:
        json.dump({"pitfalls": pitfalls, "taste": taste}, f, ensure_ascii=False)
    return d


# ---------- 写入①：显式项目输入 → 偏好 ----------
@pytest.mark.p1
def test_signals_persist_and_idempotent(env):
    cfg = _cfg(genre="玄幻, 都市", pacing_density="high",
               forbidden=["重生开局", "系统流"])
    svc = UserMemoryService(7)
    svc.record_project_signals("p1", cfg)
    svc.record_project_signals("p1", cfg)          # 同项目幂等：不重复计数
    data = svc.load()
    pref = data["preferences"]
    assert {g["name"] for g in pref["genres"]} == {"玄幻", "都市"}
    assert all(g["count"] == 1 for g in pref["genres"])       # 幂等：只计 1 次
    assert [p["name"] for p in pref["pacing"]] == ["密集"]
    assert {a["desc"] for a in pref["avoid"]} == {"重生开局", "系统流"}
    assert all(a["source"] == "forbidden" for a in pref["avoid"])
    assert data["stats"]["project_signals"] == ["p1"]
    assert (env / "data" / "users" / "7" / "user_memory.json").exists()


# ---------- 写入②：人工编辑信号 → 文风锚 ----------
@pytest.mark.p1
def test_edits_dedupe_and_cap(env):
    svc = UserMemoryService(8)
    svc.record_edits("p1", 1, ["锚A", "锚B"])
    svc.record_edits("p1", 1, ["锚A"])              # 同书同章同文本去重
    anchors = svc.load()["style_anchors"]
    assert len(anchors) == 2
    # 上限：超 30 条时按插入顺序淘汰最旧，保留最近 30
    for i in range(40):
        svc.record_edits("p2", 1, [f"锚{i}"])
    anchors = svc.load()["style_anchors"]
    assert len(anchors) == 30
    assert anchors[0]["text"] == "锚10"             # 旧的被淘汰（2 旧锚 + 40 新锚 → 保留末 30）


# ---------- 写入③：书级 → 用户级蒸馏 ----------
@pytest.mark.p1
def test_distill_idempotent_and_cross_book_escalation(env):
    _write_book_memory("p1", [{"pattern": "机制数字-蚀化跨章口径冲突",
                               "count": 3, "fix": "以蚀晶账本为准"}])
    _write_book_memory("p2", [{"pattern": "机制数字-蚀化跨章口径冲突",
                               "count": 2, "fix": "以蚀晶账本为准"}])
    svc = UserMemoryService(9)
    assert svc.distill("p1") == 1
    assert svc.distill("p1") == 0                   # 幂等：同贡献跳过
    assert svc.distill("p2") == 1
    dis = svc.load()["disciplines"]
    assert len(dis) == 1
    d = dis[0]
    assert d["books"] == 2 and set(d["book_ids"]) == {"p1", "p2"}
    assert d["count"] == 5
    # 跨书确认（≥2 本）的纪律才进注入快照
    snap = svc.build_snapshot(_cfg(genre="玄幻"))
    assert snap and any(x["pattern"] == d["pattern"] for x in snap["disciplines"])
    assert snap["uid"] == 9


@pytest.mark.p1
def test_distill_single_book_not_injected(env):
    _write_book_memory("p1", [{"pattern": "节奏-前五章无小高潮",
                               "count": 4, "fix": "每 3 章一个小钩子"}])
    svc = UserMemoryService(10)
    assert svc.distill("p1") == 1
    snap = svc.build_snapshot(_cfg(genre="玄幻"))
    assert snap is None or not snap["disciplines"]   # 单书一次不污染新书
    assert len(svc.load()["disciplines"]) == 1       # 账本仍保留（跨书确认用）


@pytest.mark.p1
def test_distill_updates_remove_old_contrib(env):
    _write_book_memory("p1", [{"pattern": "机制数字-蚀化跨章口径冲突",
                               "count": 3, "fix": "以蚀晶账本为准"}])
    svc = UserMemoryService(11)
    assert svc.distill("p1") == 1
    # 书更新：旧 pattern 消失，换成新 pattern
    _write_book_memory("p1", [{"pattern": "人物-配角战力跳变",
                               "count": 1, "fix": "配角实力提前铺垫"}])
    assert svc.distill("p1") > 0
    dis = svc.load()["disciplines"]
    patterns = {d["pattern"] for d in dis}
    assert "机制数字-蚀化跨章口径冲突" not in patterns   # 旧贡献被移除
    assert "人物-配角战力跳变" in patterns


@pytest.mark.p1
def test_distill_stats(env):
    _write_book_memory("p1", [{"pattern": "A", "count": 2, "fix": "f"}],
                       chapters=[{"score": 80}, {"score": 90}])
    _write_book_memory("p2", [{"pattern": "A", "count": 1, "fix": "f"},
                              {"pattern": "B", "count": 3, "fix": "g"}],
                       chapters=[{"score": 70}])
    svc = UserMemoryService(12)
    svc.distill("p1")
    svc.distill("p2")
    st = svc.load()["stats"]
    assert st["books"] == 2
    assert st["avg_score"] == 77.5                  # (85+70)/2 按 _recompute_stats 现算
    assert set(st["top_patterns"][:2]) == {"A", "B"}  # A/B 均 3 次并列最高


# ---------- 继承快照 ----------
@pytest.mark.p1
def test_build_snapshot_genre_filter_and_topn(env):
    _write_book_memory("p1", [{"pattern": "机制数字-蚀化跨章口径冲突",
                               "count": 3, "fix": "以蚀晶账本为准"}])
    _write_book_memory("p2", [{"pattern": "机制数字-蚀化跨章口径冲突",
                               "count": 2, "fix": "以蚀晶账本为准"}])
    svc = UserMemoryService(13)
    svc.record_project_signals("p1", _cfg(genre="玄幻"))
    svc.record_project_signals("p2", _cfg(genre="都市"))
    svc.distill("p1")
    svc.distill("p2")
    # 本书题材=都市：只注入匹配的题材偏好；避雷/节奏/纪律仍注入
    snap = svc.build_snapshot(_cfg(genre="都市"))
    assert snap and "都市" in snap["preferences"]
    assert "玄幻" not in snap["preferences"]
    assert len(snap["disciplines"]) == 1
    # 本书题材=玄幻：只注入玄幻偏好
    snap2 = svc.build_snapshot(_cfg(genre="玄幻"))
    assert "玄幻" in snap2["preferences"] and "都市" not in snap2["preferences"]


@pytest.mark.p1
def test_build_snapshot_empty_none(env):
    assert UserMemoryService(14).build_snapshot(_cfg()) is None


@pytest.mark.p1
def test_build_snapshot_topn_cap(env):
    svc = UserMemoryService(15)
    for i in range(7):
        _write_book_memory(f"p{i}", [{"pattern": f"纪律{i}", "count": 3, "fix": f"修复{i}"}])
        svc.distill(f"p{i}")
    # 制造跨书：p0 与 p1..p6 各配对一次，让 6 条纪律达到 books>=2
    for i in range(1, 7):
        _write_book_memory(f"px{i}", [{"pattern": f"纪律{i}", "count": 1, "fix": f"修复{i}"}])
        svc.distill(f"px{i}")
    cfg = _cfg(genre="玄幻")
    snap = svc.build_snapshot(cfg)
    assert snap and len(snap["disciplines"]) == cfg.user_memory_inject_top   # Top-N=5


# ---------- novel_gen 读侧注入 ----------
@pytest.mark.p1
def test_inject_block_writer_side(env):
    store = ProjectStore.create(env / "data" / "projects", _cfg(genre="玄幻"))
    snap = {
        "uid": 16,
        "disciplines": [{"pattern": "机制数字-蚀化跨章口径冲突",
                         "rule": "以蚀晶账本为准", "books": 2, "book_ids": ["p1", "p2"]}],
        "style_anchors": [{"book": "p1", "ch": 3, "text": "他数了一遍手指。", "length": 10}],
    }
    store.save_user_memory(snap)
    writer = ChapterWriter(store.load_config(), MagicMock(), store, Memory(store, 5))
    block = writer._user_memory_block()
    assert "用户跨书纪律" in block
    assert "以蚀晶账本为准" in block and "跨 2 本书" in block
    assert "用户个人文风锚" in block and "他数了一遍手指。" in block


@pytest.mark.p1
def test_inject_block_uid_invalid_skips(env):
    store = ProjectStore.create(env / "data" / "projects", _cfg(genre="玄幻"))
    for bad in ("abc", -1, 0, None):
        store.save_user_memory({"uid": bad,
                                "disciplines": [{"rule": "x", "books": 2}]})
        writer = ChapterWriter(store.load_config(), MagicMock(), store, Memory(store, 5))
        assert writer._user_memory_block() == "", f"uid={bad} 不应注入"


@pytest.mark.p1
def test_inject_block_disabled_flag(env):
    store = ProjectStore.create(env / "data" / "projects", _cfg(genre="玄幻"))
    store.save_user_memory({"uid": 17, "disciplines": [{"rule": "x", "books": 2}]})
    cfg = store.load_config()
    cfg.user_memory = False                          # 开关关闭 → 零注入
    writer = ChapterWriter(cfg, MagicMock(), store, Memory(store, 5))
    assert writer._user_memory_block() == ""


@pytest.mark.p1
def test_inject_block_no_snapshot_skips(env):
    store = ProjectStore.create(env / "data" / "projects", _cfg(genre="玄幻"))
    writer = ChapterWriter(store.load_config(), MagicMock(), store, Memory(store, 5))
    assert writer._user_memory_block() == ""


# ---------- 隔离 / 兜底 ----------
@pytest.mark.p1
def test_user_isolation(env):
    svc1 = UserMemoryService(1)
    svc2 = UserMemoryService(2)
    svc1.record_project_signals("pa", _cfg(genre="玄幻"))
    assert "玄幻" in (svc1.summary()["genres"])
    assert svc2.summary()["genres"] == []           # uid 目录分区，互不可见


@pytest.mark.p1
def test_clear_removes_memory(env):
    svc = UserMemoryService(3)
    svc.record_project_signals("pa", _cfg(genre="玄幻"))
    assert svc.load()["preferences"]["genres"]
    svc.clear()
    assert svc.summary()["genres"] == []            # 清空后偏好归零
