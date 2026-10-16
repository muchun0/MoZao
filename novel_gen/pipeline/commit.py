# -*- coding: utf-8 -*-
"""章节原子提交 + 投影一致性（REQ-借鉴webnovel-章节提交与投影一致性，A/B）。

把 manager.resume 每章收尾的"章后入账"（人物动态状态 / 章末机制状态 / 揭示执行 /
读者认知账本）从分散的 try/except 静默调用收敛为一次统一提交：

  1. 先落提交基线（chapter_commit.json 标记 pending）；
  2. 按序执行所有注册的入账动作（name + callable + enabled），成功/失败逐项记录；
  3. 全部成功 → committed；任一失败 → incomplete（明确记录失败项），供体检/重试；
  4. 幂等：同章已 committed 且非 force 则跳过（断点续传语义不变）；
  5. 每次提交后写 projection_log.jsonl（各视图同步状态）；check_projection() 提供
     账本一致性体检：扫描各派生视图落后章号，定位"哪个账本没跟上"。

边界（§11）：不改变任何账本自身的读/注入路径与降级语义；正文等核心产物仍在 stages
落盘，提交只收敛章后增量账本；开关 chapter_commit=False 时回退原分散调用（行为不变）。
失败不阻塞整本生成——incomplete 记录供体检/重试，不抛错中断。
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from ..store.project import ProjectStore
from webapp.logger import get_logger

logger = get_logger("pipeline.commit")


class CommitAction:
    """一次章后入账动作：name + callable(no) + enabled。"""

    __slots__ = ("name", "fn", "enabled")

    def __init__(self, name: str, fn: Callable, enabled: bool = True):
        self.name = name
        self.fn = fn
        self.enabled = enabled


class ChapterCommitter:
    """章节统一提交器：收敛章后入账，落基线 + 幂等 + 失败留痕 + 投影日志。"""

    def __init__(self, store: ProjectStore, cfg=None, trace=None,
                 actions: Optional[list[CommitAction]] = None):
        self.store = store
        self.cfg = cfg
        self.trace = trace
        self.actions: list[CommitAction] = list(actions or [])

    @property
    def enabled(self) -> bool:
        """总开关：chapter_commit=False 时回退为直接依次调用（不落提交记录）。"""
        if self.cfg is None:
            return True
        return bool(getattr(self.cfg, "chapter_commit", True))

    def add_action(self, name: str, fn: Callable, enabled: bool = True) -> None:
        self.actions.append(CommitAction(name, fn, enabled=enabled))

    # ---------- 主入口 ----------
    def commit_chapter(self, chapter_no: int, *, force: bool = False,
                       silent: bool = False) -> dict:
        """执行一次章节提交，返回 {chapter, status, done[], failed[]}。

        status:
          committed  — 全部入账成功（基线记录为 committed）；
          incomplete — 有失败项（基线记录为 incomplete，保留失败清单供体检/重试）；
          skipped    — 该章已 committed 且非 force（幂等跳过）；
          disabled   — 开关关闭，直接依次调用、不落提交记录（行为与原分散调用一致）。
        """
        if not self.enabled:
            for act in self.actions:
                if act.enabled:
                    self._run_one(act.name, act.fn, chapter_no, silent=silent)
            return {"chapter": chapter_no, "status": "disabled",
                    "done": [a.name for a in self.actions if a.enabled], "failed": []}

        base = self.store.load_chapter_commit() or {}
        chapters = base.get("chapters")
        if not isinstance(chapters, dict):
            chapters = {}
            base["chapters"] = chapters
        key = str(chapter_no)

        # 幂等：已 committed 且非 force → 跳过（断点续传语义不变）
        prev = chapters.get(key)
        if prev and prev.get("status") == "committed" and not force:
            if not silent:
                logger.info("[Commit] 第%d章已提交，跳过", chapter_no)
            return {"chapter": chapter_no, "status": "skipped",
                    "done": list(prev.get("done") or []), "failed": []}

        # 落提交基线（pending）
        chapters[key] = {"status": "pending", "done": [], "failed": [],
                         "started_at": int(time.time())}
        self.store.save_chapter_commit(base)

        done: list[str] = []
        failed: list[dict] = []
        for act in self.actions:
            if not act.enabled:
                continue
            ok, err = self._run_one(act.name, act.fn, chapter_no, silent=silent)
            if ok:
                done.append(act.name)
            else:
                failed.append({"name": act.name, "error": err})
                # 失败即时落盘：保证 incomplete 状态在断点/体检时可见，不静默丢失
                chapters[key] = {"status": "incomplete",
                                 "done": list(done), "failed": list(failed),
                                 "started_at": chapters[key].get("started_at", 0)}
                self.store.save_chapter_commit(base)

        status = "committed" if not failed else "incomplete"
        chapters[key] = {"status": status, "done": done, "failed": failed,
                         "started_at": chapters[key].get("started_at", 0),
                         "finished_at": int(time.time())}
        self.store.save_chapter_commit(base)
        base["updated_ch"] = int(chapter_no)
        self.store.save_chapter_commit(base)

        # 投影日志
        self._log_projection(chapter_no, status, done, failed, silent=silent)
        return {"chapter": chapter_no, "status": status, "done": done,
                "failed": failed}

    # ---------- 内部 ----------
    def _run_one(self, name: str, fn: Callable, chapter_no: int,
                 silent: bool = False) -> tuple[bool, str]:
        """执行单个入账动作：返回 (成功, 错误文本)。fn 抛异常或返回 False 视为失败；
        不 re-raise（保持失败不阻塞整本生成的降级语义，但留痕到提交记录）。"""
        try:
            result = fn(chapter_no)
            if result is False:
                return False, "动作返回失败标志"
            return True, ""
        except Exception as e:  # 兜底：内部未捕获的异常也记为失败项
            if not silent:
                logger.warning("[Commit] %s 入账失败: %s", name, e)
            return False, f"{type(e).__name__}: {e}"

    def _log_projection(self, chapter_no: int, status: str,
                        done: list[str], failed: list[dict],
                        silent: bool = False) -> None:
        """写 projection_log.jsonl：记录本章各视图同步状态（done=已同步，failed=缺失）。"""
        try:
            self.store.append_projection_log({
                "ts": int(time.time()),
                "chapter": int(chapter_no),
                "status": status,
                "synced": list(done),
                "missing": [f.get("name") for f in failed],
            })
        except Exception:
            if not silent:
                logger.exception("投影日志写入失败（不阻塞）")


# ---------------------------------------------------------------------------
# 一致性体检（B）：扫描各派生视图，定位"哪个账本落后于当前章号"
# ---------------------------------------------------------------------------

def _max_num(seq) -> Optional[int]:
    """取可迭代数值序列的最大值；空/异常返回 None。"""
    vals = [int(v) for v in seq if v is not None]
    return max(vals) if vals else None


def _covered_chapter(store: ProjectStore, view: str) -> Optional[int]:
    """尽力推断某视图已覆盖到的最大章号；无法推断返回 None（标 unknown，不误报）。"""
    try:
        if view == "chapter_state":
            d = store.load_chapter_state()
            return int(d.get("chapter")) if d and d.get("chapter") else None
        if view == "reveal_status":
            d = store.load_reveal_status() or {}
            hits = [h for it in d.values() if isinstance(it, dict)
                    for h in (it.get("hit_ch") or [])]
            return _max_num(hits) if hits else None
        if view == "foreshadow_commitment":
            d = store.load_foreshadow_commitment()
            return int(d.get("updated_ch")) if d and d.get("updated_ch") else None
        if view == "payoff_quota":
            d = store.load_payoff_quota()
            log = (d or {}).get("log") or []
            return _max_num([r.get("ch") for r in log]) if log else None
        if view == "summaries":
            s = store.load_summaries()
            return _max_num([x.get("no") for x in s]) if s else None
        if view == "scenes":
            d = store.load_scenes()
            return _max_num(list(d.keys())) if isinstance(d, dict) and d else None
        if view == "beat_gate":
            d = store.load_beat_gate()
            return _max_num(list(d.keys())) if d else None
        if view == "chapter_commit":
            d = store.load_chapter_commit()
            chapters = (d or {}).get("chapters") or {}
            covered = [int(k) for k, v in chapters.items()
                       if isinstance(v, dict) and v.get("status") != "pending"]
            return _max_num(covered) if covered else None
    except Exception:
        return None
    return None  # character_states / book_memory 等无法从结构推断章号


#: 体检视图清单（name → 是否纳入"落后判断"；无法推断的返回 None=unknown）
PROJECTION_VIEWS: list[str] = [
    "chapter_commit", "chapter_state", "reveal_status",
    "foreshadow_commitment", "payoff_quota", "summaries",
    "scenes", "beat_gate", "character_states", "book_memory",
]


def check_projection(store: ProjectStore,
                     up_to_chapter: Optional[int] = None) -> list[dict]:
    """账本一致性体检：扫描各派生视图，输出覆盖章号与同步状态。

    返回 [{view, covered_ch, status}]，status ∈ {synced, lagging, ahead, unknown}。
    up_to_chapter 默认 = 当前已写最大章节号；无法推断覆盖章号的视图标 unknown（不误报）。
    """
    if up_to_chapter is None:
        chapters = store.list_chapters()
        nums = [int(c.no) for c in chapters if c.no is not None]
        up_to = _max_num(nums) if nums else 0
    else:
        up_to = int(up_to_chapter)

    out: list[dict] = []
    for view in PROJECTION_VIEWS:
        covered = _covered_chapter(store, view)
        if covered is None:
            out.append({"view": view, "covered_ch": None, "status": "unknown"})
            continue
        if covered > up_to:
            status = "ahead"
        elif covered == up_to:
            status = "synced"
        else:
            status = "lagging"
        out.append({"view": view, "covered_ch": covered, "status": status})
    return out
