# -*- coding: utf-8 -*-
"""踩坑账本统一事前注入专项测试（REQ-踩坑账本注入对账 · T01 配置与数据底座 / T02 注入统一）。

覆盖：
- T01：5 个新开关注册进 FEATURE_FLAGS 并挂到 NovelConfig（§11.3 不硬编码）
- T01：ProjectStore.pitfall_reconcile 三个方法（读/追加/清空 + 2000 条上限 + 原子写）
- T01：BookMemory.pitfall_injection() 渲染块与 pattern 列表同源；pitfall_block 输出不变
- T02：chapter_prompt 补 book_pitfalls 形参——不传时输出与改造前逐字节一致
- T02：注入参数统一（top_n=cfg.book_memory_top_n / min_count=cfg.pitfall_inject_min_count /
      self_ch=chapter_no）
- T02：回退整章不再把踩坑块拼进 memory_context（走私移除），改走独立形参
- T02：_book_pitfalls() 签名与默认值保持兼容（既有用例直接断言它）
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import FEATURE_FLAGS, NovelConfig, feature_default
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.pitfall_inject import PitfallInjection, build_injection
from novel_gen.pipeline.stages import ChapterWriter
from novel_gen.pipeline.templates import chapter_prompt, scene_prompt
from novel_gen.pipeline.memory import Memory
from novel_gen.store.project import ProjectStore


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", chapter_words=2500, total_words=10000)
    return ProjectStore.create(tmp_path / "piu", cfg)


def _seed(mem: BookMemory, times: int = 2, pattern: str = "情节-同事件循环重演") -> None:
    """制造 count=times 的同模式踩坑（跨章记录，模拟历史教训）。

    注：踩坑账本只记「大改」级 issue，故 level 固定为 大改。
    """
    for i in range(times):
        mem.record_pitfall(3 + i, [{"type": pattern, "detail": "重复", "level": "大改"}])


# =====================================================================
# T01 · 配置与数据底座
# =====================================================================

@pytest.mark.p2
def test_new_flags_registered():
    """5 个新开关均注册进 FEATURE_FLAGS，默认值与 NovelConfig 字段一致。"""
    expected = {
        "pitfall_inject_min_count": 2,
        "pitfall_reconcile": True,
        "pitfall_reconcile_fail_fast": False,
        "pitfall_reconcile_streak": 3,
        "pitfall_reconcile_book_limit": 5,
    }
    by_key = {f.key: f for f in FEATURE_FLAGS}
    cfg = NovelConfig()
    for key, default in expected.items():
        assert key in by_key, f"开关未注册: {key}"
        assert by_key[key].default == default, f"{key} 默认值应为 {default}"
        assert by_key[key].module == "memory", f"{key} 应归属 memory 模块"
        assert feature_default(key) == default
        # §11.3：字段默认值必须引用 feature_default，不硬编码
        assert getattr(cfg, key) == default, f"NovelConfig.{key} 未挂上开关默认值"


@pytest.mark.p2
def test_top_n_reuses_existing_knob():
    """top_n 不新增开关，复用既有 book_memory_top_n（默认 5）——见 TECH §2.2.2。"""
    assert "pitfall_inject_top_n" not in {f.key for f in FEATURE_FLAGS}
    assert feature_default("book_memory_top_n") == 5
    assert NovelConfig().book_memory_top_n == 5


@pytest.mark.p2
def test_new_flags_can_be_overridden(store):
    """新开关能被全局覆盖（写 config.json → load_config 读回）。"""
    cfg = store.load_config()
    cfg.pitfall_inject_min_count = 4
    cfg.pitfall_reconcile = False
    store.save_config(cfg)
    back = store.load_config()
    assert back.pitfall_inject_min_count == 4
    assert back.pitfall_reconcile is False


# ---------- ProjectStore.pitfall_reconcile ----------

@pytest.mark.p2
def test_pitfall_reconcile_store_roundtrip(store):
    """append → load 往返；文件名与路径方法一致。"""
    assert store.pitfall_reconcile_path() == store.dir / "pitfall_reconcile.jsonl"
    assert store.load_pitfall_reconcile() == []
    store.append_pitfall_reconcile({"chapter_no": 1, "path_id": "scene"})
    store.append_pitfall_reconcile({"chapter_no": 2, "path_id": "fallback_whole"})
    got = store.load_pitfall_reconcile()
    assert [g["chapter_no"] for g in got] == [1, 2]
    assert got[1]["path_id"] == "fallback_whole"


@pytest.mark.p2
def test_pitfall_reconcile_cap_2000(store):
    """超出 2000 条按插入顺序淘汰最旧条目（PRD §5.3 / R-T3）。"""
    for i in range(2005):
        store.append_pitfall_reconcile({"n": i})
    got = store.load_pitfall_reconcile()
    assert len(got) == 2000
    assert got[0]["n"] == 5      # 最旧 5 条被淘汰
    assert got[-1]["n"] == 2004  # 最新保留


@pytest.mark.p2
def test_pitfall_reconcile_bad_line_tolerated(store):
    """JSONL 逐行容错：坏行跳过，好行保留。"""
    p = store.pitfall_reconcile_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"a": 1}\nnot json\n{"a": 2}\n', encoding="utf-8")
    got = store.load_pitfall_reconcile()
    assert [g["a"] for g in got] == [1, 2]


# ---------- BookMemory.pitfall_injection ----------

@pytest.mark.p1
def test_pitfall_injection_returns_block_and_patterns(store):
    """返回 (渲染块, pattern 列表)，且每个 pattern 都能在块中扫到（对账命中判据）。"""
    cfg = store.load_config()
    mem = BookMemory(store, cfg)
    assert mem.pitfall_injection()[0] == ""      # 空账本 → 空块
    assert mem.pitfall_injection()[1] == []     # 空账本 → 空 Expected
    _seed(mem, 2)
    block, patterns = mem.pitfall_injection(top_n=5, min_count=2, self_ch=9)
    assert patterns and block
    assert "本作历史错误" in block
    for p in patterns:
        assert f"错误模式「{p}」" in block


@pytest.mark.p1
def test_pitfall_injection_matches_pitfall_block(store):
    """同源保证：pitfall_injection 的块 == pitfall_block 的输出（逐字）。"""
    cfg = store.load_config()
    mem = BookMemory(store, cfg)
    _seed(mem, 3)
    for top_n in (1, 3, 5):
        for mc in (1, 2, 3):
            for sc in (None, 3, 6):
                blk_expected = mem.pitfall_block(top_n=top_n, min_count=mc, self_ch=sc)
                blk, _ = mem.pitfall_injection(top_n=top_n, min_count=mc, self_ch=sc)
                assert blk == blk_expected


@pytest.mark.p1
def test_pitfall_injection_patterns_filtered(store):
    """patterns 已去空串/纯空白，对账侧无需再过滤。"""
    cfg = store.load_config()
    mem = BookMemory(store, cfg)
    _seed(mem, 2)
    data = mem.load()
    data["pitfalls"].append({"pattern": "   ", "fix": "f", "example": "e",
                             "count": 5, "last_ch": 7, "recent_rounds": 1})
    data["pitfalls"].append({"pattern": "", "fix": "f", "example": "e",
                             "count": 5, "last_ch": 7, "recent_rounds": 1})
    _, patterns = mem.pitfall_injection(top_n=10, min_count=1)
    assert all(p.strip() for p in patterns)
    assert "" not in patterns


@pytest.mark.p2
def test_pitfall_injection_disabled():
    """book_memory=False → 空注入（管线行为与关闭前一致）。"""
    cfg = NovelConfig()
    cfg.book_memory = False
    s = ProjectStore.create(Path(ROOT) / "_tmp_piu_disabled", cfg)
    try:
        mem = BookMemory(s, cfg)
        _seed(mem, 3)
        assert mem.pitfall_injection() == ("", [])
    finally:
        import shutil
        shutil.rmtree(Path(ROOT) / "_tmp_piu_disabled", ignore_errors=True)


# =====================================================================
# T02 · 注入统一
# =====================================================================

@pytest.mark.p1
def test_chapter_prompt_backward_compatible(store):
    """chapter_prompt 不传 book_pitfalls 时与改造前逐字节一致（形参带默认值 ""）。"""
    cfg = store.load_config()
    a = chapter_prompt(cfg, 1, "第一章", "目标", "调研", "记忆", "上一章尾")
    b = chapter_prompt(cfg, 1, "第一章", "目标", "调研", "记忆", "上一章尾",
                           book_pitfalls="")
    assert a == b
    assert "错误模式" not in a[1]


@pytest.mark.p1
def test_chapter_prompt_renders_book_pitfalls(store):
    """传入 book_pitfalls 时渲染为独立段落（镜像 scene_prompt 的通道）。"""
    cfg = store.load_config()
    _, user = chapter_prompt(cfg, 1, "第一章", "目标", "", "记忆", "",
                             book_pitfalls="【本作历史错误】X：修。")
    assert "【本作历史错误】X：修。" in user


@pytest.mark.p1
def test_chapter_prompt_pitfalls_in_both_alignment_branches(store):
    """两条组装分支（prefix_cache_aligned True / False）都必须渲染踩坑块。

    回归点：`chapter_prompt` 内部有 aligned / 非 aligned 两段 user 拼装，两处缩进不同，
    只改一处会导致 `prefix_cache_aligned=False` 时注入块静默丢失。
    """
    cfg = store.load_config()
    for aligned in (True, False):
        cfg.prefix_cache_aligned = aligned
        _, user = chapter_prompt(cfg, 1, "第一章", "目标", "", "记忆", "",
                                 book_pitfalls="【本作历史错误】X：修。")
        assert "【本作历史错误】X：修。" in user, f"aligned={aligned} 分支未渲染踩坑块"


@pytest.mark.p1
def test_chapter_prompt_book_pitfalls_is_appended_with_default():
    """book_pitfalls 追加在形参表末尾且默认值为 ""（对既有调用零影响）。"""
    sig = inspect.signature(chapter_prompt)
    names = list(sig.parameters)
    assert names[-1] == "book_pitfalls"
    assert sig.parameters["book_pitfalls"].default == ""


@pytest.mark.p1
def test_chapter_and_scene_pitfall_channel_isomorphic(store):
    """两条路径的踩坑文本形态一致：同一段 pitfall_block 文本，同样的独立段落包裹。"""
    cfg = store.load_config()
    mem = BookMemory(store, cfg)
    _seed(mem, 2)
    block, _ = mem.pitfall_injection(top_n=5, min_count=2, self_ch=9)

    _, user_ch = chapter_prompt(cfg, 1, "第一章", "目标", "", "记忆", "",
                                book_pitfalls=block)
    scene = {"location": "墟口", "event": "e", "emotion": "紧张",
             "anchors": [], "target_words": 1200}
    _, user_sc = scene_prompt(cfg, 1, "第一章", 1, scene, "记忆", "", "",
                              book_pitfalls=block)
    # 同一段块文本在两条路径中都完整出现，且包裹方式一致（前后各一个换行）
    assert block in user_ch and block in user_sc
    assert f"\n{block}\n" in user_ch and f"\n{block}\n" in user_sc


@pytest.mark.p1
def test_build_injection_unified_params(store):
    """统一取值：top_n=cfg.book_memory_top_n、min_count=cfg.pitfall_inject_min_count、
    self_ch=chapter_no。"""
    cfg = store.load_config()
    mem = BookMemory(store, cfg)
    inj = build_injection(cfg, mem, 9)
    assert inj.params == {"top_n": cfg.book_memory_top_n,
                          "min_count": cfg.pitfall_inject_min_count,
                          "self_ch": 9}

    # top_n 旋钮生效：调小后注入条数随之变化（改造前场景路径硬编码 3 完全无视它）
    _seed(mem, 2)
    _seed(mem, 2, pattern="面板-数字方向错误")
    _seed(mem, 2, pattern="对白-风格不一致")
    big = build_injection(cfg, mem, 9)
    assert len(big.patterns) == 3

    cfg.book_memory_top_n = 1
    assert len(build_injection(cfg, mem, 9).patterns) == 1
    cfg.book_memory_top_n = 5
    assert len(build_injection(cfg, mem, 9).patterns) == 3

    # min_count 默认 2：count==1 的低频尾部不进 Expected
    cfg.pitfall_inject_min_count = 2
    pats = build_injection(cfg, mem, 9).patterns
    assert pats  # 至少有 count>=2 的条目


@pytest.mark.p1
def test_build_injection_memory_none():
    """memory 为 None → 空注入但 params 仍完整（对账可判定 L0 Skip）。"""
    cfg = NovelConfig()
    inj = build_injection(cfg, None, 4)
    assert inj.empty() and inj.block == "" and inj.patterns == []
    assert inj.params == {"top_n": 5, "min_count": 2, "self_ch": 4}


@pytest.mark.p1
def test_book_pitfalls_signature_unchanged():
    """_book_pitfalls 签名与默认值不得变更（tests/test_book_memory.py:384/389 依赖）。"""
    sig = inspect.signature(ChapterWriter._book_pitfalls)
    p = sig.parameters
    assert p["top_n"].default == 3
    assert p["min_count"].default == 2
    assert p["self_ch"].default is None
    assert list(p) == ["self", "top_n", "min_count", "self_ch"]


@pytest.mark.p1
def test_downstream_methods_have_defaults():
    """_generate_chapter_scenes / _fallback_whole_chapter 的新形参必须给默认值
    （既有测试会直接调用它们）。"""
    g = inspect.signature(ChapterWriter._generate_chapter_scenes).parameters
    f = inspect.signature(ChapterWriter._fallback_whole_chapter).parameters
    assert g["pitfall"].default is None
    assert f["pitfall"].default is None


@pytest.mark.p1
def test_fallback_whole_chapter_no_smuggling(store):
    """回退整章不再把踩坑块拼进 memory_context：memory_context 原样传给
    chapter_prompt 的 memory_context 形参，踩坑块走独立的 book_pitfalls 形参。"""
    from unittest.mock import MagicMock, patch

    cfg = store.load_config()
    llm = MagicMock()
    llm.chat.return_value = "正文内容\n【摘要】摘要"
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    mem = writer._memory()
    _seed(mem, 2)

    sentinel = "前情记忆哨兵文本"
    captured: dict = {}

    def _fake_chapter_prompt(_cfg, **kwargs):
        captured.update(kwargs)
        return "SYS", "USER"

    with patch("novel_gen.pipeline.templates.chapter_prompt", _fake_chapter_prompt):
        writer._fallback_whole_chapter(
            1, "第一章", "目标", sentinel, "尾文", False, "", [],
        )

    # 走私已移除：memory_context 保持原样，未被追加踩坑块
    assert captured["memory_context"] == sentinel
    # 踩坑块改为走独立形参
    assert "本作历史错误" in captured["book_pitfalls"]
    assert captured["book_pitfalls"] == build_injection(cfg, mem, 1).block


@pytest.mark.p1
def test_write_body_single_decision_point(store):
    """_write_body 只算一次注入，并透传给 _generate_chapter_scenes。"""
    from unittest.mock import MagicMock, patch

    cfg = store.load_config()
    llm = MagicMock()
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    _seed(writer._memory(), 2)

    seen: list = []

    def _fake_scenes(self_, chapter_no, *a, **kw):
        seen.append(kw.get("pitfall"))
        return "正文\n【摘要】摘要", []

    with patch.object(ChapterWriter, "_generate_chapter_scenes", _fake_scenes):
        writer._write_body(1, "第一章", "目标", None, False, True)

    assert len(seen) == 1
    assert isinstance(seen[0], PitfallInjection)
    assert seen[0].patterns
    assert seen[0].params["self_ch"] == 1


@pytest.mark.p1
def test_generate_chapter_scenes_falls_back_when_not_passed(store):
    """未透传 pitfall 时（既有测试直接调用）内部自建，行为不回归。"""
    from unittest.mock import MagicMock, patch

    cfg = store.load_config()
    llm = MagicMock()
    writer = ChapterWriter(cfg, llm, store, Memory(store, 5))
    _seed(writer._memory(), 2)

    captured: dict = {}

    def _fake_scene_prompt(_cfg, *a, **kw):
        captured.update(kw)
        return "SYS", "USER"

    with patch("novel_gen.pipeline.templates.scene_prompt", _fake_scene_prompt), \
         patch.object(ChapterWriter, "_split_scenes",
                      lambda self_, *a, **kw: ([{"location": "x", "event": "e",
                                                 "emotion": "紧张", "anchors": [],
                                                 "target_words": 1200}], [], [], "")):
        try:
            writer._generate_chapter_scenes(1, "第一章", "目标", "记忆", "尾文", False)
        except Exception:
            pass

    assert captured.get("book_pitfalls")


@pytest.mark.p2
def test_pitfall_injection_module_has_no_llm_dep():
    """新模块禁止引用 llm（零额外 LLM 调用，共享约定 §8.7）。"""
    src = Path(__file__).resolve().parent.parent / "novel_gen" / "pipeline" / "pitfall_inject.py"
    text = src.read_text(encoding="utf-8")
    assert "from ..llm" not in text and "import llm" not in text
