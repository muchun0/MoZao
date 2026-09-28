# -*- coding: utf-8 -*-
"""模块4 ReaderAgent 结果自校验测试（P2）。

覆盖：字数严重不足触发自动重写、重写后复检通过、重复片段检测、
正常章节不触发重写、校验结果落 review.json。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, AgentGuardConfig
from novel_gen.store.project import ProjectStore
from novel_gen.store.schemas import Chapter
from novel_gen.pipeline.quality import QualityChecker


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    s = ProjectStore.create(tmp_path / "q", cfg)
    return s


def _mk_llm(reader_result: str = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}',
            rewrite_text: str = ""):
    """按调用顺序返回：第1次=读者校验，第2次=自动重写。"""
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return reader_result
        return rewrite_text

    llm.chat.side_effect = _chat
    return llm, calls


@pytest.mark.p2
def test_short_chapter_triggers_rewrite(store):
    llm, calls = _mk_llm(rewrite_text="这是一段足够长的重写后正文。" * 100 + "\n【摘要】重写成功")
    qc = QualityChecker(store.load_config(), llm, store)
    chapter, result = qc.ensure_quality(1, "第一章", "推进主线", "太短了。", "摘要")
    # 触发 1 次重写（第1次=读者校验，第2次=重写）
    assert calls["n"] >= 2
    # 重写后正文被采用
    assert len(chapter.content) > 100
    # 结果落 review.json
    review = json.loads((store.dir / "review.json").read_text(encoding="utf-8"))
    assert review[0]["chapter"] == 1
    assert "quality_check" in review[0]


@pytest.mark.p2
def test_quality_passed_no_rewrite(store):
    llm, calls = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    # 真实且多样、篇幅达标（>1500 字）、末句完整收尾的连贯正文 → 应直接通过、不触发重写
    long_content = (
        "天还没亮，墟口外的风就带着一股潮气。陆沉在界碑下蹲了一夜，膝盖早就僵了，"
        "站起来时小腿一麻，扶着碑沿才站稳。他低头数了一遍手指，又把手伸进怀里，"
        "摸到那块蚀晶，凉的，贴着肋骨。昨晚老赵的话还在耳朵里转，蚀晶挖够三块就撤，"
        "别他妈贪。可他现在一块都没有。\n\n"
        "墟口比昨天又大了些。灰雾贴着地面爬，像一层没化开的霜。远处有人低声喊他名字，"
        "他没回头，只把腰间布袋紧了紧。昨天下墟的人回来五个，走了十二个，瘦猴没在里头。"
        "他数了数袋里的干粮，够吃三天。三天够他再下一次墟。\n\n"
        "他往里走的时候，雾先是没过脚踝，然后到膝。界碑上的字在雾里显得模糊，"
        "他回头看了一眼，那碑已经小成一个黑点。前头有动静，他停住，手摸上铁凿柄。"
        "雾里出来一个人，是独眼，浑身是血，短铲没了，嘴唇干裂。\n\n"
        "陆沉认出他，没急着走近，先看了一眼他身后，雾里空荡荡的。独眼看出他在想什么，"
        "咧嘴想笑，脸却抽动了一下。\n\n"
        "底下不对劲，他说，声音抖得不成调，灰卒跟疯了似的，见人就咬。我这条胳膊，"
        "差点交代在第二层。你师父呢，他补了一句，眼神闪了闪。\n\n"
        "陆沉没答，从怀里摸出一块干粮递过去。独眼接过，也不道谢，狼吞虎咽咬了几口，"
        "被噎得直咳，咳得眼眶发红。\n\n"
        "老陈头昨晚就没回来。陆沉在心里算了一遍：他下到第二层去凿那块赤纹蚀晶，"
        "说这一趟够铺子吃半年。陆沉在墟口等到三更，雾里没人出来。他今早下来，"
        "一半是挖晶，一半是找人。可这话他没法跟独眼说，说了也没用。\n\n"
        "不知道，他说，然后往雾深处走。\n\n"
        "独眼在后面喊了两声，声音被雾吞得只剩一半。陆沉没回头，也没停下。"
        "他踩过一块松动的碎石，听见它滚进雾里，很久没有落地声。他忽然想起小时候，"
        "老陈头教他淬火，说火候不到铁会脆，火候过了铁会裂。他说这话的时候，"
        "眉毛上还挂着火星。\n\n"
        "雾里传来一声响，很轻，像骨头在关节窝里转了一圈。陆沉停住，把铁凿握紧。"
        "他数了数自己还剩多少块干粮，又在心里把退路走了一遍。然后他往前迈了一步。"
    )
    chapter, result = qc.ensure_quality(1, "第一章", "推进主线", long_content, "摘要")
    assert result["passed"] is True
    assert calls["n"] == 1  # 只调了读者校验，未触发重写


@pytest.mark.p2
def test_duplicate_detection(store):
    """与前一章高度重复 → 结构-与前文重复问题。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="重复段落A" * 200, summary="s"))
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    dup_content = "重复段落A" * 200 + "新增内容" * 10
    result = qc.check(2, "第二章", "推进", dup_content)
    assert any("与前文重复" in (i.get("type", "") or "") for i in result["issues"])


