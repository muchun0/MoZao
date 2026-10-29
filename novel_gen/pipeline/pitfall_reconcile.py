# -*- coding: utf-8 -*-
"""踩坑账本「写入 ↔ 注入命中」对账（REQ-踩坑账本注入对账 · 第 3 步）。

设计铁律（REQ §3.4 / §8.2）
---------------------------
本函数**不接收 `memory`、也不接收 `top_n` / `min_count` / `self_ch`**。Expected 只能由
注入侧算好后通过 `expected` / `params` 传入——函数物理上拿不到账本、也拿不到取样参数，
因而无法自算第二套事实来源。这正是把「第二套参数」从**类型层面**堵死，而非依赖注释自觉。

对账看的是「账本 Top-N 是否真的出现在最终 prompt 文本里」，不验证模型是否遵守纪律
（那属质检链路，不在本需求范围）。

分级策略（REQ §4.3，默认非阻断）
---------------------------------
- L0 Skip：本就不该注入（memory 关 / expected 为空）→ 不落盘、不打 warning。
- L1u 未接线：契约声明 `wired == false`（本期 `expand`）→ 每章 1 条 warning + 落盘（每章去重），
  不计 streak、不进 fail_fast。区分「登记在案的缺口」与「接线突然失效」。
- L1 Info：wired 且全命中 → logger.info + 落盘（留证据链）。
- L2 Warning：部分未命中 / 重复命中 / 数量不一致 / **`params_conflict` / `channel_conflict` 命中即 L2**（REQ §3.6，team-lead 已决）→ logger.warning + 落盘；连续 3 次升 L3。
- L3 Error：expected≥1 但实际命中 0（一条都没进 prompt）→ logger.error + 落盘 + 累计计数；
  同 project 连续 3 章或单书累计 5 章，且 `pitfall_reconcile_fail_fast=True` 才抛异常中断。

约束（REQ §5.3）
----------------
- 零额外 LLM 调用（比对是 N 次 `str.count`，N ≤ 8，微秒级）。
- 任何异常 `logger.exception` 后返回 `None`，绝不上抛（AGENTS.md §11.6）。
- 落盘复用 `ProjectStore.append_pitfall_reconcile`（原子写，逐字复用 `append_projection_log`）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .pitfall_contract import (
    CHANNEL_BLOCK,
    CHANNEL_NONE,
    CHANNEL_REWRITE,
    contract_for,
    expected_params,
)

logger = logging.getLogger(__name__)

__all__ = [
    "reconcile_pitfall_injection",
    "PitfallReconcileError",
    "LEVEL_SKIP",
    "LEVEL_UNWIRED",
    "LEVEL_INFO",
    "LEVEL_WARNING",
    "LEVEL_ERROR",
]

# 渲染特征串（与 BookMemory._render_pitfalls 的逐行格式同构：
# 「{i}. 错误模式「<pattern>」：<fix>。本书实例：...」）
_HIT_PREFIX = "错误模式「"
_HIT_SUFFIX = "」"

LEVEL_SKIP = "skip"
LEVEL_UNWIRED = "unwired"
LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"


class PitfallReconcileError(Exception):
    """fail_fast 开启且达阈值时抛出（REQ §4.4）。

    抛出前本模块**已先把本次明细落盘**（先盘点后抛，绝不销毁现场）。
    生成线程捕获后应将任务置 failed，不落章节版本、不结算。
    """


def _count_hits(prompt: str, patterns: list[str]) -> tuple[dict, int, int, list[str]]:
    """扫描最终 prompt，返回 (每 pattern 命中次数, distinct, total, ghost 列表)。

    - 命中判定用字面 `str.count`，不使用正则（REQ R-T1：pattern 可能含「（）【】/」等元字符）。
    - ghost：prompt 中出现、但不在 expected 内的 pattern（另一调用点/另一组参数注入的证据）。
    """
    per: dict[str, int] = {}
    for p in patterns:
        pp = (p or "").strip()
        if not pp:
            continue
        needle = f"{_HIT_PREFIX}{pp}{_HIT_SUFFIX}"
        per[pp] = prompt.count(needle)

    distinct = sum(1 for c in per.values() if c > 0)
    total = sum(per.values())

    # ghost：抽取 prompt 中全部「错误模式「...」」片段，挑出不在 expected 内的
    expected_set = set(patterns)
    ghosts: list[str] = []
    start = 0
    while True:
        i = prompt.find(_HIT_PREFIX, start)
        if i < 0:
            break
        j = prompt.find(_HIT_SUFFIX, i + len(_HIT_PREFIX))
        if j < 0:
            break
        pat = prompt[i + len(_HIT_PREFIX):j].strip()
        if pat and pat not in expected_set and pat not in ghosts:
            ghosts.append(pat)
        start = j + len(_HIT_SUFFIX)

    return per, distinct, total, ghosts


def _trailing_consecutive_chapters(
    records: list[dict], project_id: str, path_id: str, level: str, chapter_no: int
) -> int:
    """统计同一 project + path_id + level 的「连续章号（以 chapter_no 结尾）」的条数（含当前章）。

    用于 L3「连续 3 章」阈值（按章去重，避免场景重试多次计数）。
    返回含当前章在内的连续运行长度：若 prior 中已有 ch-1, ch-2… 连续命中，则当前 = 1 + 连续前置。
    """
    chaps = {
        r.get("chapter_no")
        for r in records
        if r.get("project_id") == project_id
        and r.get("path_id") == path_id
        and r.get("level") == level
    }
    cnt = 1  # 当前章本身
    c = chapter_no - 1
    while c in chaps:
        cnt += 1
        c -= 1
    return cnt


def _trailing_consecutive_records(
    records: list[dict], project_id: str, path_id: str, level: str
) -> int:
    """统计 jsonl 末尾同一 project + path_id + level 的连续记录条数（按追加顺序）。

    用于 L2「连续 3 次」升 L3 判定（含场景重试，按次计数）。
    """
    cnt = 0
    for r in reversed(records):
        if (
            r.get("project_id") == project_id
            and r.get("path_id") == path_id
            and r.get("level") == level
        ):
            cnt += 1
        else:
            break
    return cnt


def _thresholds(cfg: Any) -> tuple[int, int, bool]:
    if cfg is not None:
        streak = int(getattr(cfg, "pitfall_reconcile_streak", 3) or 3)
        book_limit = int(getattr(cfg, "pitfall_reconcile_book_limit", 5) or 5)
        fail_fast = bool(getattr(cfg, "pitfall_reconcile_fail_fast", False))
    else:  # 兜底：直接读注册表默认值
        from ..config import feature_default

        streak = int(feature_default("pitfall_reconcile_streak") or 3)
        book_limit = int(feature_default("pitfall_reconcile_book_limit") or 5)
        fail_fast = bool(feature_default("pitfall_reconcile_fail_fast") or False)
    return streak, book_limit, fail_fast


def reconcile_pitfall_injection(
    *,
    expected: list[str],
    params: dict,
    prompt: str,
    path_id: str,
    chapter_no: int,
    cfg: Any = None,
    store: Any = None,
    project_id: str = "",
    scene_idx: Optional[int] = None,
    retry_round: int = 0,
    inject_channel: Optional[str] = None,
    trace: Any = None,
) -> Optional[dict]:
    """踩坑账本注入对账的唯一入口（单一实现，全项目只此一份）。

    Args:
        expected: 注入侧本次**实际选中**的 pattern 列表（来自 `BookMemory.pitfall_injection`）。
        params: 注入侧本次**实际使用的取样参数** ``{"top_n", "min_count", "self_ch"}``。
        prompt: 最终 ``system + "\\n" + user`` 拼装完成、且经过全部后处理之后的文本。
        path_id: 路径枚举（见 pitfall_contract.PATH_IDS）。
        chapter_no: 当前章号。
        cfg: `NovelConfig`（读 4 个对账开关）；为 None 时退回 `feature_default`。
        store: `ProjectStore`（落盘 jsonl）；为 None 时只告警不落盘。
        project_id: 项目 ID（落盘字段，取自 `store.project_id`）。
        scene_idx / retry_round: 场景路径定位（每场景多轮重试各一次）。
        inject_channel: 本次实际使用的注入通道（CHANNEL_*）；不传则取契约期望通道。
        trace: 可选的 `TraceRecorder`（L2/L3 旁路留痕，尽力而为）。

    Returns:
        本次对账记录 dict；以下情况返回 `None`（不落盘）：主开关关闭、契约未知、
        本就不该注入（L0）、或内部异常。异常绝不上抛。
    """
    try:
        return _reconcile_impl(
            expected=expected,
            params=params,
            prompt=prompt,
            path_id=path_id,
            chapter_no=chapter_no,
            cfg=cfg,
            store=store,
            project_id=project_id,
            scene_idx=scene_idx,
            retry_round=retry_round,
            inject_channel=inject_channel,
            trace=trace,
        )
    except PitfallReconcileError:
        # fail_fast 信号：不吞，交上层生成线程捕获（任务置 failed，不落版本/不结算）
        raise
    except Exception:
        logger.exception("踩坑注入对账异常（降级：不落盘、不阻断生成）")
        return None


def _reconcile_impl(
    *,
    expected: list[str],
    params: dict,
    prompt: str,
    path_id: str,
    chapter_no: int,
    cfg: Any,
    store: Any,
    project_id: str,
    scene_idx: Optional[int],
    retry_round: int,
    inject_channel: Optional[str],
    trace: Any,
) -> Optional[dict]:
    # 主开关：关闭时与改造前逐字节一致（不调用即零成本，这里再兜一层）
    if cfg is not None and not getattr(cfg, "pitfall_reconcile", True):
        return None

    # 契约：未知 path_id 视为调用点未登记，跳过（禁止自行传参）
    try:
        contract = contract_for(path_id, cfg, chapter_no)
    except KeyError as e:
        logger.warning("踩坑对账跳过未知 path_id=%r：%s", path_id, e)
        return None

    wired = bool(contract.get("wired"))
    exp_params = expected_params(contract)
    exp_channel = contract.get("channel")

    expected_patterns = [str(p).strip() for p in (expected or []) if str(p).strip()]
    expected_n = len(expected_patterns)

    streak_threshold, book_limit, fail_fast = _thresholds(cfg)

    # ── L1u 未接线：只观测不补注入，每章去重 ──
    if not wired:
        return _record_unwired(
            store=store, project_id=project_id, path_id=path_id,
            chapter_no=chapter_no, scene_idx=scene_idx, retry_round=retry_round,
            exp_params=exp_params, exp_channel=exp_channel,
            call_params=dict(params or {}), trace=trace,
        )

    # ── L0 Skip：本就该注入为空（新书前几章必然命中） ──
    if expected_n == 0:
        return None

    # ── 实际扫描 ──
    per, distinct, total, ghosts = _count_hits(prompt, expected_patterns)
    missing = [p for p, c in per.items() if c == 0]
    duplicated = [{"pattern": p, "times": c} for p, c in per.items() if c >= 2]
    count_mismatch = (expected_n != distinct) or (expected_n != total)

    call_params = dict(params or {})
    params_conflict = call_params != exp_params
    inj_channel = inject_channel if inject_channel is not None else exp_channel
    channel_conflict = inj_channel != exp_channel

    # ── 等级判定（按序，命中即停） ──
    if expected_n >= 1 and distinct == 0:
        level = LEVEL_ERROR
    elif (duplicated or (0 < len(missing) < expected_n) or (count_mismatch and distinct >= 1)
          or params_conflict or channel_conflict):
        level = LEVEL_WARNING
    elif not missing and not duplicated and not count_mismatch:
        level = LEVEL_INFO
    else:
        level = LEVEL_INFO  # 兜底（不应到达）

    # ── 连续/累计计数与升级 ──
    prior = store.load_pitfall_reconcile() if store is not None else []

    l3_streak = 0
    l3_cumulative = 0
    l2_streak = 0
    promoted = False

    if level == LEVEL_WARNING:
        l2_streak = 1 + _trailing_consecutive_records(
            prior, project_id, path_id, LEVEL_WARNING
        )
        if streak_threshold and l2_streak >= streak_threshold:
            level = LEVEL_ERROR
            promoted = True
    if level == LEVEL_ERROR:
        l3_streak = _trailing_consecutive_chapters(
            prior, project_id, path_id, LEVEL_ERROR, chapter_no
        )
        l3_cumulative = (
            sum(
                1
                for r in prior
                if r.get("project_id") == project_id and r.get("level") == LEVEL_ERROR
            )
            + 1
        )

    streak = l3_streak if level == LEVEL_ERROR else (l2_streak if level == LEVEL_WARNING else 0)

    record = {
        "ts": time.time(),
        "project_id": project_id,
        "chapter_no": chapter_no,
        "scene_idx": scene_idx,
        "retry": retry_round,
        "path_id": path_id,
        "wired": wired,
        "expected_params": exp_params,
        "call_params": call_params,
        "params_conflict": params_conflict,
        "inject_channel": inj_channel,
        "expected_channel": exp_channel,
        "channel_conflict": channel_conflict,
        "expected": expected_patterns,
        "expected_n": expected_n,
        "actual_distinct": distinct,
        "actual_total": total,
        "missing": missing,
        "duplicated": duplicated,
        "ghost": ghosts,
        "count_mismatch": count_mismatch,
        "level": level,
        "streak": streak,
    }

    # ── 落盘（先盘点，fail_fast 抛异常前必须已完成） ──
    if store is not None:
        try:
            store.append_pitfall_reconcile(record)
        except Exception:
            logger.exception("pitfall_reconcile 落盘失败（不影响生成）")

    # ── 日志（结构化，禁止打印 prompt 全文，REQ §11.6/AC-19） ──
    _log(record)

    # ── agent_trace 旁路（L2/L3 才写，L1 不写避免噪声；尽力而为） ──
    if trace is not None and level in (LEVEL_WARNING, LEVEL_ERROR):
        try:
            trace.record(
                step_type="pitfall_reconcile",
                step_name=f"第{chapter_no}章/{path_id}"
                + (f"/场景{scene_idx}" if scene_idx is not None else "")
                + f"/重试{retry_round}",
                extra={
                    "level": level,
                    "expected_n": expected_n,
                    "actual_distinct": distinct,
                    "actual_total": total,
                    "missing": missing,
                    "duplicated": duplicated,
                    "params_conflict": params_conflict,
                    "channel_conflict": channel_conflict,
                    "streak": streak,
                },
                success=level in (LEVEL_INFO, LEVEL_UNWIRED),
            )
        except Exception:
            pass

    # ── fail_fast：L3 且达连续/累计阈值才中断（默认关） ──
    should_raise = (
        level == LEVEL_ERROR
        and fail_fast
        and (
            (streak_threshold and l3_streak >= streak_threshold)
            or (book_limit and l3_cumulative >= book_limit)
        )
    )
    if should_raise:
        # 信号交给外层 reconcile_pitfall_injection 在落盘后抛出
        record["_raise"] = True
        raise PitfallReconcileError(
            f"踩坑注入对账 L3 连续 {l3_streak} 章/累计 {l3_cumulative} 章，"
            f"path_id={path_id} chapter={chapter_no}（fail_fast=True）"
        )

    return record


def _record_unwired(
    *, store, project_id, path_id, chapter_no, scene_idx, retry_round,
    exp_params, exp_channel, call_params, trace,
) -> Optional[dict]:
    """L1u 未接线路径（本期 expand）：每章 1 条记录，每章去重，不计 streak。"""
    # 每章去重：同 project + chapter + path 已有 unwired 记录则跳过
    if store is not None:
        try:
            for r in store.load_pitfall_reconcile():
                if (
                    r.get("project_id") == project_id
                    and r.get("chapter_no") == chapter_no
                    and r.get("path_id") == path_id
                    and r.get("level") == LEVEL_UNWIRED
                ):
                    return None  # 本已记录，去重
        except Exception:
            pass

    record = {
        "ts": time.time(),
        "project_id": project_id,
        "chapter_no": chapter_no,
        "scene_idx": scene_idx,
        "retry": retry_round,
        "path_id": path_id,
        "wired": False,
        "expected_params": exp_params,
        "call_params": call_params,
        "params_conflict": call_params != exp_params,
        "inject_channel": CHANNEL_NONE,
        "expected_channel": exp_channel,
        "channel_conflict": CHANNEL_NONE != exp_channel,
        "expected": [],
        "expected_n": 0,
        "actual_distinct": 0,
        "actual_total": 0,
        "missing": [],
        "duplicated": [],
        "ghost": [],
        "count_mismatch": False,
        "level": LEVEL_UNWIRED,
        "streak": 0,
    }
    if store is not None:
        try:
            store.append_pitfall_reconcile(record)
        except Exception:
            logger.exception("pitfall_reconcile(unwired) 落盘失败（不影响生成）")
    logger.warning(
        "踩坑对账 L1u 未接线路径: path_id=%s chapter=%s（已知缺口，仅观测不补注入）",
        path_id, chapter_no,
    )
    if trace is not None:
        try:
            trace.record(
                step_type="pitfall_reconcile",
                step_name=f"第{chapter_no}章/{path_id}/未接线",
                extra={"level": LEVEL_UNWIRED, "wired": False},
                success=True,
            )
        except Exception:
            pass
    return record


def _log(record: dict) -> None:
    """按等级输出结构化日志（不含 prompt 全文，REQ AC-19）。"""
    level = record.get("level")
    base = (
        f"踩坑对账[{level}] path_id={record.get('path_id')} "
        f"chapter={record.get('chapter_no')} scene={record.get('scene_idx')} "
        f"retry={record.get('retry')} expected_n={record.get('expected_n')} "
        f"actual_distinct={record.get('actual_distinct')} "
        f"actual_total={record.get('actual_total')} "
        f"params_conflict={record.get('params_conflict')} "
        f"channel_conflict={record.get('channel_conflict')} "
        f"streak={record.get('streak')}"
    )
    if level == LEVEL_ERROR:
        logger.error(
            "%s missing=%s duplicated=%s",
            base, record.get("missing"), record.get("duplicated"),
        )
    elif level == LEVEL_WARNING:
        logger.warning(
            "%s missing=%s duplicated=%s count_mismatch=%s",
            base, record.get("missing"), record.get("duplicated"),
            record.get("count_mismatch"),
        )
    elif level == LEVEL_INFO:
        logger.info("%s", base)
    # L1u / skip 的日志已在各自分支处理
