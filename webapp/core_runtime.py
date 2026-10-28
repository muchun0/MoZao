# -*- coding: utf-8 -*-
"""
墨造核心层·运行时：路径配置、运行中任务注册表、并发槽位与任务持久化。

从 core.py 拆出（业务域拆分，行为零变化）。本模块只承载「任务运行时/并发/持久化」
这一个内聚概念，不包含任何路由、业务结算、生成线程主体逻辑：
- 数据目录路径常量（PROJECTS_DIR / OUTPUT_DIR / WEB_DIR / BASE_DIR）
- 运行中任务注册表 _RUNS 与全局并发信号量 _GEN_SEMAPHORE
- 生成槽位占用/释放（_acquire_slot / _release_slot）
- 任务标记落盘与崩溃/重启重拾（_mark_task / _clear_task / _resume_interrupted_tasks）
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from novel_gen.store.project import ProjectStore
from novel_gen.utils.paths import get_output_dir, get_projects_dir

from .logger import get_logger

logger = get_logger("server")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
# 数据目录可配置：.env 的 DATA_DIR；未配置时回退代码根，行为与历史版本一致
PROJECTS_DIR = get_projects_dir()
OUTPUT_DIR = get_output_dir()
WEB_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
_MAX_UPLOAD = 10 * 1024 * 1024  # P2 上传大小上限 10MB


# ---------- 运行中的任务注册表：project_id -> 控制句柄 ----------
class _RunHandle:
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.error: Optional[str] = None
        self.task_id: str = ""  # agent_run 任务 id（task.json 关联，重启后沿用）


_RUNS: dict[str, _RunHandle] = {}
_LOCK = threading.Lock()
# 全局并发生成数限制：防止单用户开太多生成任务导致 LLM API 限流/数据库连接耗尽。
# 每个生成任务开始时 acquire，结束时 release。3 路并发对当前额度和连接池是安全水位。
_GEN_SEMAPHORE = threading.Semaphore(3)


def _acquire_slot(project_id: str, busy_msg: str = "系统繁忙，已有多个任务在生成，请稍后再试") -> _RunHandle:
    """占用一个生成槽位：检查是否已在运行 + 建句柄 + 申请并发信号量。失败抛 409/429。"""
    with _LOCK:
        if project_id in _RUNS and _RUNS[project_id].thread and _RUNS[project_id].thread.is_alive():
            raise HTTPException(status_code=409, detail="该项目正在生成中")
        handle = _RunHandle()
        _RUNS[project_id] = handle
    if not _GEN_SEMAPHORE.acquire(blocking=False):
        with _LOCK:
            _RUNS.pop(project_id, None)
        raise HTTPException(status_code=429, detail=busy_msg)
    return handle


def _release_slot(project_id: str) -> None:
    """释放生成槽位：归还信号量并移除句柄。"""
    _GEN_SEMAPHORE.release()
    with _LOCK:
        _RUNS.pop(project_id, None)


# ---------- 任务持久化（崩溃/重启后自动重拾） ----------
_TASK_FILE = "task.json"


def _mark_task(store: ProjectStore, kind: str, **kw) -> None:
    """落盘任务标记（服务重启后据此重拾）。线程启动时调用。"""
    try:
        store.dir.joinpath(_TASK_FILE).write_text(
            json.dumps({"kind": kind, "started_at": time.time(), **kw}, ensure_ascii=False),
            encoding="utf-8")
    except Exception:
        pass


def _clear_task(store: ProjectStore) -> None:
    """清除任务标记。线程正常结束/用户停止/异常退出时调用。"""
    try:
        p = store.dir / _TASK_FILE
        if p.exists():
            p.unlink()
    except Exception:
        pass


def _resume_interrupted_tasks() -> None:
    """扫描 PROJECTS_DIR 下残留的 task.json，逐个重拾生成任务。

    重拾后从断点继续（章节产物文件是唯一权威来源，不重复生成）；
    并发槽不足时跳过并告警（用户可手动重新触发）。
    """
    if not PROJECTS_DIR.exists():
        return
    resumed = skipped = 0
    for d in sorted(PROJECTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        tf = d / _TASK_FILE
        if not tf.exists():
            continue
        project_id = d.name
        try:
            meta = json.loads(tf.read_text(encoding="utf-8"))
            kind = meta.get("kind", "")
            store = ProjectStore.open(d)
            handle = _RunHandle()
            with _LOCK:
                _RUNS[project_id] = handle
            if not _GEN_SEMAPHORE.acquire(blocking=False):
                with _LOCK:
                    _RUNS.pop(project_id, None)
                skipped += 1
                logger.warning("启动重拾跳过（并发槽满）: project=%s kind=%s", project_id, kind)
                continue
            # 延迟导入避免 core_runtime ↔ core_generate 模块级循环依赖
            from .core_generate import _run_batch, _run_generation, _run_outline_only
            if kind == "generation":
                target = _run_generation
                args = (store, handle, int(meta.get("start_from", 0) or 0))
            elif kind == "outline":
                target = _run_outline_only
                args = (store, handle)
            elif kind == "batch":
                target = _run_batch
                args = (store, handle, int(meta.get("start", 1) or 1), int(meta.get("end", 1) or 1))
            elif kind == "agent_run":
                # Agent 统一触发（TECH §4.1）：沿用原 task_id/run_id 重拾
                try:
                    from . import generate_service
                    generate_service.resume_agent_run(store, handle, meta)
                    resumed += 1
                    logger.info("启动重拾任务: project=%s kind=%s", project_id, kind)
                except Exception:
                    logger.exception("agent_run 重拾失败: project=%s", project_id)
                    _clear_task(store)
                    _GEN_SEMAPHORE.release()
                    with _LOCK:
                        _RUNS.pop(project_id, None)
                continue
            else:
                _clear_task(store)
                _GEN_SEMAPHORE.release()
                with _LOCK:
                    _RUNS.pop(project_id, None)
                logger.warning("未知任务标记，已清理: project=%s kind=%s", project_id, kind)
                continue
            t = threading.Thread(target=target, args=args, daemon=True)
            handle.thread = t
            t.start()
            resumed += 1
            logger.info("启动重拾任务: project=%s kind=%s", project_id, kind)
        except Exception:
            logger.exception("启动重拾失败: project=%s", project_id)
    logger.info("启动重拾完成: resumed=%d skipped=%d", resumed, skipped)


__all__ = [
    "logger",
    "BASE_DIR", "PROJECTS_DIR", "OUTPUT_DIR", "WEB_DIR", "_MAX_UPLOAD", "_TASK_FILE",
    "_RunHandle", "_RUNS", "_LOCK", "_GEN_SEMAPHORE",
    "_acquire_slot", "_release_slot", "_mark_task", "_clear_task", "_resume_interrupted_tasks",
]
