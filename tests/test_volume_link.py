# -*- coding: utf-8 -*-
"""分卷-章节关联完整修复测试（任务一）。

覆盖：
1. assign_volumes：按 volumes.start_ch~end_ch 区间确定性回填 volume_no
   （正常/重叠/留空补全/超界/尊重手动/无 volumes）
2. save_outline 保存链路：OutlineSaveReq 接收 volume_no 与 volumes，落盘后完整保留
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline.stages import assign_volumes
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import ChapterPlan, Outline, Volume


def _mk_chapters(n: int) -> list[ChapterPlan]:
    return [ChapterPlan(no=i, title=f"第{i}章", goal=f"目标{i}") for i in range(1, n + 1)]


def _mk_volumes() -> list[Volume]:
    return [Volume(no=1, name="卷一", start_ch=1, end_ch=4),
            Volume(no=2, name="卷二", start_ch=5, end_ch=8)]


@pytest.mark.p1
def test_assign_normal():
    """正常区间：章节按分卷 start_ch~end_ch 正确落位。"""
    out = Outline(chapters=_mk_chapters(8), volumes=_mk_volumes())
    assign_volumes(out, 8)
    assert [c.volume_no for c in out.chapters] == [1, 1, 1, 1, 2, 2, 2, 2]


@pytest.mark.p1
def test_assign_overlap_takes_first():
    """重叠区间：取 no 较小者，不交叉。"""
    vols = [Volume(no=1, start_ch=1, end_ch=5), Volume(no=2, start_ch=4, end_ch=8)]
    out = Outline(chapters=_mk_chapters(8), volumes=vols)
    assign_volumes(out, 8)
    assert [c.volume_no for c in out.chapters] == [1, 1, 1, 1, 1, 2, 2, 2]


@pytest.mark.p1
def test_assign_gaps_filled_nearest():
    """区间外（首卷前/卷间空档）就近归卷，不留未分卷。"""
    vols = [Volume(no=1, start_ch=3, end_ch=5), Volume(no=2, start_ch=7, end_ch=8)]
    out = Outline(chapters=_mk_chapters(8), volumes=vols)
    assign_volumes(out, 8)
    # 1-2→卷1(首卷前)，3-5→卷1，6→卷1(空档归前卷)，7-8→卷2
    assert [c.volume_no for c in out.chapters] == [1, 1, 1, 1, 1, 1, 2, 2]


@pytest.mark.p1
def test_assign_out_of_range_truncated():
    """分卷区间超出目标章数时按 target 截断。"""
    vols = [Volume(no=1, start_ch=0, end_ch=20), Volume(no=2, start_ch=21, end_ch=30)]
    out = Outline(chapters=_mk_chapters(10), volumes=vols)
    assign_volumes(out, 10)
    assert [c.volume_no for c in out.chapters] == [1] * 10


@pytest.mark.p1
def test_assign_respect_manual():
    """已手动设置（volume_no != 0）的章节不被回填覆盖。"""
    out = Outline(chapters=_mk_chapters(8), volumes=_mk_volumes())
    out.chapters[3].volume_no = 2  # 第4章手动移到卷2
    assign_volumes(out, 8)
    assert out.chapters[3].volume_no == 2
    assert out.chapters[4].volume_no == 2  # 第5章本就在卷2区间


@pytest.mark.p2
def test_assign_no_volumes_unchanged():
    """无 volumes 时不处理，保持默认 0。"""
    out = Outline(chapters=_mk_chapters(8))
    assign_volumes(out, 8)
    assert all(c.volume_no == 0 for c in out.chapters)


@pytest.mark.p1
def test_save_outline_persists_volumes_and_volume_no(tmp_path):
    """store.save_outline 落盘后，volumes 与章节 volume_no 完整保留。"""
    cfg = NovelConfig(title="测试", total_words=10000)
    store = ProjectStore.create(tmp_path / "p", cfg)
    out = Outline(
        title="测试",
        chapters=_mk_chapters(6),
        volumes=[Volume(no=1, name="卷一", start_ch=1, end_ch=3),
                 Volume(no=2, name="卷二", start_ch=4, end_ch=6)],
    )
    out.chapters[1].volume_no = 2  # 手动把第2章移到卷2
    assign_volumes(out, 6)         # 其余章节按区间回填
    store.save_outline(out)
    loaded = store.load_outline()
    assert [v.no for v in loaded.volumes] == [1, 2]
    assert [c.volume_no for c in loaded.chapters] == [1, 2, 1, 2, 2, 2]


@pytest.mark.p1
def test_outline_save_req_accepts_volume_no_and_volumes():
    """OutlineSaveReq 能接收 volume_no 与 volumes（拖拽保存链路反序列化）。"""
    from webapp.core import OutlineSaveReq
    req = OutlineSaveReq(
        title="测试",
        chapters=[{"title": "第一章", "goal": "g", "volume_no": 2}],
        volumes=[{"no": 1, "name": "卷一", "start_ch": 1, "end_ch": 3}],
    )
    assert req.chapters[0].volume_no == 2
    assert req.volumes[0].no == 1
    assert req.volumes[0].name == "卷一"
