# -*- coding: utf-8 -*-
"""章后账本同步（生成链路与 R1 局部改写共用）。

两个入口：
1. `record_mechanism_state`：确定性机制账本入账（死亡次数/蚀化/注册字段末值），
   零 LLM。原先内联在 `stages._write_post_gates`，抽到此处供改写路径复用，避免
   两条路径口径漂移。
2. `sync_after_rewrite`：正文被局部改写并已结算之后的账本同步（摘要 / 场景标脏 /
   机制账本 / 章末状态与人物状态）。

约束：本模块只做"正文已落盘后"的派生账本更新，失败只留日志、绝不抛出，
避免"钱扣了正文没了"；不参与任何计费计算。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["record_mechanism_state", "sync_after_rewrite",
           "write_chapter_state_guarded"]


def _as_int(value) -> int:
    """宽松转 int；无法转换返回 0（用于章号比较）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _project_id(store) -> str:
    """项目标识（仅用于日志，取目录名）。"""
    return getattr(getattr(store, "dir", None), "name", "") or ""


def write_chapter_state_guarded(store, state: dict,
                                source: str = "unknown") -> tuple[str, dict]:
    """写章末状态快照，带"章号倒退"护栏。

    `chapter_state.json` 是全书单条"最近一章"快照，没有版本维度。旧章被改写后
    若直接覆盖，全书承接点会倒退回旧章，故仅在目标章号不早于已记录章号时写入。

    返回 (status, detail)，status 三种情形：
      - "written"：成功写入（首章 / 正常前进 / 同章幂等覆盖）
      - "skipped_backward"：目标章号 < 已记录章号，保留更靠后的快照（warning 留痕）
      - "skipped_empty"：无有效章号，未写入（warning 留痕）
    """
    target = _as_int((state or {}).get("chapter"))
    pid = _project_id(store)
    if target <= 0:
        logger.warning("章末状态未写入：缺少有效章号 project=%s source=%s",
                       pid, source)
        return "skipped_empty", {"target": None, "current": None,
                                 "source": source}
    current = _as_int((store.load_chapter_state() or {}).get("chapter"))
    if current and target < current:
        logger.warning(
            "章末状态未写入：章号倒退，保留较新快照 project=%s target=%d "
            "current=%d source=%s", pid, target, current, source)
        return "skipped_backward", {"target": target, "current": current,
                                    "source": source}
    store.save_chapter_state(state)
    return "written", {"target": target, "current": current, "source": source}


def record_mechanism_state(memory, chapter_no: int, content: str) -> bool:
    """本章末机制状态确定性入账（零 LLM，同章覆盖）。

    与 `stages._write_post_gates` 原内联逻辑等价：累积型字段（升向）取正文面板最大值，
    deaths 用「本章末累积值 - 上一章末累积值」反推（P0-14 口径），注册字段末值由
    `ledger_extract_numbers` 抽取。memory 为空或关闭时直接跳过。
    """
    if memory is None:
        return False
    try:
        from .chapter_state import (cumulative_value,
                                    ledger_extract_numbers,
                                    mechanism_field_registry)
        _canon = (memory.load().get("canon") or {})
        _reg = mechanism_field_registry(_canon.get("mechanism_baseline") or {})
        # 累积型字段（蚀化/进度等，方向 increase）取正文最大值；注册表为空/未出现 → None
        _eros = cumulative_value(content or "", _reg)
        _prev_eros = None
        try:
            _st0 = memory.load().get("state") or {}
            _prev_ch = [c for c in _st0.get("chapters", [])
                        if int(c.get("ch", 0)) < int(chapter_no)]
            if _prev_ch:
                _prev_eros = _prev_ch[-1].get("erosion")
        except Exception:
            _prev_eros = None
        # 无面板=无事件=蚀化不变：缺失时继承上一章末，保证账本不破链
        if _eros is None and _prev_eros is not None:
            _eros = int(_prev_eros)
        _deaths = 0
        if _eros is not None and _prev_eros is not None and _eros >= int(_prev_eros or 0):
            _deaths = _eros - int(_prev_eros or 0)
        _nums = ledger_extract_numbers(content or "", _reg) if _reg else {}
        memory.record_chapter_state(chapter_no, deaths=_deaths, erosion=_eros,
                                    numbers=_nums)
        return True
    except Exception:
        logger.exception("机制状态账本入账失败（降级跳过）: ch=%s", chapter_no)
        return False


def _open_book_memory(store, cfg):
    """按开关构造 BookMemory；关闭或构造失败返回 None（降级）。"""
    if not getattr(cfg, "book_memory", True):
        return None
    try:
        from .book_memory import BookMemory
        return BookMemory(store, cfg)
    except Exception:
        logger.warning("BookMemory 初始化失败（账本同步降级）: project=%s",
                       getattr(getattr(store, "dir", None), "name", ""), exc_info=True)
        return None


def sync_after_rewrite(store, pipeline, chapter_no: int,
                       version: int | None = None) -> dict:
    """局部改写落盘并结算后，同步本章派生账本。

    步骤互不阻断：任一步失败只记日志，其余继续；返回值给调用方留痕，
    便于上层感知"正文与账本可能不一致"。
    """
    result = {"summary": False, "scenes_stale": False,
              "book_memory": False, "commit": None, "chapter_state": "not_applied"}
    chapter = store.load_chapter(chapter_no)
    if chapter is None or not chapter.content:
        logger.warning("账本同步跳过：章节不存在或正文为空 no=%s", chapter_no)
        return result
    cfg = pipeline.cfg

    # 1) 摘要：用与写章同源的兜底摘要函数重生成（幂等覆盖；受 LLM 客户端 guard 约束）
    try:
        writer = getattr(pipeline, "chapter_writer", None)
        if writer is not None:
            summary = writer._gen_summary(chapter_no, chapter.title, chapter.content)
            if isinstance(summary, str) and summary.strip():
                store.append_summary(chapter_no, summary.strip())
                result["summary"] = True
    except Exception:
        logger.warning("改写后摘要同步失败（降级）: no=%s", chapter_no, exc_info=True)

    # 2) 场景记录：不重跑拆场景（会引入额外 LLM 与正文漂移），标记为过期供上层感知
    try:
        result["scenes_stale"] = bool(store.mark_scenes_stale(
            chapter_no, reason="issue_rewrite", version=version))
    except Exception:
        logger.warning("改写后场景标记失败（降级）: no=%s", chapter_no, exc_info=True)

    # 3) 机制账本（book_memory.state.chapters[ch]），确定性零 LLM
    try:
        memory = _open_book_memory(store, cfg)
        if memory is not None:
            result["book_memory"] = record_mechanism_state(
                memory, chapter_no, chapter.content)
    except Exception:
        logger.warning("改写后机制账本同步失败（降级）: no=%s", chapter_no, exc_info=True)

    # 4) 章末状态 / 人物状态 / 揭示跟踪 / 读者账本：force 跳过"已提交即跳过"短路
    try:
        result["commit"] = pipeline.commit_chapter_ledgers(
            chapter_no, force=True, source="rewrite_sync")
        # 把 chapter_state 的落盘状态摊到 ledger 顶层，上层可直接判断本章账本
        # 是否跟上正文：written / skipped_backward / skipped_idempotent / not_applied
        result["chapter_state"] = ((result["commit"] or {}).get(
            "chapter_state_status") or "not_applied")
    except Exception:
        logger.warning("改写后章末账本提交失败（降级）: no=%s", chapter_no, exc_info=True)
        result["chapter_state"] = "failed"
    return result
