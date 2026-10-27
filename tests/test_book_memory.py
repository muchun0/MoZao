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


@pytest.mark.p1
def test_top_pitfalls_self_ch_depollution(store):
    """P3 去污染：self_ch=当前重写章 时，last_ch==self_ch 的本轮错误降权到尾部，
    让 Top-N 优先取跨章真历史教训，不把本章刚犯的错当历史教训回灌。"""
    mem = _memory(store)
    # 跨章真历史教训（count=5，last_ch=4）
    for ch in (2, 3, 4):
        mem.record_pitfall(ch, [{"type": "情节-同事件循环重演", "detail": "重演", "level": "大改"}])
    mem.record_pitfall(5, [{"type": "情节-同事件循环重演", "detail": "重演", "level": "大改"}])
    # 第 1 章本轮刚犯的错（count=12，last_ch=1，recent_rounds=12）——污染源
    for _ in range(1):  # 同章多轮只累加一次 recent；这里补 11 轮模拟 high recent
        pass
    mem.record_pitfall(1, [{"type": "写作-机制数字方向违规", "detail": "回升", "level": "大改"}])
    mem.record_pitfall(1, [{"type": "自相矛盾-事件重复", "detail": "两版本", "level": "大改"}])
    mem.record_pitfall(1, [{"type": "自相矛盾-身体状态/行为模式", "detail": "矛盾", "level": "大改"}])
    # 直接改 last_ch=1 的 recent_rounds 模拟高频率（同章多轮回灌的真实形态）
    for p in mem.load()["pitfalls"]:
        if p["pattern"] != "情节-同事件循环重演":
            p["recent_rounds"] = 12
    mem.save()

    # 无 self_ch：last_ch=1 的污染源（高 count）排前
    tops = mem.top_pitfalls(top_n=3, min_count=1)
    pats = [p["pattern"] for p in tops]
    assert "写作-机制数字方向违规" in pats[:3]      # last_ch=1 的高 count 排前

    # self_ch=1：跨章真教训（last_ch=4 的循环重演）升到首位，
    # 第1章本轮错误（机制数字方向违规）不再排第一——降权而非剔除
    tops2 = mem.top_pitfalls(top_n=3, min_count=1, self_ch=1)
    assert tops2[0]["pattern"] == "情节-同事件循环重演"   # 跨章教训升顶
    assert tops2[0]["last_ch"] == 5
    # 机制数字方向违规（last_ch=1）不再是注入第一条
    assert not (tops2[0]["pattern"] == "写作-机制数字方向违规" and tops2[0]["last_ch"] == 1)

    # pitfall_block 透传生效：头部第一条是跨章教训，不含「最近第 1 章」
    pb = mem.pitfall_block(top_n=3, min_count=1, self_ch=1)
    assert "情节-同事件循环重演" in pb
    assert "最近第 1 章" not in pb.splitlines()[1]      # 第1行注入条目非本轮错误


@pytest.mark.p1
def test_top_pitfalls_self_ch_none_keeps_original(store):
    """self_ch=None（不指定当前章）→ 行为与改动前完全一致（向后兼容）。"""
    mem = _memory(store)
    mem.record_pitfall(2, [{"type": "A类", "detail": "x", "level": "大改"}])
    mem.record_pitfall(4, [{"type": "B类", "detail": "y", "level": "大改"}])
    tops = mem.top_pitfalls(top_n=2, min_count=1, self_ch=None)
    # 与不带 self_ch 的调用等序（向后兼容）
    assert [p["pattern"] for p in tops] == [p["pattern"] for p in mem.top_pitfalls(2, 1)]


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
    cfg = store.load_config()
    cfg.auto_rewrite_rounds = 3  # 显式开启自动重写（默认 0=不重写，一次一版）
    qc = QualityChecker(cfg, llm, store, trace=None, guard=None, memory=mem)
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


def test_chapter_writer_good_block_injection(store):
    """三期 REQ5.6：ChapterWriter 的 BookMemory 在 good 账本入账后 good_block 非空
    （write() memory_context 组装调用 good_block，场景/整章两路径注入）。"""
    cfg = store.load_config()
    llm = MagicMock()
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    mem = writer._memory()
    assert mem.good_block() == ""      # 空账本不注入
    mem.record_good_manual(1, _good_text())
    block = mem.good_block(top_n=2)
    assert "写得好的片段" in block
    assert "参考质感" in block
    assert "- 第1章" in block
    assert "不是内容来源" in block


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


# ---------- P0-10 机制状态账本（2026-10-10 混合方案④） ----------

def _obj_baseline():
    """真实 World 的 mechanism_baseline 形态（pydantic 对象，非 dict）。"""
    from novel_gen.store.schemas import MechanismBaseline
    return MechanismBaseline(
        scar_start="左手腕内侧",
        scar_per_death="每死一次延伸约一寸",
        scar_current="已延伸三十七寸（从手腕到肘弯，距心脏约一寸）",
        scar_end="蚀痕延伸至心脏即彻底异化，不可逆",
        panel_format="今日可死次数：3；距离蚀化：97%",
        reset_rule="每日子时重置'今日可死次数'为3；'距离蚀化'永久累计不重置",
    )


