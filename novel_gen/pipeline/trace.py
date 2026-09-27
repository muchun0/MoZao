# -*- coding: utf-8 -*-
"""
Agent 全链路中间状态持久化（模块1）：TraceSink 接口 + TraceRecorder。

设计：
- TraceSink 抽象落库通道；novel_gen 只依赖接口，MySQL 实现在 webapp/db/traces.py。
- NullTraceSink 为默认降级实现（不落库），保证 novel_gen 可独立离线跑通、
  且任何 sink 异常都不阻塞生成主流程。
- TraceRecorder 持有本次任务上下文（trace_id / project_id / user_id / scene），
  统一组装快照记录并写入 sink；step_order 自增保证链路有序。

可扩展预留：
- scene / step_type 字段支撑阶段2监控仪表盘（成功率、单步耗时按维度聚合）
- user_id 支撑用户维度行为与质量分析
- trace_id 可透传给分布式追踪系统
"""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional


class TraceSink(ABC):
    """快照记录落库通道。实现方负责真正持久化，必须吞掉一切异常（不阻塞主流程）。"""

    @abstractmethod
    def record(self, entry: dict) -> None:
        """写入一条快照记录。实现方自行 try/except，绝不允许向上抛。"""


class NullTraceSink(TraceSink):
    """降级实现：什么都不做。用于未配置落库通道 / 离线测试。"""

    def record(self, entry: dict) -> None:
        pass


# 各字段写入上限（排查质量问题需要全文，但防止异常超大文本撑爆 DB）
_MAX_SYSTEM = 8000       # 系统提示词
_MAX_USER = 20000        # 用户输入/上下文
_MAX_OUTPUT = 40000      # LLM 原始返回（单章正文 2.5w 字 + 余量）
_MAX_ERROR = 2000        # 错误栈


class TraceRecorder:
    """组装并写入一条快照记录。所有方法都不抛异常（record 内部兜底）。"""

    def __init__(
        self,
        sink: Optional[TraceSink] = None,
        trace_id: str = "",
        project_id: str = "",
        user_id: int = 0,
        scene: str = "",
        step_name_prefix: str = "",
    ):
        self.sink = sink if sink is not None else NullTraceSink()
        self.trace_id = trace_id or uuid.uuid4().hex
        self.project_id = project_id
        self.user_id = int(user_id or 0)
        self.scene = scene
        self.step_name_prefix = step_name_prefix
        self._order = 0

    def set_scene(self, scene: str) -> None:
        """切换业务场景（research/outline/world/chapter/revise/review...）。"""
        self.scene = scene

    def _next_order(self) -> int:
        self._order += 1
        return self._order

    def _clip(self, text: str, limit: int) -> str:
        if not text:
            return ""
        return text[:limit]

    def record(
        self,
        step_type: str,
        step_name: str = "",
        system: str = "",
        user: str = "",
        output: str = "",
        token_in: int = 0,
        token_out: int = 0,
        duration_ms: int = 0,
        success: bool = True,
        error: str = "",
        model: str = "",
        extra: Optional[dict] = None,
    ) -> None:
        """写入一条快照记录（吞异常，绝不干扰生成主流程）。"""
        try:
            self.sink.record({
                "trace_id": self.trace_id,
                "project_id": self.project_id,
                "user_id": self.user_id,
                "scene": self.scene,
                "step_type": step_type,
                "step_order": self._next_order(),
                "step_name": (self.step_name_prefix + step_name) if self.step_name_prefix else step_name,
                "system_prompt": self._clip(system, _MAX_SYSTEM),
                "user_input": self._clip(user, _MAX_USER),
                "raw_output": self._clip(output, _MAX_OUTPUT),
                "token_in": int(token_in or 0),
                "token_out": int(token_out or 0),
                "token_total": int(token_in or 0) + int(token_out or 0),
                "duration_ms": int(duration_ms or 0),
                "success": 1 if success else 0,
                "error_info": self._clip(error, _MAX_ERROR),
                "model": model or "",
                "extra": extra or None,
                "created_at": time.time(),
            })
        except Exception:
            # 快照写入绝不允许拖垮生成
            pass

    def record_guard(self, kind: str, detail: str, extra: Optional[dict] = None) -> None:
        """记录成本防护超限事件（模块2）。"""
        self.record(
            step_type="guard",
            step_name=f"cost_guard_{kind}",
            output=detail,
            success=False,
            extra=extra,
        )

    def record_quality(self, chapter_no: int, result: dict, issues: list,
                        auto_revised: bool = False) -> None:
        """记录结果自校验事件（模块4）。P2-4：extra 附带 score/issue_count/auto_revised 供报表聚合。"""
        self.record(
            step_type="quality_check",
            step_name=f"第{chapter_no}章自校验",
            output=result.get("summary", "")[:2000] if result else "",
            success=bool(result and result.get("passed")),
            extra={
                "issues": issues[:50],
                "score": int(result.get("score", 0)) if result else 0,
                "issue_count": len(issues),
                "auto_revised": bool(auto_revised),
            },
        )

    def record_agent_step(self, step_name: str, extra: Optional[dict] = None) -> None:
        """记录流水线业务语义步骤（阶段开始/完成）。"""
        self.record(step_type="agent_step", step_name=step_name, extra=extra)
