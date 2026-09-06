# -*- coding: utf-8 -*-
"""存储层测试：项目落盘 / 章节读写 / 摘要幂等 / 导出。"""
from pathlib import Path

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter, Outline, ChapterPlan, World, Character, ProjectState


def _make_cfg(**kw) -> NovelConfig:
    base = dict(title="测试书", genre="玄幻", total_words=4000, chapter_words=1000)
    base.update(kw)
    return NovelConfig(**base)


def test_create_and_reload(tmp_path):
    cfg = _make_cfg()
    store = ProjectStore.create(tmp_path, cfg)
    assert store.dir.exists()
    assert store.config_path().exists()
    assert store.load_config().title == "测试书"

    reopened = ProjectStore.open(store.dir)
    assert reopened.load_config().title == "测试书"
    # api_key 不应落盘
    raw = store.config_path().read_text(encoding="utf-8")
    assert "api_key" not in raw


def test_outline_world_roundtrip(tmp_path):
    store = ProjectStore.create(tmp_path, _make_cfg())
    outline = Outline(
        title="测试书",
        intro="简介",
        chapters=[ChapterPlan(no=1, title="第一章", goal="开局"), ChapterPlan(no=2, title="第二章", goal="冲突")],
    )
    store.save_outline(outline)
    loaded = store.load_outline()
    assert loaded is not None
    assert len(loaded.chapters) == 2
    assert loaded.chapters[1].title == "第二章"

    world = World(world_view="世界观", characters=[Character(name="张三", role="主角", desc="坚韧")], foreshadowing=["伏笔"])
    store.save_world(world)
    assert store.load_world().characters[0].name == "张三"


def test_chapter_write_list_and_summary(tmp_path):
    store = ProjectStore.create(tmp_path, _make_cfg())
    store.save_chapter(Chapter(no=1, title="第一章", content="正文内容", summary="摘要一"))
    store.save_chapter(Chapter(no=3, title="第三章", content="正文三", summary="摘要三"))
    store.save_chapter(Chapter(no=2, title="第二章", content="正文二", summary="摘要二"))

    chapters = store.list_chapters()
    assert [c.no for c in chapters] == [1, 2, 3]

    # 摘要幂等：同章节覆盖且不重复
    store.append_summary(2, "摘要二新")
    store.append_summary(2, "摘要二新")  # 重复写
    summaries = store.load_summaries()
    assert summaries == [{"no": 2, "summary": "摘要二新"}]

    store.append_summary(1, "摘要一")
    summaries = store.load_summaries()
    assert [s["no"] for s in summaries] == [1, 2]


def test_state_roundtrip(tmp_path):
    store = ProjectStore.create(tmp_path, _make_cfg())
    state = store.load_state()
    assert state.phase == "outline"
    state.phase = "writing"
    state.last_chapter = 3
    store.save_state(state)
    assert store.load_state().last_chapter == 3


def test_export_txt(tmp_path):
    cfg = _make_cfg()
    store = ProjectStore.create(tmp_path, cfg)
    store.save_outline(Outline(title="测试书", intro="这是简介", chapters=[]))
    store.save_chapter(Chapter(no=1, title="第一章", content="第一章正文内容", summary=""))
    store.save_chapter(Chapter(no=2, title="第二章", content="第二章正文内容", summary=""))

    out = store.export_txt()
    text = out.read_text(encoding="utf-8")
    assert "测试书" in text
    assert "这是简介" in text
    assert "第1章 第一章" in text
    assert "第一章正文内容" in text
    assert "第2章 第二章" in text
