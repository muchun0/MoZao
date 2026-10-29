# -*- coding: utf-8 -*-
"""踩坑账本注入对账核心测试（REQ-踩坑账本注入对账 · T03）。

覆盖对账函数分级矩阵、口径冲突检测、L1u/L0 处理、连续升级、fail_fast 默认关闭、
零 LLM 调用、签名级回归护栏、主开关关闭、体检脚本退出码。

不涉及完整写章链路（见 test_pitfall_injection_unify / test_chapter_flow 的集成覆盖）。
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig, feature_default
from novel_gen.pipeline import pitfall_reconcile as pr
from novel_gen.pipeline.pitfall_contract import (
    CHANNEL_BLOCK, CHANNEL_NONE, CHANNEL_REWRITE, contract_for, expected_params,
)
from novel_gen.pipeline.pitfall_inject import build_injection
from novel_gen.pipeline.book_memory import BookMemory
from novel_gen.pipeline.memory import Memory
from novel_gen.store.project import ProjectStore


# ----------------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------------
class FakeCfg:
    book_memory_top_n = 5
    pitfall_inject_min_count = 2
    pitfall_reconcile = True
    pitfall_reconcile_fail_fast = False
    pitfall_reconcile_streak = 3
    pitfall_reconcile_book_limit = 5


class FakeStore:
    """内存版 ProjectStore，仅实现对账需要的接口。"""

    def __init__(self, project_id="prjX"):
        self.project_id = project_id
        self.records: list[dict] = []

    def load_pitfall_reconcile(self):
        return list(self.records)

    def append_pitfall_reconcile(self, entry):
        self.records.append(dict(entry))

    def pitfall_reconcile_path(self):
        return None


def _hit_block(patterns):
    lines = "\n".join(
        f"{i}. 错误模式「{p}」：fix。本书实例：x（已出现 2 次，最近第 3 章）"
        for i, p in enumerate(patterns, 1)
    )
    return "【本作历史错误·场景生成纪律...】\n" + lines


def _call(expected, params, prompt, path_id, chapter_no=3, cfg=None, store=None, **kw):
    cfg = cfg or FakeCfg()
    store = store or FakeStore()
    return pr.reconcile_pitfall_injection(
        expected=expected, params=params, prompt=prompt,
        path_id=path_id, chapter_no=chapter_no, cfg=cfg, store=store,
        project_id=store.project_id if store else "p", **kw,
    ), store


# ----------------------------------------------------------------------------
# AC-1 全命中 -> info
# ----------------------------------------------------------------------------
def test_ac1_full_hit_is_info():
    store = FakeStore()
    r, _ = _call(["A", "B"], {"top_n": 5, "min_count": 2, "self_ch": 3},
                 "SYS\n" + _hit_block(["A", "B"]), "scene", cfg=FakeCfg(), store=store,
                 inject_channel=CHANNEL_BLOCK)
    assert r["level"] == pr.LEVEL_INFO
    assert r["expected_n"] == 2 and r["actual_distinct"] == 2 and r["actual_total"] == 2
    assert r["missing"] == [] and r["duplicated"] == [] and r["count_mismatch"] is False
    assert r["params_conflict"] is False and r["channel_conflict"] is False


# ----------------------------------------------------------------------------
# AC-2 全未命中 -> L3 error，且不中断、不抛异常
# ----------------------------------------------------------------------------
def test_ac2_full_miss_is_error_and_not_raises():
    store = FakeStore()
    r, _ = _call(["A", "B"], {"top_n": 5, "min_count": 2, "self_ch": 3},
                 "SYS\nUSER 无命中", "scene", cfg=FakeCfg(), store=store,
                 inject_channel=CHANNEL_BLOCK)
    assert r["level"] == pr.LEVEL_ERROR
    assert r["expected_n"] >= 1 and r["actual_distinct"] == 0


# ----------------------------------------------------------------------------
# AC-3 重复命中 -> L2 warning
# ----------------------------------------------------------------------------
def test_ac3_duplicate_is_warning():
    block = _hit_block(["A"]) + "\n1. 错误模式「A」：fix。"  # A 出现 2 次
    r, _ = _call(["A"], {"top_n": 5, "min_count": 2, "self_ch": 3}, "SYS\n" + block,
                 "scene", inject_channel=CHANNEL_BLOCK)
    assert r["level"] == pr.LEVEL_WARNING
    assert r["duplicated"] == [{"pattern": "A", "times": 2}]


# ----------------------------------------------------------------------------
# AC-4 数量不一致 -> L2 warning
# ----------------------------------------------------------------------------
def test_ac4_count_mismatch_is_warning():
    # Expected 5，prompt 中仅 3 条
    exp = ["A", "B", "C", "D", "E"]
    block = _hit_block(["A", "B", "C"])
    r, _ = _call(exp, {"top_n": 5, "min_count": 2, "self_ch": 3}, "SYS\n" + block,
                 "scene", inject_channel=CHANNEL_BLOCK)
    assert r["level"] == pr.LEVEL_WARNING
    assert r["count_mismatch"] is True
    assert len(r["missing"]) == 2 and r["actual_distinct"] == 3


# ----------------------------------------------------------------------------
# AC-5 L0 Skip：expected 为空 -> 不落盘、不抛
# ----------------------------------------------------------------------------
def test_ac5_l0_skip_when_expected_empty():
    store = FakeStore()
    before = len(store.records)
    r, store = _call([], {"top_n": 5, "min_count": 2, "self_ch": 3}, "SYS\nUSER",
                     "scene", store=store, inject_channel=CHANNEL_BLOCK)
    assert r is None
    assert len(store.records) == before


# ----------------------------------------------------------------------------
# AC-6 对账内部异常不拖垮生成（返回 None，不抛）
# ----------------------------------------------------------------------------
def test_ac6_internal_error_is_swallowed():
    store = FakeStore()
    # 触发内部异常：path_id 未知 -> contract_for 抛 KeyError，被外层吞掉
    r, store = _call(["A"], {}, "SYS\nUSER", "no_such_path", store=store)
    assert r is None
    assert len(store.records) == 0


# ----------------------------------------------------------------------------
# AC-7 零额外 LLM 调用：对账模块源码不含 llm 引用
# ----------------------------------------------------------------------------
def test_ac7_no_llm_dependency():
    src = inspect.getsource(pr.reconcile_pitfall_injection).lower()
    assert "llm" not in src
    # 模块层面也不引用 llm
    mod_src = inspect.getsource(pr).lower()
    assert "llm.chat" not in mod_src


# ----------------------------------------------------------------------------
# AC-9 fallback_whole 首要路径：统一后口径 conflict 全 False
# ----------------------------------------------------------------------------
def test_ac9_fallback_whole_unified_no_conflict():
    cfg = FakeCfg()
    contract = contract_for("fallback_whole", cfg, 7)
    params = expected_params(contract)
    r, _ = _call(["A"], params, "SYS\n" + _hit_block(["A"]), "fallback_whole",
                 chapter_no=7, cfg=cfg, inject_channel=CHANNEL_BLOCK)
    assert r["params_conflict"] is False
    assert r["channel_conflict"] is False
    assert r["expected_params"] == r["call_params"]


# ----------------------------------------------------------------------------
# AC-10 两路径口径统一（修复效果的量化出口）
# ----------------------------------------------------------------------------
def test_ac10_two_paths_unified():
    cfg = NovelConfig(title="对账双路径", chapter_words=2500, total_words=10000)
    # 注入侧统一走 build_injection（契约 scene），两路径同源
    mem = BookMemory(Memory(ProjectStore.create(Path("/tmp/pu10"), cfg), 5))
    # 跨章累计才能达 count>=2（同章同 pattern 不累加）
    mem.record_pitfall(1, [{"type": "P1", "detail": "d", "level": "大改"}])
    mem.record_pitfall(2, [{"type": "P1", "detail": "d", "level": "大改"}])
    inj_scene = build_injection(cfg, mem, 3)
    inj_fb = build_injection(cfg, mem, 3)
    assert inj_scene.params == inj_fb.params
    assert inj_scene.patterns == inj_fb.patterns
    # 对账两侧均应为 conflict False
    r1, _ = _call(inj_scene.patterns, inj_scene.params,
                  "SYS\n" + _hit_block(inj_scene.patterns), "scene",
                  cfg=cfg, inject_channel=CHANNEL_BLOCK)
    r2, _ = _call(inj_fb.patterns, inj_fb.params,
                  "SYS\n" + _hit_block(inj_fb.patterns), "fallback_whole",
                  cfg=cfg, inject_channel=CHANNEL_BLOCK)
    assert r1["expected_params"] == r2["expected_params"]
    assert r1["params_conflict"] is False and r2["params_conflict"] is False


# ----------------------------------------------------------------------------
# AC-10b 分裂检测逻辑本身可用（防止"永远 False=检测是死的"）
# ----------------------------------------------------------------------------
def test_ac10b_old_params_detected():
    cfg = FakeCfg()
    # 旧分叉参数 (5,1,None)
    old_params = {"top_n": 5, "min_count": 1, "self_ch": None}
    r, _ = _call(["A"], old_params, "SYS\n" + _hit_block(["A"]), "fallback_whole",
                 cfg=cfg, inject_channel=CHANNEL_BLOCK)
    assert r["params_conflict"] is True


# ----------------------------------------------------------------------------
# AC-11 expand 未接线观察：L1u，每章去重，不计 streak
# ----------------------------------------------------------------------------
def test_ac11_expand_unwired_dedup():
    cfg = FakeCfg()
    store = FakeStore()
    contract = contract_for("expand", cfg, 6)
    params = expected_params(contract)
    r1, store = _call([], params, "SYS\nUSER", "expand", chapter_no=6,
                      cfg=cfg, store=store, inject_channel=CHANNEL_NONE)
    assert r1["level"] == pr.LEVEL_UNWIRED and r1["wired"] is False
    # 同章再次调用 -> 去重，不再落盘
    r2, store = _call([], params, "SYS\nUSER", "expand", chapter_no=6,
                      cfg=cfg, store=store, inject_channel=CHANNEL_NONE)
    assert r2 is None
    assert len(store.records) == 1


# ----------------------------------------------------------------------------
# AC-13 L3 连续 3 章升级：streak 依次为 1/2/3
# ----------------------------------------------------------------------------
def test_ac13_consecutive_l3_streak():
    cfg = FakeCfg()
    store = FakeStore()
    streaks = []
    for ch in (1, 2, 3):
        r, store = _call(["A", "B"], {"top_n": 5, "min_count": 2, "self_ch": ch},
                         "SYS\n无命中", "scene", chapter_no=ch,
                         cfg=cfg, store=store, inject_channel=CHANNEL_BLOCK)
        streaks.append(r["streak"])
    assert streaks == [1, 2, 3]
    assert all(r["level"] == pr.LEVEL_ERROR for r in store.records)


# ----------------------------------------------------------------------------
# AC-15 L1u 不得污染 L3：连续 3 章 expand 不产生 error、streak 恒 0
# ----------------------------------------------------------------------------
def test_ac15_unwired_does_not_raise_l3():
    cfg = FakeCfg()
    store = FakeStore()
    for ch in (1, 2, 3):
        _call([], expected_params(contract_for("expand", cfg, ch)), "SYS\nUSER",
              "expand", chapter_no=ch, cfg=cfg, store=store, inject_channel=CHANNEL_NONE)
    assert all(r["level"] == pr.LEVEL_UNWIRED for r in store.records)
    assert all(r["streak"] == 0 for r in store.records)
    assert not any(r["level"] == pr.LEVEL_ERROR for r in store.records)


# ----------------------------------------------------------------------------
# AC-18 防"第二套参数"：签名不含 memory/top_n/min_count/self_ch
# ----------------------------------------------------------------------------
def test_ac18_signature_excludes_memory_and_params():
    sig = inspect.signature(pr.reconcile_pitfall_injection)
    names = set(sig.parameters)
    assert "memory" not in names
    for forbidden in ("top_n", "min_count", "self_ch"):
        assert forbidden not in names


# ----------------------------------------------------------------------------
# AC-19 输出净化：记录不含 prompt 全文；单行 < 2KB
# ----------------------------------------------------------------------------
def test_ac19_record_no_prompt_and_small():
    store = FakeStore()
    big_prompt = "SYS\n" + _hit_block(["A", "B", "C"]) + "\n" + ("x" * 5000)
    r, store = _call(["A", "B", "C"], {"top_n": 5, "min_count": 2, "self_ch": 3},
                     big_prompt, "scene", store=store, inject_channel=CHANNEL_BLOCK)
    assert "prompt" not in r
    line = json.dumps(store.records[-1], ensure_ascii=False)
    assert len(line.encode("utf-8")) < 2048


# ----------------------------------------------------------------------------
# AC-20 主开关关闭 -> 零变化（不落盘、不抛）
# ----------------------------------------------------------------------------
def test_ac20_master_switch_off():
    cfg = FakeCfg()
    cfg.pitfall_reconcile = False
    store = FakeStore()
    r, store = _call(["A"], {"top_n": 5, "min_count": 2, "self_ch": 3},
                     "SYS\n" + _hit_block(["A"]), "scene", cfg=cfg, store=store,
                     inject_channel=CHANNEL_BLOCK)
    assert r is None
    assert len(store.records) == 0


# ----------------------------------------------------------------------------
# AC-21 chapter_plain 埋了但生产不触发：一旦触发 -> L3（wired + 实际 0 命中）
# ----------------------------------------------------------------------------
def test_ac21_chapter_plain_triggered_is_l3():
    cfg = FakeCfg()
    contract = contract_for("chapter_plain", cfg, 9)
    params = expected_params(contract)
    r, _ = _call(["A"], params, "SYS\nUSER 无命中", "chapter_plain", chapter_no=9,
                 cfg=cfg, inject_channel=CHANNEL_NONE)
    assert r["wired"] is True
    assert r["level"] == pr.LEVEL_ERROR
    assert r["channel_conflict"] is True  # 期望独立段落，实际未注入


# ----------------------------------------------------------------------------
# AC-16 体检脚本：L3 -> 退出码 3；全 info -> 0
# ----------------------------------------------------------------------------
def test_ac16_health_check_exit_codes(tmp_path):
    from tools import pitfall_reconcile_check as checker

    # 构造一个含 L3 的 project
    proj = tmp_path / "proj_l3"
    proj.mkdir()
    store = ProjectStore(proj)
    store.append_pitfall_reconcile({
        "path_id": "scene", "chapter_no": 1, "level": pr.LEVEL_ERROR,
        "expected_n": 2, "actual_distinct": 0, "expected": ["A", "B"],
        "missing": ["A", "B"], "params_conflict": False, "channel_conflict": False,
        "streak": 1, "expected_params": {}, "call_params": {}, "duplicated": [],
        "ghost": [], "count_mismatch": False,
    })
    old = sys.argv
    try:
        sys.argv = ["check", str(proj)]
        assert checker.main() == 3
    finally:
        sys.argv = old

    # 全 info 项目
    proj2 = tmp_path / "proj_ok"
    proj2.mkdir()
    store2 = ProjectStore(proj2)
    store2.append_pitfall_reconcile({
        "path_id": "scene", "chapter_no": 1, "level": pr.LEVEL_INFO,
        "expected_n": 1, "actual_distinct": 1, "expected": ["A"],
        "missing": [], "params_conflict": False, "channel_conflict": False,
        "streak": 0, "expected_params": {}, "call_params": {}, "duplicated": [],
        "ghost": [], "count_mismatch": False,
    })
    try:
        sys.argv = ["check", str(proj2)]
        assert checker.main() == 0
    finally:
        sys.argv = old