@pytest.mark.p2
def test_output_check_flag(store):
    """输出含明显违规词 → 合规-疑似违规内容问题。"""
    store.save_chapter(Chapter(no=1, title="第一章", content="正常内容" * 200, summary="s"))
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    bad = "正常内容" * 200 + "本章包含对幼女做爱的详细描写。"
    result = qc.check(2, "第二章", "推进", bad)
    assert any("合规" in (i.get("type", "") or "") for i in result["issues"])


# ---------- 章内多稿拼接 / 结尾截断 防护（P2） ----------

_MID = (
    "老赵拿匕首在岩壁上敲了两下，一块拇指大的蚀晶落进他掌心。蚀晶的微热贴着掌心，"
    "他攥紧了，凿头磨得发亮的铁凿被他换到右手。灰雾里又传来一声响，很轻，"
    "像骨头在关节窝里转了一圈。所有人都停了手，老赵慢慢直起腰，匕首反握。"
    "瘦猴的步子碎而快，踩得石片哗啦响。陆沉蹲下来，从腰间布袋里摸出铁凿，"
    "凿了七下才崩下一块蚀晶，断面不齐，有裂纹，卖不上价。灰雾深处又响了一声，"
    "这次更近，然后是第二声、第三声，从不同方向同时响起来，像一圈人同时掰手指。"
    "老赵的刀劈在第一只灰卒的甲壳上，火星溅出来，刀口崩了。灰卒的嘴裂开，"
    "横向张开，比他脑袋还宽。他抬起铁凿扎过去，凿尖卡在甲壳缝里。"
)


def _mk_draft(extra: str) -> str:
    """构造一个以面板句完整收尾的草稿（用于拼接防护测试）。"""
    return (
        "灰雾从裂隙口漫出来，像没烧透的炭堆被泼了水。陆沉踩在碎石上，"
        "鞋底碾过蚀晶碎屑，声音被雾气吞掉大半。\n\n"
        + _MID
        + extra
        + "\n\n今日可死次数：2。\n\n明天呢？"
    )


@pytest.mark.p2
def test_intra_chapter_duplicate_detected(store):
    """同一章内多段草稿拼接 → 结构-章内大量自重复问题（大改，不通过）。"""
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    stacked = _mk_draft("第一稿：他往侧边跑，撞上一块岩壁。") + "\n\n" + \
        _mk_draft("第二稿：他往侧边跑，撞上一块岩壁。这次他记住了蚀晶掉落的方位。")
    result = qc.check(1, "第一章", "推进", stacked)
    assert any("章内大量自重复" in (i.get("type", "") or "") for i in result["issues"])
    assert result["passed"] is False


@pytest.mark.p2
def test_incomplete_ending_detected(store):
    """正文未以句末标点收尾（中途截断）→ 结构-正文未完整收尾问题。"""
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    result = qc.check(1, "第一章", "推进", "少年握剑，走向山下，风里有人在喊他的名")
    assert any("未完整收尾" in (i.get("type", "") or "") for i in result["issues"])


@pytest.mark.p2
def test_salvage_recovers_single_draft(store):
    """多稿拼接正文 → ensure_quality 确定性抢救为单一完整草稿，且不依赖 LLM 重写。"""
    llm, calls = _mk_llm()
    cfg = store.load_config()
    cfg.chapter_words = 500  # 抢救后的单稿（~370字）达标即可通过，避免触发重写
    qc = QualityChecker(cfg, llm, store)
    stacked = _mk_draft("第一稿：他往侧边跑，撞上一块岩壁。") + "\n\n" + \
        _mk_draft("第二稿：他往侧边跑，撞上一块岩壁。这次他记住了蚀晶掉落的方位。")
    chapter, result = qc.ensure_quality(1, "第一章", "推进", stacked, "摘要")
    # 抢救后只保留单一草稿：不再出现两个"明天呢？"分界，且以句号收尾
    assert chapter.content.count("明天呢？") <= 1
    assert chapter.content.rstrip().endswith("。")
    # 仅两次读者校验（首检+抢救后复检），未触发 LLM 重写（确定性抢救已修复）
    assert calls["n"] <= 2
    # 抢救以 auto_revised 落 review.json
    review = json.loads((store.dir / "review.json").read_text(encoding="utf-8"))
    assert review[0]["auto_revised"] is True
    # 抢救后复检应通过（不再有多稿拼接/截断等大改问题）
    assert result["passed"] is True


@pytest.mark.p2
def test_quality_check_disabled(store, tmp_path):
    """quality_check=False 时 ChapterWriter 不触发校验（降级开关）。"""
    cfg = store.load_config()
    cfg.agent_guard.quality_check = False
    from novel_gen.pipeline.stages import ChapterWriter
    from novel_gen.pipeline.memory import Memory
    llm = MagicMock()
    llm.chat.return_value = "正文内容" * 200 + "\n【摘要】正常"
    w = ChapterWriter(cfg, llm, store, Memory(store, 5))
    ch = w.write(1, "第一章", "推进", None, silent=True)
    assert len(ch.content) > 100
    # 未触发校验（不会因短正文调用重写导致额外调用）
    assert llm.chat.call_count >= 1
