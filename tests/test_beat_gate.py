# -*- coding: utf-8 -*-
"""三期 F1 Beat 覆盖度门禁 测试（2026-10-14；2026-10-15 语义槽修复版）。

覆盖：
- _split_plan_beats：character_moves → 逐拍；<2 拍返回 []
- _norm_action：等价改写归一到规范动作（修复点：近义改写不再判 miss）
- compute_beat_coverage：2/3 拍命中=pass；1/3=fail；无计划拍=skip
- 分级阈值边界：warn 区间落在 warn
- 语义槽鲁棒性：近义改写（咬喉↔咬穿、检查面板↔面板亮）仍判覆盖（修复点）
- 关键信息缺失仍扣分（防假阳性）
- 真实第一章 5 拍全实质覆盖 → 覆盖率显著提升（根治 0.00 假阴性）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.beat_gate import (
    _split_plan_beats, _normalize_term, _slot_hit, compute_beat_coverage,
)

_MOVES = "主角在墟口遭遇灰卒被咬死→复活后用铁锤砸碎灰卒核心→抱着小九回到铁匠铺"


@pytest.mark.p2
def test_split_plan_beats():
    beats = _split_plan_beats(_MOVES)
    assert len(beats) == 3
    assert _split_plan_beats("只有一拍没有分隔") == []   # <2 拍不误报
    assert _split_plan_beats("") == []
    assert _split_plan_beats("   ") == []


@pytest.mark.p2
def test_norm_action_rewrite_normalized():
    """等价改写归一到规范动作（修复点：近义改写不再判 miss；书级词族已移除）。"""
    assert _normalize_term("砸碎")[0] == "砸"
    assert _normalize_term("活过来")[0] == "复活"
    assert _normalize_term("走向")[0] == "前往"
    assert _normalize_term("没有任何动作词")[0] == "没有任何动作词"
    # _slot_hit：正文出现归一主词族任一写法即命中
    assert _slot_hit("砸碎", "他用铁锤砸碎了核心")
    assert _slot_hit("清晨", "天刚蒙蒙亮")
    assert not _slot_hit("隐藏", "一座古老的祭坛")


@pytest.mark.p1
def test_coverage_two_of_three_pass():
    """正文覆盖 2/3 拍（0.67 ≥ 0.6）→ pass；且"走回"命中"回到"类拍，语义槽不再误判 miss。"""
    content = ("陆沉在墟口拾荒时突然遭遇灰卒，被咬喉致死。随后他复活过来，"
               "拿起铁锤狠狠砸碎了灰卒的核心。之后他抱着小九一路走回铁匠铺。")
    r = compute_beat_coverage(content, _MOVES)
    # 修复前 bigram 对第3拍"回到vs走回"判 miss → 0.667；语义槽下"走回"归一到"回到" → 全命中 1.0
    assert r["coverage"] == pytest.approx(1.0, abs=0.02)
    assert r["verdict"] == "pass"
    assert len(r["planned"]) == 3


@pytest.mark.p1
def test_coverage_one_of_three_fail():
    """正文只沾了第 1 拍的边（< 0.4）→ fail 且 missing 列出漏拍。"""
    content = "陆沉在墟口拾荒，灰卒从暗处扑出来。"
    r = compute_beat_coverage(content, _MOVES)
    assert r["coverage"] < 0.4
    assert r["verdict"] == "fail"
    assert len(r["missing"]) >= 1


@pytest.mark.p2
def test_coverage_no_plan_skip():
    """无计划拍（moves/core_event 皆空）→ skip，不误报。"""
    r = compute_beat_coverage("随便一段正文", "", "")
    assert r["verdict"] == "skip"
    assert r["coverage"] is None


@pytest.mark.p2
def test_coverage_falls_back_to_core_event():
    """moves 空但 core_event 有 ≥2 拍 → 用 core_event 评估。"""
    r = compute_beat_coverage(
        "陆沉遭遇灰卒被咬死。复活后砸碎灰卒核心。",
        "", "主角遭遇灰卒被咬死→复活砸碎灰卒核心")
    assert r["verdict"] in ("pass", "warn")
    assert r["coverage"] is not None


@pytest.mark.p2
def test_coverage_paraphrase_robust():
    """拍文本与正文措辞不同但核心词都在 → 命中（语义槽鲁棒性）。"""
    moves = "他踏入密林深处→发现隐藏的古老祭坛"
    content = "陆沉走进密林深处，竟在草丛后发现了一座隐藏的古老祭坛。"
    r = compute_beat_coverage(content, moves)
    assert r["coverage"] >= 0.6
    assert r["verdict"] == "pass"


@pytest.mark.p2
def test_coverage_missing_detail_counts_as_miss():
    """拍要求的关键信息正文没写 → 该拍不命中，覆盖率下降（防假阳性）。"""
    moves = "他踏入密林深处→发现隐藏的古老祭坛"
    content = "陆沉走进密林深处，竟在草丛后发现了一座古老的祭坛。"
    r = compute_beat_coverage(content, moves)
    # 第2拍缺「隐藏」关键信息 → 不命中，覆盖率落到 0.5（warn），不是 0 也不是全 pass
    assert 0.0 < r["coverage"] < 0.6


@pytest.mark.p1
def test_ch1_real_beats_not_false_negative():
    """真实第一章：计划拍近义改写后仍应判覆盖（根治 0.00 假阴性的回归用例）。

    大纲 5 拍 vs 正文（旧稿 2876 字）——正文对 5 拍均实质覆盖，只是措辞改写：
      ①「清晨出门前检查面板(3次)→拾荒→遇灰卒→被咬喉致死」→「昨日子时还是三/废铁堆翻找/灰雾里三个影子/裂口对准喉咙——咬穿了」
      ②「复活后面板显示2」→「面板在脑子里亮了一下——今日可死次数：2」
      ③「趁进食间隙铁锤砸碎核心」→「胸口那点暗红往外移了移…铁锤砸下去…核心碎成粉末」
      ④「小九出现弓背嘶吼、灰卒逃窜」→「小九弓起背…低吼…两只灰卒…钻进灰雾里不见了」
      ⑤「抱着小九回铺子、老陈头见血洞、药钱塞回」→「布衫破了个洞…铜板推过来」
    """
    moves = (
        "陆沉清晨出门前检查面板（3次），在墟口拾荒时遭遇灰卒，被咬喉致死"
        "→复活后面板显示2→利用灰卒进食同类的间隙用铁锤砸碎一只灰卒的核心"
        "→小九出现，弓背嘶吼，剩余灰卒逃窜"
        "→陆沉抱着小九回到铁匠铺，老陈头发现他衣服上的血洞但没追问，只是把药钱塞回他手里"
    )
    content = (
        "面板在脑子里亮了一下——今日可死次数：2。天刚蒙蒙亮，铁匠铺的烟囱还没冒气。"
        "陆沉绕到后门，从柴堆底下摸出那把缺了角的铁锤，插进腰后。"
        "废铁堆在墟口外围，陆沉蹲下来，手指在铁块间翻找。"
        "灰雾里站着三个影子。人形，无头。灰卒。三只灰卒同时扑上来，"
        "领头的灰卒压下来，裂口对准他喉咙——咬穿了。"
        "再睁眼时，灰卒正围着他先前站的地方啃。领头的先舔，胸口那点暗红往外移了移。"
        "陆沉从废铁堆上滑下来，铁锤已经在手里，三步冲到领头灰卒侧面，锤头对准胸口那点暗红——"
        "砸下去。暗红核心碎成粉末。"
        "废铁堆顶上，蹲着一只黑猫。小九弓起背，喉咙里滚出一声低吼。"
        "两只灰卒的触须同时缩回裂口，然后转身，钻进灰雾里，不见了。"
        "陆沉回到铁匠铺，侧身挤进来。老陈头目光停在他胸口——布衫破了个洞，边缘发黑。"
        "老陈头看了两秒，伸手去够桌角那串铜板，推过来。'药钱先欠着。'"
    )
    r = compute_beat_coverage(content, moves)
    # 旧算法这里报 0.00/fail；语义槽修复后 5 拍应全命中（覆盖率 ~1.0 → pass）
    assert r["coverage"] == pytest.approx(1.0, abs=0.2), \
        f"覆盖率仍偏低（应为~1.0，实得{r['coverage']}）: {r['missing']}"
    assert r["verdict"] == "pass"
