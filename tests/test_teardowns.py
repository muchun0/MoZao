# -*- coding: utf-8 -*-
"""爆款拆书库（book_teardown）测试。

数据层 + 沉淀逻辑（teardown_lib）：
P1：调研沉淀新建/同项目幂等/同题材合并、列表筛选、详情浏览数、删除；
P2：种子幂等、关键词搜索、分页、异常输入（None/空题材不沉淀）。

依赖本机 MySQL（mozao 库）；连接失败时整组跳过（CI 无 DB 场景）。
测试数据以 source_project='td_test_*' 标记，清理时不触碰正式/种子数据。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp import db
from webapp import teardown_lib


def pytest_configure(config):
    config.addinivalue_line("markers", "p1: 重要功能（主流程/关键边界）")
    config.addinivalue_line("markers", "p2: 一般（边缘/工具逻辑）")


def _db_ok() -> bool:
    try:
        with db._connect() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_ok(), reason="本机 MySQL 不可用")


@pytest.fixture
def clean_teardowns():
    """清掉测试期间创建的拆书条目：调研沉淀（source_project=td_test_*）与测试种子（题材=测试种子*）。"""
    yield
    with db._connect() as conn:
        conn.execute("DELETE FROM book_teardown WHERE source_project LIKE 'td_test_%'")
        conn.execute("DELETE FROM book_teardown WHERE genre LIKE '测试种子%'")


class _FakeReport:
    def __init__(self, genre="都市", summary="都市共性：逆袭+感情线，章末强钩子"):
        self.genre = genre
        self.summary = summary
        self.trends = ["赘婿文", "重生复仇"]
        self.plot_patterns = ["开局打脸-中期升级-后期逆袭"]
        self.styles = ["快节奏", "爽文风"]
        self.pacing = "三章一小高潮"
        self.opening_strategy = "第一章必须出现冲突与金手指"
        self.avoid = ["流水账", "无冲突日常"]
        self.references = ["《示例爆款一》", "《示例爆款二》"]


# ---------------- P1：主流程 ----------------

@pytest.mark.p1
def test_sink_creates_new(clean_teardowns):
    tid = teardown_lib.sink_research(_FakeReport(), "td_test_proj_a")
    assert tid is not None
    row = db.get_teardown(tid)
    assert row["genre"] == "都市"
    assert row["source_type"] == "research"
    assert row["source_project"] == "td_test_proj_a"
    assert "逆袭" in row["summary"]
    assert "trends" in row["content"]


@pytest.mark.p1
def test_sink_same_project_idempotent(clean_teardowns):
    r1 = teardown_lib.sink_research(_FakeReport(summary="v1"), "td_test_proj_b")
    r2 = teardown_lib.sink_research(_FakeReport(summary="v2 更新"), "td_test_proj_b")
    assert r1 == r2                       # 同项目 → 更新而非新增
    lst = db.list_teardowns(genre="都市")
    assert lst["total"] == 1
    assert "v2 更新" in db.get_teardown(r1)["summary"]


@pytest.mark.p1
def test_sink_same_genre_merges(clean_teardowns):
    a = teardown_lib.sink_research(_FakeReport(), "td_test_proj_c1")
    b = teardown_lib.sink_research(_FakeReport(), "td_test_proj_c2")
    assert a == b                         # 同题材不同项目 → 合并到同一条
    assert db.list_teardowns(genre="都市")["total"] == 1
    assert db.get_teardown(b)["source_project"] == "td_test_proj_c2"  # 归属最新项目


@pytest.mark.p1
def test_list_filter_by_genre(clean_teardowns):
    teardown_lib.sink_research(_FakeReport(genre="都市"), "td_test_proj_d1")
    teardown_lib.sink_research(_FakeReport(genre="玄幻"), "td_test_proj_d2")
    lst = db.list_teardowns(genre="玄幻")
    assert lst["total"] == 1
    assert lst["items"][0]["genre"] == "玄幻"
    assert "content" not in lst["items"][0]           # 列表不含全文


@pytest.mark.p1
def test_detail_inc_view(clean_teardowns):
    tid = teardown_lib.sink_research(_FakeReport(), "td_test_proj_e")
    assert db.get_teardown(tid, inc_view=True)["view_count"] == 1
    assert db.get_teardown(tid, inc_view=True)["view_count"] == 2
    assert db.get_teardown(tid)["view_count"] == 2    # 不 inc_view 不自增


@pytest.mark.p1
def test_delete(clean_teardowns):
    tid = teardown_lib.sink_research(_FakeReport(), "td_test_proj_f")
    assert db.delete_teardown(tid) is True
    assert db.get_teardown(tid) is None
    assert db.delete_teardown(tid) is False           # 二次删除返回 False


# ---------------- P2：边界 / 工具逻辑 ----------------

@pytest.mark.p2
def test_seed_idempotent(clean_teardowns):
    # 用独立题材测试，避免与真实「通用」种子冲突（种子按题材幂等）
    first = db.seed_teardown("测试种子A", "种子A", "s", "{}")
    second = db.seed_teardown("测试种子A", "种子A2", "s2", "{}")
    assert first is not None
    assert second is None                             # 同题材 seed 幂等
    row = db.get_teardown(first)
    assert row["source_type"] == "seed"
    assert row["title"] == "种子A"                    # 内容未被覆盖


@pytest.mark.p2
def test_search_keyword(clean_teardowns):
    teardown_lib.sink_research(_FakeReport(summary="tdq_kw_金手指逆袭"), "td_test_proj_g")
    # 用不同题材避免同题材合并覆盖上一条
    teardown_lib.sink_research(_FakeReport(genre="玄幻", summary="tdq_kw_无关内容"), "td_test_proj_h")
    # 用测试独有关键词前缀，避免命中库内正式数据（如已沉淀的玄幻调研含"金手指"）
    assert db.list_teardowns(q="tdq_kw_金手指")["total"] == 1
    assert db.list_teardowns(q="tdq_不存在的词")["total"] == 0


@pytest.mark.p2
def test_pagination(clean_teardowns):
    for i in range(3):
        teardown_lib.sink_research(_FakeReport(genre="都市"), f"td_test_proj_p{i}")
    # 同题材合并后只有 1 条；用不同题材构造分页场景
    for g in ["悬疑", "科幻", "历史"]:
        teardown_lib.sink_research(_FakeReport(genre=g), f"td_test_proj_pg_{g}")
    lst = db.list_teardowns(page=1, page_size=2)
    assert lst["total"] >= 4
    assert len(lst["items"]) == 2
    page2 = db.list_teardowns(page=2, page_size=2)
    assert len(page2["items"]) == 2
    ids1 = {i["id"] for i in lst["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not (ids1 & ids2)                          # 两页不重复


@pytest.mark.p2
def test_sink_ignores_none_and_empty_genre(clean_teardowns):
    assert teardown_lib.sink_research(None, "td_test_proj_x") is None
    assert teardown_lib.sink_research(_FakeReport(genre="  "), "td_test_proj_y") is None
    # 测试期间不新增任何条目（与已有种子/正式数据无关）
    with db._connect() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM book_teardown WHERE source_project LIKE 'td_test_%'").fetchone()["c"]
    assert cnt == 0


@pytest.mark.p2
def test_genres_aggregate(clean_teardowns):
    teardown_lib.sink_research(_FakeReport(genre="都市"), "td_test_proj_ga1")
    teardown_lib.sink_research(_FakeReport(genre="都市"), "td_test_proj_ga2")
    teardown_lib.sink_research(_FakeReport(genre="历史"), "td_test_proj_gb")
    genres = db.list_teardown_genres()
    m = {g["genre"]: g["count"] for g in genres}
    assert m.get("都市") == 1                          # 同题材合并
    assert m.get("历史") == 1


# ---------------- 方案 A：expand 模式可选附带调研 ----------------

def _mk_cfg(mode: str = "auto", with_research: bool = False):
    from novel_gen.config import NovelConfig
    return NovelConfig(mode=mode, with_research=with_research)


@pytest.mark.p1
def test_research_needed_auto():
    from webapp.core import _research_needed
    # auto 模式：无大纲必跑；有大纲（复用/续写）不跑
    assert _research_needed(_mk_cfg("auto"), has_outline=False) is True
    assert _research_needed(_mk_cfg("auto"), has_outline=True) is False


@pytest.mark.p1
def test_research_needed_expand_option():
    from webapp.core import _research_needed
    # expand 模式：无大纲必跑；有大纲时由 with_research 开关决定
    assert _research_needed(_mk_cfg("expand"), has_outline=False) is True
    assert _research_needed(_mk_cfg("expand", False), has_outline=True) is False
    assert _research_needed(_mk_cfg("expand", True), has_outline=True) is True


@pytest.mark.p2
def test_with_research_config_roundtrip():
    import dataclasses
    from novel_gen.config import NovelConfig
    # 默认关闭（向后兼容），显式开启可序列化持久化
    assert NovelConfig().with_research is False
    d = dataclasses.asdict(_mk_cfg("expand", True))
    assert d["with_research"] is True
    assert d["mode"] == "expand"


@pytest.mark.p1
def test_with_research_persisted_in_config_file(tmp_path):
    """config.json 落盘往返不丢 with_research（save_config/load_config 白名单字段）。"""
    from novel_gen.store.project import ProjectStore
    store = ProjectStore.create(tmp_path, _mk_cfg("expand", True))
    reloaded = store.load_config()
    assert reloaded.with_research is True
    assert reloaded.mode == "expand"
    # 旧配置（无该字段）兼容：默认 False
    store2 = ProjectStore.create(tmp_path / "legacy", _mk_cfg("expand", False))
    import json
    data = json.loads((store2.dir / "config.json").read_text(encoding="utf-8"))
    data.pop("with_research", None)   # 模拟历史项目
    (store2.dir / "config.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert store2.load_config().with_research is False
