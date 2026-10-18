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


def _aggregate_quality(rows: list[dict]) -> dict:
    """纯函数：把 quality_check 快照行聚合为质量指标（可单测，不依赖 MySQL）。

    依赖 trace.record_quality 写入的 extra：{issues, score, issue_count, auto_revised}。
    """
    import json as _json
    import re as _re
    scores: list[int] = []
    rewrites = 0
    issue_types: dict[str, int] = {}
    checks = 0
    chapter_map: dict[int, dict] = {}   # P0-5 进度盘：每章状态聚合
    for r in rows:
        checks += 1
        raw = r.get("extra")
        if isinstance(raw, str):
            try:
                extra = _json.loads(raw)
            except Exception:
                extra = {}
        elif isinstance(raw, dict):
            extra = raw
        else:
            extra = {}
        # P0-5 每章状态（红=未过 / 绿=hook>=4 高分 / 黄=自动重写 / 灰=通过普通）
        _ch_m = _re.search(r"第(\d+)章", str(r.get("step_name") or ""))
        if _ch_m:
            _ch = int(_ch_m.group(1))
            chapter_map[_ch] = {
                "chapter": _ch,
                "score": int(extra.get("score") or 0),
                "passed": bool(extra.get("passed")),
                "auto_revised": bool(extra.get("auto_revised")),
                "hook_score": extra.get("hook_score"),
            }
        sc = extra.get("score")
        if isinstance(sc, (int, float)):
            scores.append(int(sc))
        if extra.get("auto_revised"):
            rewrites += 1
        for it in (extra.get("issues") or []):
            if not isinstance(it, dict):
                continue
            t = str(it.get("type") or "")
            if not t:
                continue
            key = t.split("-", 1)[0] if "-" in t else t
            issue_types[key] = issue_types.get(key, 0) + 1
    buckets = {"0-59": 0, "60-79": 0, "80-100": 0}
    for s in scores:
        if s < 60:
            buckets["0-59"] += 1
        elif s < 80:
            buckets["60-79"] += 1
        else:
            buckets["80-100"] += 1
    n = len(scores) or 1
    # P0-5 进度盘：每章色块（红=未过 / 绿=hook>=4 高分 / 黄=自动重写 / 灰=通过普通）
    chapter_status = []
    for _ch in sorted(chapter_map):
        _c = chapter_map[_ch]
        if not _c["passed"]:
            _color = "red"
        elif _c["hook_score"] is not None and int(_c["hook_score"]) >= 4:
            _color = "green"
        elif _c["auto_revised"]:
            _color = "yellow"
        else:
            _color = "gray"
        chapter_status.append({**_c, "color": _color})
    return {
        "quality_checks": checks,
        "score_avg": round(sum(scores) / n, 1) if scores else None,
        "score_buckets": buckets,
        "rewrite_count": rewrites,
        "rewrite_rate": round(rewrites / n, 3) if checks else 0,
        "issue_top_types": sorted(issue_types.items(), key=lambda x: -x[1])[:10],
        "chapter_status": chapter_status,   # P0-5 进度盘
    }


def quality_report(project_id: str = "", days: int = 0) -> dict:
    """P2-4 质量指标报表（只读）：按项目/时间维度聚合 agent_trace。

    返回 {"filter": {...}, "report": {任务/调用/成本总览, 质检得分分布, 重写率,
    问题类型 TopN, 弃书风险分布, scene 成本分布}}。
    数字与 agent_trace 明细同源（可对账）。
    """
    conds: list[str] = []
    params: list = []
    if project_id:
        conds.append("project_id=%s")
        params.append(project_id)
    if days and days > 0:
        conds.append("created_at>=%s")
        params.append(time.time() - days * 86400)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(DISTINCT trace_id) AS task_count, COUNT(*) AS call_count, "
            f"COALESCE(SUM(token_total),0) AS token_total, "
            f"COALESCE(AVG(duration_ms),0) AS avg_duration_ms, "
            f"COALESCE(SUM(success=0),0) AS fail_count "
            f"FROM agent_trace{where}", tuple(params)).fetchone()
        qc_where = "step_type='quality_check'"
        if conds:
            qc_where += " AND " + " AND ".join(conds)
        qc_rows = conn.execute(
            f"SELECT extra, step_name FROM agent_trace WHERE {qc_where}", tuple(params)).fetchall()
        churn_where = "step_type='agent_step' AND step_name LIKE '%%编辑审稿%%'"
        if conds:
            churn_where += " AND " + " AND ".join(conds)
        churn_rows = conn.execute(
            f"SELECT extra FROM agent_trace WHERE {churn_where}", tuple(params)).fetchall()
        scenes = conn.execute(
            f"SELECT scene, COUNT(*) AS n, COALESCE(SUM(token_total),0) AS tokens, "
            f"COALESCE(AVG(duration_ms),0) AS avg_ms "
            f"FROM agent_trace{where} GROUP BY scene", tuple(params)).fetchall()
    report = dict(total)
    report.update(_aggregate_quality([dict(r) for r in qc_rows]))
    churn_dist = {"低": 0, "中": 0, "高": 0}
    for r in churn_rows:
        raw = r["extra"]
        if isinstance(raw, str):
            try:
                import json as _json
                extra = _json.loads(raw)
            except Exception:
                extra = {}
        elif isinstance(raw, dict):
            extra = raw
        else:
            extra = {}
        c = extra.get("churn")
        if c in churn_dist:
            churn_dist[c] += 1
    report["churn_risk_dist"] = churn_dist
    report["scenes"] = {
        r["scene"]: {"calls": r["n"], "tokens": r["tokens"],
                     "avg_duration_ms": round(r["avg_ms"], 1)}
        for r in scenes
    }
    ch_tokens = report["scenes"].get("chapter", {}).get("tokens", 0)
    ch_calls = report["scenes"].get("chapter", {}).get("calls", 0)
    report["per_chapter_avg_tokens"] = round(ch_tokens / ch_calls, 1) if ch_calls else 0
    return {"filter": {"project_id": project_id, "days": int(days or 0)}, "report": report}


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
