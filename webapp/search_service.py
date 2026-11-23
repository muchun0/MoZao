# -*- coding: utf-8 -*-
"""只读全文检索（R25 交付、R29/R28 共用，基座 §5.4）。

纪律：只读、不计费、不缓存、不写任何文件；逐章流式扫描 `iter_chapter_texts()`，
避免整本书一次性载入内存。命中片段取前后各 40 字，按章节号/章内偏移升序返回。
"""
from __future__ import annotations

import time

from .logger import get_logger

logger = get_logger("search")

__all__ = ["SearchError", "search_enabled", "search_project"]

Q_MIN, Q_MAX = 2, 40
LIMIT_MIN, LIMIT_MAX, LIMIT_DEFAULT = 1, 50, 20
SNIPPET_PAD = 40
SCAN_CHARS_MAX = 20_000_000
DEADLINE_SECONDS = 3.0
SCOPES = ("body", "settings", "all")


class SearchError(Exception):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def search_enabled() -> bool:
    """`sys_config.search_enabled` 开关（未配行按开启）；读取失败降级开启。"""
    try:
        from .db import config as config_db
        row = config_db.get_config("search_enabled")
        if not row:
            return True
        return bool(config_db.coerce_config_type(row["config_type"], row["config_value"]))
    except Exception:
        logger.warning("读取 search_enabled 失败，按开启处理")
        return True


def _validate(q: str, limit: int, offset: int, scope: str) -> tuple[str, int, int, str]:
    q = str(q or "").strip()
    if len(q) < Q_MIN or len(q) > Q_MAX:
        raise SearchError(400, "BAD_QUERY", f"关键词长度需在 {Q_MIN}–{Q_MAX} 字之间")
    if any(ord(ch) < 32 for ch in q):
        raise SearchError(400, "BAD_QUERY", "关键词不能包含控制字符")
    try:
        limit = int(limit)
        offset = int(offset)
    except (TypeError, ValueError):
        raise SearchError(400, "BAD_QUERY", "limit/offset 必须是整数")
    limit = max(LIMIT_MIN, min(LIMIT_MAX, limit))
    offset = max(0, offset)
    scope = str(scope or "all").strip().lower()
    if scope not in SCOPES:
        raise SearchError(400, "BAD_QUERY", f"scope 只能是 {'/'.join(SCOPES)}")
    return q, limit, offset, scope


def _snippet(text: str, pos: int, size: int) -> str:
    start = max(0, pos - SNIPPET_PAD)
    end = min(len(text), pos + size + SNIPPET_PAD)
    return text[start:end].replace("\n", " ")


def _settings_texts(store):
    """设定类文本（世界观 / 人物 / 术语 / 机制 / 大纲），按标签产出。只读降级。"""
    try:
        world = store.load_world()
    except Exception:
        logger.warning("检索读取世界观失败（降级跳过 settings）")
        world = None
    if world is not None:
        if getattr(world, "world_view", ""):
            yield "世界观", world.world_view
        for c in getattr(world, "characters", []) or []:
            yield f"人物·{c.name}", " ".join(filter(None, [c.name, c.role, c.desc, c.personality]))
        for t in getattr(world, "terminology", []) or []:
            yield f"术语·{t.name}", " ".join(filter(None, [t.name, t.definition]))
        if getattr(world, "mechanism_rules", ""):
            yield "机制规则", world.mechanism_rules
    try:
        outline = store.load_outline()
    except Exception:
        logger.warning("检索读取大纲失败（降级跳过 settings）")
        return
    if outline is None:
        return
    if outline.intro:
        yield "大纲·总纲", outline.intro
    for v in outline.volumes or []:
        yield f"大纲·第{v.no}卷", " ".join(filter(None, [v.name, v.intro, v.main_conflict]))
    for p in outline.chapters or []:
        yield f"大纲·第{p.no}章", " ".join(filter(None, [
            p.title, p.goal, p.core_event, p.conflict,
            p.character_moves, p.foreshadowing, p.cliffhanger]))


def search_project(store, *, q: str, limit: int = LIMIT_DEFAULT, offset: int = 0,
                   scope: str = "all") -> dict:
    """执行一次只读检索，返回 {items, scanned_chars, truncated, elapsed_ms}。"""
    q, limit, offset, scope = _validate(q, limit, offset, scope)
    started = time.time()
    deadline = started + DEADLINE_SECONDS
    needed = offset + limit
    hits: list[dict] = []
    scanned = 0
    truncated = False

    def _scan_chapter(ch: int, title: str, text: str, kind: str) -> bool:
        """返回 False 表示应停止（已满足数量/超时/超扫描上限）。"""
        nonlocal scanned, truncated
        if not text:
            return True
        scanned += len(text)
        pos = text.find(q)
        while pos != -1:
            hits.append({"ch": ch, "ch_title": title, "offset": pos,
                         "snippet": _snippet(text, pos, len(q)), "kind": kind})
            if len(hits) >= needed:
                return False
            pos = text.find(q, pos + max(1, len(q)))
        if scanned >= SCAN_CHARS_MAX:
            truncated = True
            return False
        if time.time() > deadline:
            truncated = True
            return False
        return True

    if scope in ("body", "all"):
        try:
            for no, title, content in store.iter_chapter_texts():
                if not _scan_chapter(no, title, content, "body"):
                    break
        except Exception:
            logger.exception("正文检索失败（返回已扫描部分）: project=%s", store.dir.name)
            truncated = True

    if scope in ("settings", "all") and len(hits) < needed and not truncated:
        try:
            for label, text in _settings_texts(store):
                if not _scan_chapter(0, label, text, "settings"):
                    break
        except Exception:
            logger.exception("设定检索失败（返回已扫描部分）: project=%s", store.dir.name)
            truncated = True

    elapsed = int((time.time() - started) * 1000)
    logger.info("全文检索: project=%s q=%r scope=%s hits=%d scanned=%d elapsed=%dms",
                store.dir.name, q, scope, len(hits), scanned, elapsed)
    return {
        "items": hits[offset:offset + limit],
        "scanned_chars": scanned,
        "truncated": bool(truncated),
        "elapsed_ms": elapsed,
    }