@pytest.mark.p1
def test_sync_baseline_object_model():
    """P0-10 修复实证：world.mechanism_baseline 是对象（非 dict）时也必须同步进 canon。
    此前 isinstance(wb, dict) 恒 False → 基线从未入账（v7 蚀化跨章口径冲突的根因之一）。"""
    store = _store()
    mem = _memory(store)
    world = MagicMock()
    world.mechanism_baseline = _obj_baseline()
    mem.sync_baseline(world)
    canon = mem.load()["canon"]["mechanism_baseline"]
    assert canon["panel_format"].startswith("今日可死次数：3")
    assert "scar_current" in canon and "已延伸三十七寸" in canon["scar_current"]


@pytest.mark.p1
def test_record_chapter_state_and_cumulative():
    """章末机制状态入账 + 累计死亡（代码求和，零 LLM）。"""
    store = _store()
    mem = _memory(store)
    mem.sync_baseline(MagicMock(mechanism_baseline=_obj_baseline()))
    mem.record_chapter_state(1, deaths=1, erosion=98)
    mem.record_chapter_state(2, deaths=0)
    mem.record_chapter_state(3, deaths=1, erosion=100)
    assert mem.cumulative_deaths(2) == 1
    assert mem.cumulative_deaths(3) == 2
    st = mem.load()["state"]
    assert st["erosion_start"] == 97
    # 同章覆盖不重复计
    mem.record_chapter_state(3, deaths=1)
    assert len([c for c in st["chapters"] if c["ch"] == 3]) == 1


@pytest.mark.p1
def test_erosion_anchor_deterministic():
    """确定性数字锚（P0-14 链式）：累积字段 = 上一章末值承接；机制累积基线注入原文口径。"""
    store = _store()
    mem = _memory(store)
    mem.sync_baseline(MagicMock(mechanism_baseline=_obj_baseline()))
    mem.record_chapter_state(1, deaths=1, erosion=98)
    mem.record_chapter_state(3, deaths=1, erosion=100)
    mem.record_chapter_state(4, deaths=1, erosion=101)
    anchor = mem.erosion_anchor(6)
    assert "101%" in anchor and "上一章末值" in anchor   # 链式：取 ≤6 最近一章末值
    assert "97%" not in anchor
    # 字段名从基线 panel_format 声明提取（数据驱动）；累积基线注入基线原文
    assert "距离蚀化" in anchor
    assert "机制累积基线" in anchor and "已延伸三十七寸" in anchor
    # 无蚀化记录的中途章不打断链（ch5 无记录 → 仍取 ch4 的 101）
    mem.record_chapter_state(5, deaths=0)
    anchor2 = mem.erosion_anchor(6)
    assert "101%" in anchor2
    # 全新 store（无 state 账本、无基线）→ 空串不注入
    store2 = _store()
    mem2 = _memory(store2)
    assert mem2.erosion_anchor(6) == ""


@pytest.mark.p1
def test_parse_cn_number():
    from novel_gen.pipeline.book_memory import _parse_cn_number
    assert _parse_cn_number("已伸三十七寸") == 37
    assert _parse_cn_number("已伸二十寸") == 20
    assert _parse_cn_number("已伸十寸") == 10
    assert _parse_cn_number("37寸") == 37
    assert _parse_cn_number("无数字") == 0
    assert _parse_cn_number("") == 0


# ---------- REQ-写作正向引导 5.6：good 账本（正向样例双账本） ----------

def _good_text():
    return (
        "他下颌绷紧，手背青筋一点点鼓了起来，一个字都没回。\n"
        "他怕了。他认得这种感觉——上次腿被打断那夜，冷也是这么冷。\n"
        "“你在撒谎。”她没看他，指节在桌面上敲了三下。\n"
        "油灯把影子拉得很长，铁匠铺的灰落了一层，风从门缝里钻进来，"
        "带着潮锈的气味。\n"
        "他掀开闸门，扑进烟尘，一把攥住那截铁链，猛地往回拽。"
    )


def test_record_good_high_score_chapter(store):
    mem = _memory(store)
    n = mem.record_good(1, _good_text(), 90, [])
    assert n > 0
    samples = mem.load()["goods"]["samples"]
    assert len(samples) == n
    # 各类都有分类（对话-潜台词 / 情绪-白描+点破 / 环境-物象白描 / 战斗-动作链）
    cats = {s["cat"] for s in samples}
    assert "对话-潜台词" in cats
    assert "情绪-白描+点破" in cats
    assert "战斗-动作链" in cats
    assert all(int(s["src_ch"]) == 1 for s in samples)


