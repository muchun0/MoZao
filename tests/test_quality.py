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
    assert calls["n"] == 1  # 读者 LLM 校验 1 次（章内矛盾自检默认仅高质量模式，REQ-token优化 P1-3）


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
    """构造一个以非面板短句完整收尾的草稿（用于拼接防护测试）。

    分界标记用普通短句（"他记住了蚀晶的方位。"）而非数字面板行——
    P1-2 起面板行不再作为草稿分界标记（同值复现属"数字结算未推进"，
    由 _panel_progress_issues 专检；真拼接靠 n-gram 覆盖信号判定）。
    """
    return (
        "灰雾从裂隙口漫出来，像没烧透的炭堆被泼了水。陆沉踩在碎石上，"
        "鞋底碾过蚀晶碎屑，声音被雾气吞掉大半。\n\n"
        + _MID
        + extra
        + "\n\n他记住了蚀晶的方位。\n\n明天呢？"
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
    # 两轮校验（首检+抢救后复检）各含读者校验+矛盾自检，未触发 LLM 重写
    assert calls["n"] <= 4
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


# ---------- 章内同句复读（REQ-章内自洽与机制账本，P2 确定性） ----------

_PAD_A = ("风从墟口灌进来，带着湿土和血的腥气。他把铜钱塞回怀里，指尖在边缘上磨了一下。"
          "城墙的影子压过来，像一头睡着的兽。独眼刘的声音从门里传出来，骂骂咧咧。"
          "他坐在门槛上，膝盖抵着下巴。")
_PAD_B = ("雾没过脚踝，他摸到怀里的蚀晶，凉的。风从墟底吹上来，带着腥气。"
          "他想起娘死前塞给他的铜钱，边缘磨得发亮。他点点头，把铜钱攥进掌心。"
          "墟口的雾一层比一层厚。")
# 两段填充内部及彼此之间无重复句；保证只命中被测的重复句，断言不串扰。


@pytest.mark.p2
def test_repeat_narrative_long_x3():
    """叙述长句原样出现 3 次 → 大改（触发自动重写）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "城墙像谁把墓碑插满了地平线。" * 3 + _PAD_A + _PAD_B
    issues = qc._repeat_sentence_issues(content)
    assert issues and issues[0]["level"] == "大改"
    assert "同句复读" in issues[0]["type"]
    assert "城墙像谁把墓碑插满了地平线" in issues[0]["detail"]


@pytest.mark.p2
def test_repeat_narrative_long_x2():
    """叙述长句（≥12 字）出现 2 次 → 大改（整句复读）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "城墙像谁把墓碑插满了地平线。" + _PAD_A + "城墙像谁把墓碑插满了地平线。" + _PAD_B
    issues = qc._repeat_sentence_issues(content)
    assert issues and issues[0]["level"] == "大改"


@pytest.mark.p2
def test_panel_line_x2_not_repeat():
    """数字面板行不再纳入复读统计（P1-2 归类）：面板行重复由 _panel_progress_issues
    专检（跨结算→大改/无事件→中改），复读不再双重大改（22:32 版实测误触发重写）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "今日可死次数：1。" + _PAD_A + "今日可死次数：1。" + _PAD_B
    assert qc._repeat_sentence_issues(content) == []


@pytest.mark.p2
def test_repeat_narrative_short_x2_medium():
    """叙述短句（8~11 字）出现 2 次 → 中改（记录不阻塞）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "他转身往墟口走去。" + _PAD_A + "他转身往墟口走去。" + _PAD_B
    issues = qc._repeat_sentence_issues(content)
    assert issues and issues[0]["level"] == "中改"


@pytest.mark.p2
def test_repeat_dialogue_x3_hard():
    """对话句（≥8 字）出现 3 次 → 大改；短口头禅（"行"）豁免不计。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = ("“别他妈死在里面，拖累老子。”疤脸说。" + _PAD_A + "“别他妈死在里面，拖累老子。”他又说。"
               + _PAD_B + "“别他妈死在里面，拖累老子。”疤脸最后叮嘱。")
    issues = qc._repeat_sentence_issues(content)
    assert issues and issues[0]["level"] == "大改"
    assert "别他妈死在里面" in issues[0]["detail"]


@pytest.mark.p2
def test_repeat_dialogue_x2_allowed():
    """对话口头禅出现 2 次 → 不报（writer.md 允许 ≤2 次）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "“行。”他说。" + _PAD_A + "“行。”他又说。" + _PAD_B
    assert qc._repeat_sentence_issues(content) == []


@pytest.mark.p2
def test_repeat_none():
    """无复读 → 不报（无误报）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    ok = ("他往墟口走，雾没过脚踝。独眼在雾里站着，浑身是血。他摸到怀里的蚀晶，凉的。"
          "风从墟底吹上来，带着腥气。他想起娘死前塞给他的铜钱，边缘磨得发亮。"
          "独眼说别他妈死在里面。他点点头，把铜钱攥进掌心。城墙的影子压过来，像一头睡着的兽。")
    assert qc._repeat_sentence_issues(ok) == []


# ---------- 机制数字结算校验（P0-2，REQ-章内自洽与机制账本·扩展） ----------

_PAD_C = ("灰雾从裂隙口漫出来，像没烧透的炭堆被泼了水。陆沉踩在碎石上，鞋底碾过蚀晶碎屑，"
          "声音被雾气吞掉大半。他蹲下来，从腰间布袋里摸出铁凿，凿了七下才崩下一块蚀晶。")
_PAD_D = ("他爬起来，膝盖顶着碎石，手背上全是血口子。背篓翻倒在三步外，铁精矿石散了一地。"
          "他盯着火把看了两秒，估算自己死了多久。不到半盏茶的工夫。")

@pytest.mark.p2
def test_panel_progress_not_decreasing(store):
    """同一面板数字（同标签同数值）跨段复现 = 结算未推进 → 大改「机制数字未结算推进」。"""
    llm, _ = _mk_llm()
    qc = QualityChecker(store.load_config(), llm, store)
    # 两次面板出现间隔需 > _PANEL_REPEAT_SPAN(150)，用两段填充拉大
    content = ("他死了第一次，视野角落亮起：今日可死次数：2/3。\n\n"
               + _PAD_C + _PAD_D + _PAD_C
               + "\n\n他死了第二次，视野角落仍显示：今日可死次数：2/3。\n\n"
               + _PAD_D)
    result = qc.check(1, "第一章", "推进", content)
    assert any("机制数字未结算推进" in (i.get("type", "") or "") for i in result["issues"])
    assert result["passed"] is False


@pytest.mark.p2
def test_panel_progress_normal_decrease_ok():
    """面板数字正常递减（3/3→2/3→1/3）→ 不误报。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = ("视野角落亮起：今日可死次数：3/3。\n\n" + _PAD_C
               + "\n\n视野角落亮起：今日可死次数：2/3。\n\n" + _PAD_D
               + "\n\n视野角落亮起：今日可死次数：1/3。")
    assert qc._panel_progress_issues(content) == []


@pytest.mark.p2
def test_panel_progress_close_repeat_ok():
    """同一面板数字紧邻复现（间隔 <150 字，如"数字跳了一下"式强调）→ 不误报。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "数字跳了一下，今日可死次数：2/3。今日可死次数：2/3。他盯着那行字，手心出汗。"
    assert qc._panel_progress_issues(content) == []


# ---------- 面板事件锚点（2026-09-30：1/3 冗余显示误报修复） ----------

@pytest.mark.p1
def test_panel_no_event_repeat_is_minor():
    """同值跨段复现但中间无结算事件（赶路/确认状态）→ 降级中改"冗余显示"，不再误报大改。

    《万墟之上》21:52 版：第二次死亡后结算 1/3，之后赶路/章末备注栏变化时又显示
    两次 1/3——数字本就不该变，属冗余显示而非结算未推进。
    """
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    filler = ("他卷起袖子，蚀痕在小臂上蜿蜒，像一条暗紫色的蛛网。灰雾漫过脚踝，"
              "他攥紧凿子柄，往墟口方向挪了两步，背篓的铁精在身后轻轻晃动，"
              "矿渣从裂隙口簌簌落下来，砸在碎石堆上，又被风卷走。")
    content = ("面板在黑暗里浮出来。今日可死次数：1/3。\n\n"
               + filler * 2
               + "\n\n他卷起袖子看了一眼面板：今日可死次数：1/3。\n\n"
               + filler * 2
               + "\n\n面板闪了一下，备注栏多出四个字，数字没变：今日可死次数：1/3。")
    issues = qc._panel_progress_issues(content)
    assert not any("机制数字未结算推进" in (i.get("type", "") or "") for i in issues), issues
    assert any("面板数字重复显示（冗余）" in (i.get("type", "") or "") and i["level"] == "中改" for i in issues)


@pytest.mark.p1
def test_panel_event_between_repeat_still_major():
    """两次同值之间有死亡/复活等结算事件 → 仍判大改（真结算未推进）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = ("他死了第一次，视野角落亮起：今日可死次数：2/3。\n\n"
               + _PAD_C + _PAD_D + _PAD_C
               + "\n\n他死了第二次，视野角落仍显示：今日可死次数：2/3。\n\n"
               + _PAD_D)
    issues = qc._panel_progress_issues(content)
    assert any("机制数字未结算推进" in (i.get("type", "") or "") and i["level"] == "大改" for i in issues)


@pytest.mark.p1
def test_panel_recall_not_event():
    """回忆/指认/他人预判语境里的"死了"不算结算事件 → 不误报大改。

    《万墟之上》22:32 版实测："她看见他死了""你死了""她快死了""他应该已经死了"
    "感知到了他死亡"等 6 处锚点全是回忆指认，面板 2/3 未变是正确行为。
    """
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    filler = ("他卷起袖子，蚀痕在小臂上蜿蜒，像一条暗紫色的蛛网。灰雾漫过脚踝，"
              "他攥紧凿子柄，往墟口方向挪了两步，背篓的铁精在身后轻轻晃动，"
              "矿渣从裂隙口簌簌落下来，砸在碎石堆上，又被风卷走。")
    content = ("复活后面板刷新：今日可死次数：2/3。\n\n"
               + filler * 2
               + "\n\n她看见他死了，看见他倒在蚀雾里，又看见他三秒后从蚀泥中睁眼。"
               "她的共情执能——她感知到了他死亡的情绪印记。\n\n"
               "他抬手看面板：今日可死次数：2/3。\n\n"
               + filler * 2
               + "\n\n苏妄言快死了。他应该已经死了的人，不该出现在这里。"
               "面板在视野角落亮着：今日可死次数：2/3。")
    issues = qc._panel_progress_issues(content)
    assert not any("机制数字未结算推进" in (i.get("type", "") or "") for i in issues), issues
    assert any("面板数字重复显示（冗余）" in (i.get("type", "") or "") and i["level"] == "中改" for i in issues)


@pytest.mark.p1
def test_summary_consistency_mismatch():
    """摘要提到的面板分数与正文最终结算不一致 → 大改（摘要未随正文更新）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = ("他死了，面板跳出今日可死次数：1/3。复活后他往墟口爬，"
               "视野角落的今日可死次数：1/3 不再变化。")
    bad_sum = "陆沉死了一次，面板从 3/3 结算为 2/3，蚀痕延伸一寸。"
    issues = qc._summary_consistency_issues(content, bad_sum)
    assert any("摘要与正文最终状态不符" in (i.get("type", "") or "") and i["level"] == "大改" for i in issues)


@pytest.mark.p1
def test_summary_consistency_ok():
    """摘要分数与正文最终一致 / 摘要无分数 → 不报。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = "他死了，面板跳出今日可死次数：1/3。"
    assert qc._summary_consistency_issues(content, "陆沉死了一次，面板结算为 1/3。") == []
    assert qc._summary_consistency_issues(content, "陆沉在墟底被蚀物袭击，生死未卜。") == []
    assert qc._summary_consistency_issues(content, "") == []


@pytest.mark.p1
def test_consistency_llm_event_duplicate_detected():
    """矛盾自检识别"同一事件两版本"（救苏妄言空地版/坡地版）→ 大改。"""
    cfg = NovelConfig(title="t", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.side_effect = lambda *a, **k: (
        '{"contradictions": [{"type": "事件重复", '
        '"detail": "前一处写陆沉在墟口空地救苏妄言引开赤尉，'
        '后一处又写坡地上苏妄言再被赤尉围、陆沉又冲下去救——同一事件两版本"}]}'
    )
    qc = QualityChecker(cfg, llm, None)
    issues = qc._consistency_llm_check(1, "正文若干")
    assert any("事件重复" in (i.get("type", "") or "") and i["level"] == "大改" for i in issues)


@pytest.mark.p1
def test_rewrite_whole_injects_event_dedup_rules():
    """整章重写注入"每事件只写一遍"纪律：质检含事件重复/两版本 → 重写 prompt 含
    删除重复版本、事件间因果推进、状态增量要求（4500 字版重写 3 轮修不掉根因）。"""
    cfg = NovelConfig(title="t", chapter_words=2500, total_words=10000)
    llm = MagicMock()
    llm.chat.side_effect = lambda *a, **k: "重写后的正文。\n\n【摘要】最终 2/3"
    qc = QualityChecker(cfg, llm, None)
    issues = [
        {"type": "自相矛盾-事件重复", "detail": "同一事件两版本：救苏妄言石殿版/墟口外版", "level": "大改"},
        {"type": "写作-字数超出目标", "detail": "正文 4500 字", "level": "中改"},
    ]
    out = qc._rewrite_whole(1, "第一章", "目标", "旧正文", issues)
    assert out
    prompt = llm.chat.call_args.args[1] if llm.chat.call_args.args else llm.chat.call_args[1]
    assert "删除重复的事件版本" in prompt
    assert "禁止回到上一事件开头状态重演" in prompt
    assert "每个事件的结束状态" in prompt and "增量变化" in prompt
    # 无事件重复问题时：不注入该纪律块
    llm2 = MagicMock()
    llm2.chat.side_effect = lambda *a, **k: "重写后的正文。\n\n【摘要】最终 2/3"
    qc2 = QualityChecker(cfg, llm2, None)
    qc2._rewrite_whole(1, "第一章", "目标", "旧正文", [{"type": "写作-字数超出目标", "detail": "4500 字", "level": "中改"}])
    p2 = llm2.chat.call_args.args[1] if llm2.chat.call_args.args else llm2.chat.call_args[1]
    assert "删除重复的事件版本" not in p2


# ---------- 面板行不再作草稿分界标记（P1-2） ----------

@pytest.mark.p2
def test_panel_line_not_draft_marker():
    """面板行（含次数+数字）不作为草稿分界标记：同值复现不判多稿拼接、不触发抢救。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    content = ("今日可死次数：2/3。\n\n他往墟口走，雾没过脚踝。\n\n今日可死次数：2/3。\n\n"
               "他捡起背篓，把散落的铁精矿石拢回去。\n\n今日可死次数：2/3。\n\n他爬残阶。")
    assert qc._draft_markers(content, min_count=3) == {}
    issues = qc._self_dup_issues(content)
    assert not any("章内大量自重复" in (i.get("type", "") or "") for i in issues)


@pytest.mark.p2
def test_panel_line_stacked_not_salvaged(store):
    """面板行收尾的多稿拼接：信号B 报拼接，但抢救被拒（找不到非面板 marker），
    原文不被拦腰截断，走重写兜底（防《万墟之上》式误截断）。"""
    llm, _ = _mk_llm(rewrite_text="")
    qc = QualityChecker(store.load_config(), llm, store)
    draft = _mk_draft("第一稿：他往侧边跑。") + "\n\n" + _mk_draft("第二稿：他往侧边跑，记住了方位。")
    # 把分界标记改回面板行（模拟旧数据/模型爱用面板收尾的拼接稿）
    stacked = draft.replace("他记住了蚀晶的方位。", "今日可死次数：2。")
    original_len = len(stacked)
    chapter, _ = qc.ensure_quality(1, "第一章", "推进", stacked, "摘要", silent=True)
    # 抢救被拒：正文未被截断为单一草稿（保持拼接原样，交由重写/人工处理）
    assert len(chapter.content) >= original_len * 0.9


@pytest.mark.p2
def test_salvage_rejects_too_much_loss():
    """抢救稿 < 原文 40% 时拒绝采用（_SALVAGE_MIN_KEEP 保护，防拦腰截断）。"""
    qc = QualityChecker(NovelConfig(title="t", chapter_words=2500, total_words=10000), None, None)
    marker = "他往侧边跑，撞上岩壁。"
    tails = (
        "碎石扑了他一脸，他咬着牙往深处挪了半步，把蚀晶揣进怀里，又回头看了一眼来路，"
        "雾里空荡荡的，只有风在响。他攥紧铁凿，指节发白，心里数着退路。"
    )
    chunks = [marker + "\n\n" + f"第{i}稿" + tails for i in range(5)]
    content = "\n\n".join(chunks)
    salvaged = qc._salvage_single_draft(content)
    assert len(salvaged) < len(content) * 0.4


# ---------- 重写注入设定上下文（P0-1） ----------

@pytest.mark.p2
def test_rewrite_whole_injects_context(store):
    """重写 prompt 注入机制卡/世界观/人物卡/上一章账本（不再真空重写）。"""
    from novel_gen.store.schemas import World, Character
    world = World(world_view="万墟之上，下二戍，蚀雾弥漫。",
                  mechanism_rules="每死一次面板数字递减一，今日可死三次；第三次死亡后当日次数清零。")
    world.characters = [Character(name="陆沉", role="主角", desc="背负蚀痕的不死学徒")]
    store.save_world(world)
    # 账本章号 1 < 当前章 2 → 作为上一章承接状态注入
    store.save_chapter_state({"chapter": 1, "numbers": ["今日可死次数：1/3"]})
    llm = MagicMock()
    llm.chat.return_value = "重写后的正文。\n【摘要】ok"
    qc = QualityChecker(store.load_config(), llm, store)
    qc._rewrite_whole(2, "第二章", "推进", "原正文" * 200,
                      [{"type": "大改", "detail": "面板数字未结算推进"}])
    user = llm.chat.call_args[0][1]
    assert "金手指机制卡" in user
    assert "每死一次面板数字递减一" in user          # 机制结算规则注入
    assert "今日可死次数：1/3" in user                # 上一章账本数字注入
    assert "人物卡" in user


@pytest.mark.p2
def test_rewrite_whole_skips_own_chapter_state(store):
    """重写本章时，本章旧账本（章号 >= 当前章）不得当"上章承接"注入——
    否则模型会把旧稿数字（如 2/3）当标准答案，数字回跳修不动。"""
    from novel_gen.store.schemas import World
    world = World(world_view="万墟之上", mechanism_rules="每死一次面板数字递减一")
    store.save_world(world)
    # 账本章号 = 1 = 当前章 → 是本章旧稿账本，不应注入
    store.save_chapter_state({"chapter": 1, "numbers": ["今日可死次数：2/3"]})
    llm = MagicMock()
    llm.chat.return_value = "重写后的正文。\n【摘要】ok"
    qc = QualityChecker(store.load_config(), llm, store)
    qc._rewrite_whole(1, "第一章", "推进", "原正文" * 200,
                      [{"type": "大改", "detail": "面板数字未结算推进"}])
    user = llm.chat.call_args[0][1]
    assert "金手指机制卡" in user
    assert "上一章机制数字" not in user          # 本章旧账本 2/3 不得注入
    assert "今日可死次数：2/3" not in user


# ---------- 自动重写多轮（P0-3） ----------

@pytest.mark.p2
def test_ensure_quality_max_rewrite_rounds(store):
    """复检一直不通过 → 最多重写 _MAX_REWRITE 轮（原 1 轮即放行）。"""
    from novel_gen.pipeline.quality import _MAX_REWRITE
    cfg = store.load_config()
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
        return "还是太短的正文。"

    llm.chat.side_effect = _chat
    qc = QualityChecker(cfg, llm, store)
    chapter, result = qc.ensure_quality(1, "第一章", "推进", "太短了。", "摘要", silent=True)
    # 每轮重写：1 次 reader 校验 + 1 次重写；首检 1 次 reader + _MAX_REWRITE 次复检
    assert calls["n"] >= 1 + 2 * _MAX_REWRITE
    assert result["passed"] is False


# ---------- 章内矛盾 LLM 自检：普通模式降频 + 全文明细（P2） ----------

@pytest.mark.p2
def test_consistency_llm_every_normal_mode(store):
    """普通模式 + consistency_llm_check=True → 每 N 章抽 1 次（第1章触发、第2章不触发）。"""
    cfg = store.load_config()
    cfg.high_quality_mode = False
    cfg.consistency_llm_check = True
    cfg.consistency_llm_every = 3
    long = "正常内容段落，风从墟口灌进来，他握紧铁凿，往雾里走。" * 40
    llm = MagicMock()
    llm.chat.return_value = '{"issues": [], "追更欲": 4, "一句话感受": "ok"}'
    qc = QualityChecker(cfg, llm, store)
    qc.check(1, "第一章", "推进", long)
    assert llm.chat.call_count == 2  # reader + consistency
    llm.reset_mock()
    qc.check(2, "第二章", "推进", long)
    assert llm.chat.call_count == 1  # 仅 reader（2 % 3 == 2 不触发）


@pytest.mark.p2
def test_consistency_uses_full_content(store):
    """章内矛盾自检输入不再截断 3500：后半章关键状态进入检测视野。"""
    cfg = store.load_config()
    cfg.high_quality_mode = True
    llm = MagicMock()
    captured = {}

    def _chat(system, user, **kw):
        captured["user"] = user
        return '{"contradictions": []}'

    llm.chat.side_effect = _chat
    qc = QualityChecker(cfg, llm, store)
    long = ("前段内容" * 400) + "后半章关键状态：蚀痕新延伸一寸，灼烫如炭。"
    qc.check(1, "第一章", "推进", long)
    assert "后半章关键状态：蚀痕新延伸一寸" in captured["user"]


# ---------- 场景级确定性检测兜底（对话轮重复 + 字数审计） ----------

def _qc_with_cfg(cfg, store=None):
    return QualityChecker(cfg, MagicMock(), store)


@pytest.mark.p1
def test_dialogue_loop_issues():
    """同一轮问答/对话序列原样重复（循环拼接）→ 大改。"""
    from novel_gen.pipeline.quality import QualityChecker as QC
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    qc = _qc_with_cfg(cfg)
    # 同一轮对话写了五遍（"你死了？→你看错了→我看见数字了"循环），总长 >300 字触发检测
    dup = ("她盯着他：\n"
           "“你刚才——死了？”\n"
           "“你看错了。”\n"
           "“我看见数字了。从3跳到2。”\n"
           "她退了一步，转身蹲到同学身边，撕自己右袖，动作利落，牙关咬紧。\n\n") * 5
    issues = qc._dialogue_loop_issues(dup + "结尾完整。")
    assert any(i["type"] == "写作-对话轮重复（循环拼接）" and i["level"] == "大改" for i in issues)


@pytest.mark.p1
def test_dialogue_loop_no_false_positive():
    """不同台词/有递进的对话 → 不误报。"""
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    qc = _qc_with_cfg(cfg)
    fine = (
        "“你刚才——死了？”\n“你看错了。”\n“我看见数字了。从3跳到2。”\n\n"
        "她没再追问，低头缠布条。\n\n"
        "“你同学腿上的伤，再不止血就真死了。”\n她抬头看他。\n"
        "“我知道。”\n"
    )
    assert qc._dialogue_loop_issues(fine) == []


@pytest.mark.p1
def test_length_audit_issues():
    """字数审计：>1.35 倍目标 → 中改提示（不判大改、不触发重写）；正常 → 不报。

    2026-09-30 方向修正：超长 ≠ 质量失败，纯字数不触发整章重写（省 token）；
    质量硬伤由截断/循环/拼接/矛盾等维度判定。
    """
    cfg = NovelConfig(title="测试书", chapter_words=1000, total_words=4000)
    qc = _qc_with_cfg(cfg)
    # 1.7 倍 → 仅中改（不再大改）
    issues = qc._length_audit_issues("长" * 1700)
    assert issues and issues[0]["level"] == "中改" and "字数超出目标" in issues[0]["type"]
    # 1.4 倍 → 中改
    mid = qc._length_audit_issues("长" * 1400)
    assert any(i["level"] == "中改" and "字数超出" in i["type"] for i in mid)
    # 正常 → 不报
    assert qc._length_audit_issues("长" * 1200) == []


@pytest.mark.p1
def test_length_audit_ignores_summary():
    """字数审计剥离【摘要】段，不把摘要计入正文长度。"""
    cfg = NovelConfig(title="测试书", chapter_words=1000, total_words=4000)
    qc = _qc_with_cfg(cfg)
    content = "长" * 1200 + "\n\n【摘要】" + "摘" * 500
    assert qc._length_audit_issues(content) == []
