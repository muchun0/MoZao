# -*- coding: utf-8 -*-
"""Agent 全链路 trace（模块1）数据访问：MySQL 落库 + 查询。

- MysqlTraceSink 实现 novel_gen.pipeline.trace.TraceSink 接口，供 NovelPipeline 注入。
- 查询函数支撑问题回溯与阶段2监控（按 trace_id / project_id / user_id 聚合）。
"""
from __future__ import annotations

import time

from .base import _connect

__all__ = [
    "MysqlTraceSink",
    "list_trace_by_trace_id", "list_trace_by_project", "list_trace_by_user",
    "trace_stats_by_project",
]


class MysqlTraceSink:
    """MySQL 快照落库通道。所有写入异常一律吞掉（不阻塞生成主流程）。"""

    def record(self, entry: dict) -> None:
        try:
            import json as _json
            extra = entry.get("extra")
            if extra is not None and not isinstance(extra, str):
                extra = _json.dumps(extra, ensure_ascii=False)
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO agent_trace (trace_id, project_id, user_id, scene, step_type, "
                    "step_order, step_name, system_prompt, user_input, raw_output, "
                    "token_in, token_out, token_total, duration_ms, success, error_info, "
                    "model, extra, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        entry.get("trace_id", ""),
                        entry.get("project_id", ""),
                        int(entry.get("user_id", 0) or 0),
                        entry.get("scene", ""),
                        entry.get("step_type", ""),
                        int(entry.get("step_order", 0) or 0),
                        entry.get("step_name", ""),
                        entry.get("system_prompt") or None,
                        entry.get("user_input") or None,
                        entry.get("raw_output") or None,
                        int(entry.get("token_in", 0) or 0),
                        int(entry.get("token_out", 0) or 0),
                        int(entry.get("token_total", 0) or 0),
                        int(entry.get("duration_ms", 0) or 0),
                        int(entry.get("success", 1) or 1),
                        entry.get("error_info") or None,
                        entry.get("model", ""),
                        extra or None,
                        entry.get("created_at", time.time()),
                    ),
                )
        except Exception:
            # 快照写入失败绝不拖垮生成任务
            pass


def _rows(sql: str, params: tuple) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_trace_by_trace_id(trace_id: str, limit: int = 500) -> list[dict]:
    """按链路 ID 查完整执行链路（按步骤序号升序）。"""
    return _rows(
        "SELECT * FROM agent_trace WHERE trace_id=%s ORDER BY step_order ASC LIMIT %s",
        (trace_id, int(limit)),
    )


def list_trace_by_project(project_id: str, limit: int = 200) -> list[dict]:
    """按项目查最近快照（跨多次任务）。"""
    return _rows(
        "SELECT * FROM agent_trace WHERE project_id=%s ORDER BY id DESC LIMIT %s",
        (project_id, int(limit)),
    )


def list_trace_by_user(user_id: int, limit: int = 200) -> list[dict]:
    """按用户查最近快照（用户维度行为/质量分析预留）。"""
    return _rows(
        "SELECT * FROM agent_trace WHERE user_id=%s ORDER BY id DESC LIMIT %s",
        (int(user_id), int(limit)),
    )


def trace_stats_by_project(project_id: str) -> dict:
    """按项目聚合统计（阶段2监控仪表盘预留）：
    {task_count, call_count, token_total, avg_duration_ms, fail_count, scenes:{...}}
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT trace_id) AS task_count, COUNT(*) AS call_count, "
            "COALESCE(SUM(token_total),0) AS token_total, "
            "COALESCE(AVG(duration_ms),0) AS avg_duration_ms, "
            "COALESCE(SUM(success=0),0) AS fail_count "
            "FROM agent_trace WHERE project_id=%s",
            (project_id,),
        ).fetchone()
        scenes_rows = conn.execute(
            "SELECT scene, COUNT(*) AS n, COALESCE(SUM(token_total),0) AS tokens "
            "FROM agent_trace WHERE project_id=%s GROUP BY scene",
            (project_id,),
        ).fetchall()
    d = dict(row)
    d["scenes"] = {r["scene"]: {"calls": r["n"], "tokens": r["tokens"]} for r in scenes_rows}
    return d
