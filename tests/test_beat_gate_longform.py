# -*- coding: utf-8 -*-
"""REQ-超长篇生成攻坚 S3.2/S3.1 测试（2026-10-18）。

覆盖：
- S3.2 loose 匹配：标点/措辞差异仍命中（去标点归一）
- S3.2 章型白名单：对话章（引号占比高）→ skip_chapter_type，0% 不再误判 fail
- S3.2 节点分级：P2 不计入覆盖率分母（分母只算 P0+P1）
- S3.1 承诺台账：build_commitment_ledger 四类清单（committed/missed/planted/must_carry）
- S3.1 补偿块：有漏兑现生成补偿块，无漏兑现空串（零 token）
- S3.1 store 落盘：pending_commitments 按章幂等 + prev 读取
- 向后兼容：loose=False 回退精确子串匹配；未提供优先级默认全 P1
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.beat_gate import (
    _split_plan_beats, _slot_hit, compute_beat_coverage, detect_chapter_type,
)
from novel_gen.pipeline.pending_commitments import (
    build_commitment_ledger, compensation_block,
)

_MOVES = "主角在墟口遭遇灰卒被咬死→复活后用铁锤砸碎灰卒核心→抱着小九回到铁匠铺"


# ---------- S3.2 loose 匹配 ----------
@pytest.mark.p1
def test_loose_match_punctuation_gap():
    """S3.2 loose：标点隔开同义词仍命中（"检查，面板"），精确匹配 miss。"""
    content = "他抬起手，检查，面板一眼。"
    assert _slot_hit("检查面板", content, loose=True)      # 去标点归一后命中
    assert not _slot_hit("检查面板", content, loose=False)  # 精确被逗号隔开 miss


@pytest.mark.p1
def test_loose_match_rewrite_still_hit():
    """S3.2 loose：近义改写 + 标点差异仍命中（清晨→天刚蒙蒙亮）。"""
    content = "天刚蒙蒙亮，便出门了。"
    assert _slot_hit("清晨", content, loose=True)


# ---------- S3.2 章型白名单 ----------
@pytest.mark.p1
def test_detect_chapter_type_dialogue():
    dialogue = "“你来了。”他说。\n“嗯，我到了。”她轻声说。\n“那走吧。”他道。\n“好。”" * 10
    assert detect_chapter_type(dialogue) == "对话"
    # 短章（<50 字）判过渡；足长剧情句判剧情
    assert detect_chapter_type("主角在墟口拾荒，遭遇灰卒，咬牙挥锤砸碎核心。", _MOVES) == "过渡"
    plot = ("主角在墟口拾荒，遭遇灰卒扑上来撕咬，他咬牙挥起铁锤狠狠砸碎灰卒的核心，"
            "随后抱起小九转身返回铁匠铺，心里盘算着明天的药钱该怎么凑。")
    assert detect_chapter_type(plot, _MOVES) == "剧情"


@pytest.mark.p1
def test_chapter_type_whitelist_skips_dialogue():
    """S3.2 章型白名单：对话章即使 beat 覆盖为 0 也不 fail（治误伤）。"""
    dialogue = "“你来了。”他说。\n“嗯，我到了。”她轻声说。\n“那走吧。”他道。\n“好。”" * 10
    r = compute_beat_coverage(dialogue, _MOVES, chapter_type="auto")
    assert r["verdict"] == "skip_chapter_type"
    assert r["coverage"] is None
    assert r["chapter_type"] == "对话"


# ---------- S3.2 节点分级 ----------
@pytest.mark.p1
def test_priority_p2_excluded_from_denominator():
    """S3.2 节点分级：P2 氛围节点不计分母，分母只算 P0+P1。"""
    beats = _split_plan_beats(_MOVES)
    priorities = {b: (2 if i == 2 else 1) for i, b in enumerate(beats)}
    # 正文覆盖前两拍（P0/P1），漏第三拍（P2）→ 分母=2，命中 2/2=pass
    content = "主角在墟口遭遇灰卒被咬死。复活后他找到铁锤，狠狠砸碎灰卒核心。"
    r = compute_beat_coverage(content, _MOVES, priorities=priorities)
    assert r["coverage"] == 1.0
    assert r["verdict"] == "pass"


@pytest.mark.p1
def test_all_p2_yields_skip_priority():
    beats = _split_plan_beats(_MOVES)
    priorities = {b: 2 for b in beats}
    r = compute_beat_coverage("任意正文", _MOVES, priorities=priorities)
    assert r["verdict"] == "skip_priority"


# ---------- S3.1 承诺台账 ----------
@pytest.mark.p1
def test_build_commitment_ledger():
    content = "主角在墟口遭遇灰卒被咬死。复活后他砸碎灰卒核心，抱着小九回铁匠铺。"
    bg = compute_beat_coverage(content, _MOVES)
    ledger = build_commitment_ledger(bg, 2, new_planted=["面板数字异常"])
    assert ledger["chapter_no"] == 2
    assert ledger["committed"]
    assert ledger["missed"] == ledger["must_carry_to_next"]
    assert "面板数字异常" in ledger["planted"]
    assert "verdict" in ledger and "coverage" in ledger


@pytest.mark.p1
def test_compensation_block():
    assert compensation_block(None) == ""
    assert compensation_block({"missed": []}) == ""
    assert compensation_block({"must_carry_to_next": []}) == ""
    blk = compensation_block({"missed": ["主角逃出灰雾", "见血洞"]})
    assert "上章未兑现节点补偿" in blk
    assert "主角逃出灰雾" in blk


@pytest.mark.p1
def test_store_pending_commitments_roundtrip(tmp_path):
    from novel_gen.config import NovelConfig
    from novel_gen.store.project import ProjectStore
    cfg = NovelConfig(title="台账书", chapter_words=2000, total_words=6000)
    store = ProjectStore.create(tmp_path / "p", cfg)
    ledger = {"chapter_no": 1, "committed": ["a"], "missed": ["b"],
              "planted": [], "must_carry_to_next": ["b"]}
    store.save_pending_commitments(1, ledger)
    # 按章幂等覆盖
    store.save_pending_commitments(1, dict(ledger, missed=["c"]))
    assert store.prev_pending_commitments(2)["missed"] == ["c"]
    assert store.prev_pending_commitments(1) is None


# ---------- 向后兼容 ----------
@pytest.mark.p1
def test_backward_compat_defaults():
    """不传新参数（默认剧情章+全 P1+loose）行为与原版一致：有 planned/verdict。"""
    content = "主角在墟口拾荒，遭遇灰卒被咬死。"
    r = compute_beat_coverage(content, _MOVES)
    assert r["chapter_type"] == "剧情"
    assert r["verdict"] in ("pass", "warn", "fail")
    assert "planned" in r and "missing" in r