def test_record_good_pool_layer_ingest(store):
    """P0-4 分层入账（good_pool_layer 开）：记录层无条件沉淀 + 注入层 demo/cand 分层。
    - score 84（<85）无大改 → 入 cand（非 demo）；
    - score 90 无大改 → 入 demo；
    - 有大改（任意 score）→ 一律不入账。
    """
    mem = _memory(store)
    n1 = mem.record_good(1, _good_text(), 84, [])
    assert n1 > 0  # 84<85 无大改 → 通过即入账（P0-4 记录层无条件沉淀）
    s1 = mem.load()["goods"]["samples"]
    assert s1 and all(s["level"] == "cand" and s["src_type"] == "book" for s in s1)
    assert mem.record_good(2, _good_text(), 90, []) > 0
    assert mem.record_good(3, _good_text(), 90,
                           [{"type": "机制数字-方向违规", "level": "大改"}]) == 0
    samples = mem.load()["goods"]["samples"]
    assert len(samples) > 0
    # 第3章（大改）未入账；demo 池仅第2章（90分）、cand 池仅第1章（84分）
    assert all(int(s["src_ch"]) in (1, 2) for s in samples)
    demo = [s for s in samples if s["level"] == "demo"]
    cand = [s for s in samples if s["level"] == "cand"]
    assert demo and cand
    assert all(int(s["src_ch"]) == 2 for s in demo)
    assert all(int(s["src_ch"]) == 1 for s in cand)


def test_record_good_per_chapter_dedupe_and_capacity(store):
    mem = _memory(store)
    # 同章同类型只入 1 条
    assert mem.record_good(1, _good_text(), 90, []) > 0
    n1 = len(mem.load()["goods"]["samples"])
    mem.record_good(1, _good_text(), 95, [])
    assert len(mem.load()["goods"]["samples"]) == n1
    # 每类容量 ≤5：连喂 10 章同类型，该类仍 ≤5
    for ch in range(2, 12):
        mem.record_good(ch, _good_text(), 90, [])
    samples = mem.load()["goods"]["samples"]
    from collections import Counter
    counts = Counter(s["cat"] for s in samples)
    assert all(v <= 5 for v in counts.values())
    assert len(samples) <= 50


def test_good_block_empty_and_injected(store):
    mem = _memory(store)
    assert mem.good_block() == ""
    mem.record_good(1, _good_text(), 90, [])
    block = mem.good_block(top_n=2)
    assert "写得好的片段" in block
    assert "参考质感" in block
    assert block.count("- 第1章") <= 2


def test_good_block_disabled(store):
    cfg = store.load_config()
    cfg.book_memory = False
    mem = BookMemory(store, cfg)
    mem.record_good(1, _good_text(), 90, [])
    assert mem.good_block() == ""


def test_record_good_manual_no_score_condition(store):
    mem = _memory(store)
    # 用户人工修订：免质检高分条件直接入账（用户认可 = 好文风）
    assert mem.record_good_manual(3, _good_text()) > 0
    samples = mem.load()["goods"]["samples"]
    assert all(int(s["src_ch"]) == 3 for s in samples)
    assert all(int(s["score"]) == 100 for s in samples)
    # 短段落（<20 字）不入账
    assert mem.record_good_manual(4, "他走了。") == 0


@pytest.mark.p1
def test_rewrite_whole_injects_good_block(store):
    """第二点：重写路径（_rewrite_whole → _memory_block）叠加 good 正向质感引导。
    重写不只「避免什么」，还要「按什么质感写」——good 账本有值必须注入。"""
    mem = _memory(store)
    mem.record_good(1, _good_text(), 90, [])
    llm = MagicMock()
    llm.chat.return_value = _LONG_OK_TEXT
    qc = _qc(store, llm, memory=mem)
    qc._rewrite_whole(7, "第七章", "目标", "旧正文。", [])
    prompt = llm.chat.call_args[0][1]
    assert "写得好的片段" in prompt
    assert "参考质感" in prompt
    assert "不是内容来源" in prompt


@pytest.mark.p1
def test_rewrite_whole_pitfall_self_ch_depollution(store):
    """第二/三点联动：重写第1章（self_ch=1）时，pitfall 注入把本章刚犯的错
    降权到尾部，Top-N 取跨章真教训；而 good 正向引导正常注入。"""
    mem = _memory(store)
    # 跨章真教训（last_ch=4）
    for ch in (2, 3, 4):
        mem.record_pitfall(ch, [{"type": "情节-同事件循环重演", "detail": "重演", "level": "大改"}])
    # 第1章本轮刚犯的错（last_ch=1，污染源）
    mem.record_pitfall(1, [{"type": "写作-机制数字方向违规", "detail": "回升", "level": "大改"}])
    mem.record_pitfall(1, [{"type": "自相矛盾-事件重复", "detail": "两版本", "level": "大改"}])
    mem.record_good(1, _good_text(), 90, [])
    llm = MagicMock()
    llm.chat.return_value = _LONG_OK_TEXT
    qc = _qc(store, llm, memory=mem)
    qc._rewrite_whole(1, "第一章", "目标", "旧正文。", [])
    prompt = llm.chat.call_args[0][1]
    # 跨章真教训进注入
    assert "情节-同事件循环重演" in prompt
    # 本章刚犯的错（机制数字方向违规）被降权，不再作为注入的第一条错误
    mem_block = prompt[prompt.find("【本作历史错误"):]
    first_line = mem_block.splitlines()[1]
    assert "机制数字方向违规" not in first_line
    assert "情节-同事件循环重演" in first_line
    # good 正向引导照常注入
    assert "写得好的片段" in prompt


