# -*- coding: utf-8 -*-
"""
架构总控 Agent（Orchestrator）：任务拆解、阶段门禁、冲突仲裁、成果汇总。

REQ-多Agent协作分层优化：把 manager.py 的过程式编排升级为「阶段门禁」式协作——
每个生产阶段产出后，由独立校验 Agent（LogicValidator）做准出校验；发现致命问题
自动触发重生成（≤ stage_gate_retries 轮）；超限由总控按「主题 > 人物 > 世界观」
优先级仲裁并记录，不阻塞整本生成。跨模块冲突统一在此仲裁。

门禁开关：cfg.stage_gate（默认开）/ cfg.stage_gate_retries（默认 2）。
产物文件仍为唯一权威（断点续传不变）：invalidate 回调负责清产物强制重跑。
"""
from __future__ import annotations

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.project import ProjectStore
from .issue_triage import IssueItem, has_fatal
from .validator import LogicValidator
from webapp.logger import get_logger

logger = get_logger("pipeline.orchestrator")


class Orchestrator:
    """架构总控：持有校验 Agent，提供阶段门禁统一入口。"""

    def __init__(self, cfg: NovelConfig, store: ProjectStore,
                 llm: LLMClient, trace=None, validator: LogicValidator | None = None):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.trace = trace
        self.validator = validator or LogicValidator()
        #: 仲裁意见缓冲（本任务内），供最终质量报告/成果整合引用
        self.arbitrations: list[dict] = []

    # ---------- 阶段门禁 ----------
    def run_stage(self, stage: str, produce, invalidate=None,
                  extra=None) -> object:
        """阶段门禁：产出 → 准出校验 → 不合格自动重生成（≤N轮）→ 仲裁放行。

        参数：
            stage      阶段名：bottom_anchors / skeleton / pacing
            produce    无参 callable：执行生产并返回产物对象（幂等，存在则复用）
            invalidate 无参 callable：清除产物文件，强制下一轮重跑（None=不重生成）
            extra      可选附加校验器 callable(product) -> list[IssueItem]
        返回最终产物对象（不因校验失败而抛错，保证整本不阻塞）。
        """
        gate_on = bool(getattr(self.cfg, "stage_gate", True))
        # 离线测试（mock provider）默认跳过门禁，保持改造前后离线生成行为一致
        try:
            if str(getattr(getattr(self.cfg, "llm", None), "provider", "")).lower() == "mock":
                gate_on = False
        except Exception:
            pass
        retries = max(1, int(getattr(self.cfg, "stage_gate_retries", 2) or 2))
        last_issues: list[IssueItem] = []
        product = None
        for attempt in range(retries):
            product = produce()
            if not gate_on or product is None:
                self._trace_step(f"{stage} 门禁跳过" if not gate_on else f"{stage} 无产物")
                return product
            issues = self._collect_issues(stage, product, extra)
            last_issues = issues
            if not has_fatal(issues):
                self._record_pass(stage, issues)
                return product
            # 有致命问题：未达轮次上限则打回重生成
            if attempt < retries - 1 and invalidate is not None:
                self._trace_step(f"{stage} 打回重生成", {
                    "attempt": attempt + 1, "fatal": _fatal_count(issues),
                    "scopes": sorted({i.scope for i in issues}),
                })
                logger.warning("[StageGate] %s 存在致命问题，第 %d 轮打回重生成：%s",
                               stage, attempt + 1, "; ".join(i.desc for i in issues if i.severity == "fatal"))
                invalidate()
                continue
            break
        # 超限：总控仲裁放行（记录问题，不阻塞整本）
        self._arbitrate(stage, last_issues)
        return product

    def _collect_issues(self, stage: str, product, extra) -> list[IssueItem]:
        issues = self.validator.validate_stage(stage, product)
        if extra is not None:
            try:
                issues += extra(product) or []
            except Exception:
                pass
        return issues

    def _record_pass(self, stage: str, issues: list[IssueItem]) -> None:
        if issues:
            self._trace_step(f"{stage} 门禁通过", {
                "non_fatal": len(issues),
                "scopes": sorted({i.scope for i in issues}),
            })

    def _arbitrate(self, stage: str, issues: list[IssueItem]) -> None:
        """超限仲裁：按主题 > 人物 > 世界观优先级记录最终方向（本阶段放行）。"""
        fatal = [i for i in issues if i.severity == "fatal"]
        entry = {
            "stage": stage,
            "fatal": [i.to_dict() for i in fatal],
            "note": "超轮仍存在致命问题，由总控仲裁放行（不阻塞整本）；建议人工/后续阶段修复",
        }
        self.arbitrations.append(entry)
        self._trace_step(f"{stage} 仲裁放行", {"fatal": len(fatal)})
        logger.warning("[Orchestrator] %s 仲裁放行（%d 致命）：%s",
                       stage, len(fatal), "; ".join(i.desc for i in fatal))

    def _trace_step(self, name: str, extra: dict | None = None) -> None:
        if self.trace is not None:
            try:
                self.trace.record_agent_step(name, extra=extra)
            except Exception:
                pass


def _fatal_count(issues: list[IssueItem]) -> int:
    return sum(1 for i in issues if i.severity == "fatal")
