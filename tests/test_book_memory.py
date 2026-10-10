# -*- coding: utf-8 -*-
"""P0-7 小说级记忆系统专项测试（P1，docs/TECH-book-memory.md）。

覆盖：
- 踩坑账本：新增/同章去重/跨章累加/只记大改级
- 进化：escalate 升级永久纪律（≥2 次、去重、排除通用类）、decay 降权
- 设定账本：ensure_term 新名词去重、canon_block 注入组装
- 注入：pitfall_block Top-N 排序、numeric_backfill 数字修复回灌（解析/透传）
- 集成：QualityChecker 质检出问题→踩坑入账；_rewrite_whole 注入历史错误+数字回灌
- 集成：scene_prompt 注入 book_pitfalls；ChapterWriter 懒加载 memory
- 兼容：book_memory=False 时全部记忆行为跳过（管线行为与关闭前一致）
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.store.project import ProjectStore
from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.book_memory import BookMemory, _NUM_VIOL_RE
from novel_gen.pipeline.quality import QualityChecker
from novel_gen.pipeline.templates import scene_prompt, split_scenes_prompt
from novel_gen.pipeline.stages import ChapterWriter


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "sc", cfg)


def _memory(store):
    cfg = store.load_config()
    return BookMemory(store, cfg)


def _qc(store, llm, memory=None):
    cfg = store.load_config()
    return QualityChecker(cfg, llm, store, trace=None, guard=None, memory=memory)


# 真实且篇幅达标（>1500 字）、末句完整收尾的连贯正文 → 各确定性维度无大改（复用 test_quality 通过稿）
_LONG_OK_TEXT = (
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


# ---------- 踩坑账本 ----------

@pytest.mark.p1
def test_record_pitfall_new_entry(store):
    mem = _memory(store)
    n = mem.record_pitfall(3, [{"type": "情节-同事件循环重演",
                                "detail": "两次下墟→第一层演绎（L19-22≈L95-98）", "level": "大改"}])
    assert n == 1
    data = mem.load()
    assert len(data["pitfalls"]) == 1
    p = data["pitfalls"][0]
    assert p["pattern"] == "情节-同事件循环重演"
    assert p["count"] == 1
    assert p["last_ch"] == 3
    assert "两次下墟" in p["example"]


@pytest.mark.p1
def test_record_pitfall_ignores_minor_and_same_chapter_dedupe(store):
    mem = _memory(store)
    # 中改不记录
    mem.record_pitfall(3, [{"type": "结构-字数偏少", "detail": "中改", "level": "中改"}])
    # 同章同 pattern 只记一次（一轮重写多轮复检不虚增 count）
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "A", "level": "大改"}])
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "B", "level": "大改"}])
    data = mem.load()
    assert len(data["pitfalls"]) == 1
    assert data["pitfalls"][0]["count"] == 1


@pytest.mark.p1
def test_record_pitfall_cross_chapter_increments(store):
    mem = _memory(store)
    for ch in (3, 6):
        mem.record_pitfall(ch, [{"type": "情节-同事件循环重演", "detail": "换措辞重演", "level": "大改"}])
    data = mem.load()
    assert len(data["pitfalls"]) == 1
    p = data["pitfalls"][0]
    assert p["count"] == 2
    assert p["last_ch"] == 6
    assert p["recent_rounds"] == 2


@pytest.mark.p1
def test_top_pitfalls_sorted_by_count(store):
    mem = _memory(store)
    mem.record_pitfall(2, [{"type": "A类", "detail": "x", "level": "大改"}])
    mem.record_pitfall(4, [{"type": "B类", "detail": "y", "level": "大改"}])
    mem.record_pitfall(5, [{"type": "B类", "detail": "y2", "level": "大改"}])
    mem.record_pitfall(7, [{"type": "C类", "detail": "z", "level": "大改"}])
    tops = mem.top_pitfalls(top_n=3, min_count=1)
    assert [p["pattern"] for p in tops][0] == "B类"   # count=2 最前
    assert len(tops) == 3


@pytest.mark.p1
def test_top_pitfalls_min_count_filter(store):
    mem = _memory(store)
    mem.record_pitfall(3, [{"type": "单次类", "detail": "x", "level": "大改"}])
    assert mem.top_pitfalls(top_n=5, min_count=2) == []   # 无 ≥2 次的 → 不注入


# ---------- 进化 ----------

@pytest.mark.p1
def test_escalate_promotes_discipline_once(store):
    mem = _memory(store)
    for ch in (3, 6):
        mem.record_pitfall(ch, [{"type": "情节-同事件循环重演", "detail": "换措辞重演", "level": "大改"}])
    n1 = mem.escalate()
    assert n1 == 1
    dis = mem.load()["canon"]["disciplines"]
    assert len(dis) == 1
    assert dis[0]["pattern"] == "情节-同事件循环重演"
    # 再次 escalate 不重复升级
    assert mem.escalate() == 0
    assert len(mem.load()["canon"]["disciplines"]) == 1


@pytest.mark.p1
def test_escalate_skips_generic_types(store):
    mem = _memory(store)
    for ch in (3, 6):
        mem.record_pitfall(ch, [{"type": "结构-字数严重不足", "detail": "仅 300 字", "level": "大改"}])
    assert mem.escalate() == 0   # 通用问题类不升级（无操作性修复指令）
    assert mem.load()["canon"]["disciplines"] == []


@pytest.mark.p1
def test_decay_resets_recent_rounds(store):
    mem = _memory(store)
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "x", "level": "大改"}])
    mem.record_pitfall(6, [{"type": "情节-同事件循环重演", "detail": "y", "level": "大改"}])
    assert mem.decay(current_ch=9, inactive_rounds=3) == 1   # 第6章后已3章未复发 → 降权
    p = mem.load()["pitfalls"][0]
    assert p["recent_rounds"] == 0


# ---------- 设定账本 ----------

@pytest.mark.p1
def test_ensure_term_dedupe(store):
    mem = _memory(store)
    assert mem.ensure_term("蚀骨蚰", "蚀骨蚰（墟内第一层，灰壳毒刺）", "生物", 3) is True
    assert mem.ensure_term("蚀骨蚰", "蚀骨蚰（灰壳毒刺）", "生物", 4) is False  # 已存在 → 更新
    terms = mem.load()["canon"]["terms"]
    assert terms["蚀骨蚰"]["canonical"] == "蚀骨蚰（灰壳毒刺）"
    assert terms["蚀骨蚰"]["first_ch"] == 3   # 首次出现章保留


@pytest.mark.p1
def test_canon_block_includes_terms_and_disciplines(store):
    mem = _memory(store)
    mem.ensure_term("蚀骨蚰", "蚀骨蚰（灰壳毒刺）", "生物", 3)
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "x", "level": "大改"}])
    mem.record_pitfall(6, [{"type": "情节-同事件循环重演", "detail": "y", "level": "大改"}])
    mem.escalate()
    block = mem.canon_block()
    assert "蚀骨蚰" in block
    assert "永久纪律" in block
    assert "同一事件/情节只写一遍" in block


# ---------- 注入组装 ----------

@pytest.mark.p1
def test_pitfall_block_empty_when_no_pitfalls(store):
    mem = _memory(store)
    assert mem.pitfall_block() == ""
    assert mem.pitfall_block(for_rewrite=True) == ""


@pytest.mark.p1
def test_numeric_backfill_generates_correct_value():
    mem = _memory(MagicMock())   # 只测纯文本组装，不落盘
    mem._data = {"version": 1, "canon": {"terms": {}, "disciplines": [], "mechanism_baseline": {}},
                 "events": {"consumed": []}, "pitfalls": [], "taste": {"chapters": []}}
    out = mem.numeric_backfill(["面板「蚀化」应从基线 100 递增，正文出现回退 100→99"])
    assert "数字修复回灌" in out
    assert "基线 100（递增规则）" in out
    assert "你写的 99 违反规则" in out
    assert "正确应为 101" in out


@pytest.mark.p1
def test_numeric_backfill_passthrough_unparsable():
    mem = _memory(MagicMock())
    mem._data = {"version": 1, "canon": {"terms": {}, "disciplines": [], "mechanism_baseline": {}},
                 "events": {"consumed": []}, "pitfalls": [], "taste": {"chapters": []}}
    out = mem.numeric_backfill(["某条无法解析的违规描述"])
    assert "某条无法解析的违规描述" in out


@pytest.mark.p1
def test_numeric_backfill_empty_input(store):
    mem = _memory(store)
    assert mem.numeric_backfill([]) == ""
    assert mem.numeric_backfill(None) == ""


# ---------- QualityChecker 集成 ----------

@pytest.mark.p1
def test_quality_checker_records_pitfalls(store):
    """质检出问题 → 踩坑入账（零额外 LLM）：短章触字数大改 → book_memory.json 落账。"""
    mem = _memory(store)
    llm = MagicMock()
    calls = {"n": 0}

    def _chat(system, user, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"issues": [], "追更欲": 4}'
        return _LONG_OK_TEXT

    llm.chat.side_effect = _chat
    qc = _qc(store, llm, memory=mem)
    chapter, result = qc.ensure_quality(1, "第一章", "推进主线", "太短了。", "摘要")
    assert result["passed"] is True
    data = mem.load()
    pats = [p["pattern"] for p in data["pitfalls"]]
    assert "结构-字数严重不足" in pats   # 初始稿的大改问题已入账


@pytest.mark.p1
def test_rewrite_whole_injects_pitfall_block_and_backfill(store):
    """重写 prompt 注入：本作历史错误（Top-N）+ 数字修复回灌（正确应为 Z）。"""
    mem = _memory(store)
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "两次下墟演绎", "level": "大改"}])
    mem.record_pitfall(6, [{"type": "情节-同事件循环重演", "detail": "酒肆邀约两遍", "level": "大改"}])
    llm = MagicMock()
    llm.chat.return_value = _LONG_OK_TEXT
    qc = _qc(store, llm, memory=mem)
    issues = [
        {"type": "情节-同事件循环重演", "detail": "本章两次下墟演绎", "level": "大改"},
        {"type": "写作-机制数字方向违规",
         "detail": "面板「蚀化」应从基线 100 递增，正文出现回退 100→99", "level": "大改"},
    ]
    text = qc._rewrite_whole(7, "第七章", "目标", "旧正文内容。", issues)
    assert text == _LONG_OK_TEXT
    prompt = llm.chat.call_args[0][1]
    assert "本作历史错误" in prompt
    assert "酒肆邀约两遍" in prompt or "两次下墟演绎" in prompt
    assert "数字修复回灌" in prompt
    assert "正确应为 101" in prompt


@pytest.mark.p1
def test_rewrite_whole_no_memory_no_injection(store):
    """无 memory（None）→ 重写 prompt 不含记忆块（老调用方行为不变）。"""
    llm = MagicMock()
    llm.chat.return_value = _LONG_OK_TEXT
    qc = _qc(store, llm, memory=None)
    issues = [{"type": "情节-同事件循环重演", "detail": "x", "level": "大改"}]
    qc._rewrite_whole(1, "第一章", "目标", "旧正文。", issues)
    prompt = llm.chat.call_args[0][1]
    assert "本作历史错误" not in prompt
    assert "数字修复回灌" not in prompt


# ---------- templates / stages 集成 ----------

@pytest.mark.p1
def test_scene_prompt_injects_book_pitfalls(store):
    cfg = store.load_config()
    scene = {"location": "酒肆", "event": "邀约下墟", "emotion": "压抑",
             "anchors": ["碰碗"], "target_words": 1200}
    sys_c, user_c = scene_prompt(cfg, 7, "第七章", 2, scene, "记忆", "尾文", "",
                                 book_pitfalls="【本作历史错误】\n1. 错误模式「酒肆双版本」")
    assert "【本作历史错误】" in user_c
    assert "酒肆双版本" in user_c
    # 空串不注入
    sys_c2, user_c2 = scene_prompt(cfg, 7, "第七章", 2, scene, "记忆", "尾文", "")
    assert "【本作历史错误】" not in user_c2


@pytest.mark.p1
def test_split_scenes_prompt_injects_book_canon(store):
    cfg = store.load_config()
    sys_s, user_s = split_scenes_prompt(cfg, 3, "第三章", "目标", book_canon="【本作设定账本】蚀骨蚰（灰壳毒刺）")
    assert "【本作设定账本】" in user_s
    sys_s2, user_s2 = split_scenes_prompt(cfg, 3, "第三章", "目标")
    assert "【本作设定账本】" not in user_s2


@pytest.mark.p1
def test_chapter_writer_lazy_memory_and_injection(store):
    """ChapterWriter 懒加载 BookMemory：空账本 → 空注入；有 ≥2 次踩坑 → 非空注入。"""
    cfg = store.load_config()
    llm = MagicMock()
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    assert writer._book_pitfalls() == ""      # 空账本不注入
    mem = writer._memory()
    assert mem is not None
    mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "x", "level": "大改"}])
    mem.record_pitfall(6, [{"type": "情节-同事件循环重演", "detail": "y", "level": "大改"}])
    block = writer._book_pitfalls(min_count=2)
    assert "本作历史错误" in block
    assert "同一事件/情节只写一遍" in block
    # 设定账本注入
    mem.ensure_term("蚀骨蚰", "蚀骨蚰（灰壳毒刺）", "生物", 3)
    assert "蚀骨蚰" in writer._book_canon()


@pytest.mark.p1
def test_book_memory_disabled_skips_all(store):
    """book_memory=False → record/注入全跳过（管线行为与 P0-5 一致）。"""
    cfg = store.load_config()
    cfg.book_memory = False
    store.save_config(cfg)
    mem = _memory(store)
    assert mem.record_pitfall(3, [{"type": "情节-同事件循环重演", "detail": "x", "level": "大改"}]) == 0
    assert mem.pitfall_block() == ""
    assert mem.canon_block() == ""
    assert mem.numeric_backfill(["面板「蚀化」应从基线 100 递增，正文出现回退 100→99"]) == ""
    assert mem.record_event_consumed("邀约下墟", 3) is False
    mem.record_taste(3, 70, [{"type": "x"}])   # 不落盘
    assert not (store.dir / "book_memory.json").exists()


# ---------- P0-8（2026-10-10）----------

@pytest.mark.p1
def test_fix_of_priority_rewrite_before_number():
    """循环重演 issue 的 detail 含「面板」时，不得被机制数字分支劫持（v6 两条纪律 rule 错标的根因）。"""
    assert BookMemory._fix_of({
        "type": "情节-同事件循环重演",
        "detail": "同一情节节拍序列「面板→对峙→复活→面板」在章内出现 ≥2 次",
    }) == "同一事件/情节只写一遍，禁止换措辞/换地点/换细节重演完整流程；重复版本删除，不可替代信息合并进保留版本"


@pytest.mark.p1
def test_fix_of_number_rule_unchanged():
    """机制数字 issue 仍归数字结算指令（分支前移不破坏原语义）。"""
    fix = BookMemory._fix_of({
        "type": "写作-机制数字未结算推进",
        "detail": "面板数字「今日可死次数：2」在死亡/结算事件后仍未变化",
    })
    assert "机制数字必须按方向规则严格结算" in fix


@pytest.mark.p1
def test_sync_baseline_writes_canon():
    store = _store()
    mem = _memory(store)
    world = MagicMock()
    world.mechanism_baseline = {"蚀痕": "左手腕内侧起每死一寸，已伸三十七寸距心约一寸",
                                "panel_format": "今日可死次数：3；距离蚀化：97%"}
    mem.sync_baseline(world)
    canon = mem.load()["canon"]["mechanism_baseline"]
    assert canon["蚀痕"].startswith("左手腕内侧")
    assert canon["panel_format"].startswith("今日可死次数：3")
    # 幂等：再次同步不产生脏数据
    mem.sync_baseline(world)
    assert mem.load()["canon"]["mechanism_baseline"]["蚀痕"].startswith("左手腕内侧")
    # 更新值生效
    world.mechanism_baseline["蚀痕"] = "已伸三十八寸"
    mem.sync_baseline(world)
    assert mem.load()["canon"]["mechanism_baseline"]["蚀痕"] == "已伸三十八寸"


@pytest.mark.p1
def test_sync_baseline_skips_empty_and_disabled():
    store = _store()
    mem = _memory(store)
    world = MagicMock()
    world.mechanism_baseline = None
    mem.sync_baseline(world)                       # None → 跳过
    assert mem.load()["canon"]["mechanism_baseline"] == {}
    world.mechanism_baseline = {}
    mem.sync_baseline(world)                       # 空 → 跳过
    assert mem.load()["canon"]["mechanism_baseline"] == {}
    cfg = store.load_config()
    cfg.book_memory = False
    mem2 = BookMemory(store, cfg)
    world.mechanism_baseline = {"蚀痕": "x"}
    mem2.sync_baseline(world)                      # 关闭 → 跳过
    assert mem2.load()["canon"]["mechanism_baseline"] == {}


@pytest.mark.p1
def test_baseline_block_output():
    store = _store()
    mem = _memory(store)
    assert mem.baseline_block() == ""              # 空基线 → 空串
    mem.sync_baseline(MagicMock(mechanism_baseline={"蚀痕": "已伸三十七寸距心约一寸"}))
    blk = mem.baseline_block()
    assert "机制基线" in blk and "蚀痕" in blk and "三十七寸" in blk


def _store():
    """P0-8 独立临时 store（与顶部 fixture 解耦，避免 tmp_path 作用域冲突）。"""
    import tempfile
    from pathlib import Path as _P
    from novel_gen.config import NovelConfig as _NC
    from novel_gen.store.project import ProjectStore as _PS
    cfg = _NC(title="P0-8测试书", chapter_words=2500, total_words=10000)
    return _PS.create(_P(tempfile.mkdtemp()) / "sc", cfg)
