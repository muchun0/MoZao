# -*- coding: utf-8 -*-
"""
循环与 Token 成本防护（模块2）。

- AgentGuard 挂在 LLMClient 上，由模板方法 chat 在每次调用前后驱动：
    before_call()：循环计数（全局 + 按场景），超限抛 AgentGuardError；
    after_call(usage)：累计 Token，超阈值抛 AgentGuardError。
- 超限不静默：事件写入 trace（step_type=guard），供阶段2成本分析/告警。
- enabled=False 时完全旁路（等同改造前行为），用于紧急降级。

超限处理约定（webapp 侧）：捕获 AgentGuardError 后不抛 500，
返回「当前已生成的有效结果 + 超限提示」，产物文件逐步落盘天然满足断点续跑。
"""
from __future__ import annotations

from typing import Optional

from ..config import AgentGuardConfig


class AgentGuardError(Exception):
    """Agent 成本防护超限（循环次数 / Token 消耗）。消息面向用户可读。"""


class AgentGuard:
    def __init__(self, cfg: AgentGuardConfig, recorder=None):
        self.cfg = cfg
        self.recorder = recorder  # TraceRecorder | None
        self.loop_count = 0
        self.total_tokens = 0
        self.scene = ""
        self._scene_counts: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled)

    def enter_scene(self, scene: str) -> None:
        """切换业务场景（research/outline/world/chapter/revise/review...）。"""
        self.scene = scene or ""
        if self.recorder is not None:
            self.recorder.set_scene(scene)

    def reset(self) -> None:
        """重置计数（一般不调用：guard 生命周期 = 单任务生命周期）。"""
        self.loop_count = 0
        self.total_tokens = 0
        self._scene_counts.clear()

    def reset_scene_counts(self) -> None:
        """P0-8：只清场景计数（保留全局 loop/token 上限）。

        背景：v6 第 4-7 章「场景[review]调用次数超过上限（25 次）」——review 场景
        计数跨章累计（guard 生命周期=单任务），第 1-3 章已耗尽 25 次配额，后续章
        一致性校验（_run_verify）全部被 guard 拦停。生成每章开始时调用一次，
        让每章享有独立场景配额；全局 loop/token 上限不受影响。
        """
        self._scene_counts.clear()

    # ---------- LLMClient 模板方法驱动 ----------
    def before_call(self) -> None:
        if not self.enabled:
            return
        self.loop_count += 1
        if self.loop_count > self.cfg.max_loops:
            self._hit("loop", f"单任务 LLM 调用次数超过上限（{self.cfg.max_loops} 次），已终止生成。")
            return
        if self.scene:
            self._scene_counts[self.scene] = self._scene_counts.get(self.scene, 0) + 1
            limit = (self.cfg.scene_limits or {}).get(self.scene)
            if limit and self._scene_counts[self.scene] > int(limit):
                self._hit("scene_loop",
                          f"场景[{self.scene}]调用次数超过上限（{limit} 次），已终止生成。")

    def after_call(self, usage) -> None:
        if not self.enabled:
            return
        self.total_tokens += int(getattr(usage, "last_total_tokens", 0) or 0)
        if self.total_tokens > self.cfg.max_tokens:
            self._hit("token",
                      f"单任务 Token 消耗超过上限（{self.cfg.max_tokens}），已终止生成。")

    def _hit(self, kind: str, detail: str) -> None:
        # 超限事件留痕（快照写入自身兜底，不抛错）
        if self.recorder is not None:
            try:
                self.recorder.record_guard(
                    kind, detail,
                    extra={"loop_count": self.loop_count, "total_tokens": self.total_tokens,
                           "scene": self.scene},
                )
            except Exception:
                pass
        raise AgentGuardError(f"[成本防护] {detail}")
