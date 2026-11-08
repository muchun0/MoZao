# -*- coding: utf-8 -*-
"""R16：质检评委有界并发、连接短路、总时限截断与降级留痕。

全部离线、零计费：用脚本化假 LLM 统计真实调用次数与并发峰值，不读 D:\\NovelData。
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig                                # noqa: E402
from novel_gen.llm.base import LLMError                                 # noqa: E402
from novel_gen.pipeline.quality import checker as qc                   # noqa: E402
from novel_gen.pipeline.quality.checker import (                        # noqa: E402
    QUALITY_PARALLEL_WORKERS, QualityChecker, QualityDeadlineExceeded,
    QualityUnavailable,
)
from novel_gen.store.project import ProjectStore                        # noqa: E402
from novel_gen.store.schemas import Chapter                             # noqa: E402
from webapp import chapter_quality_service as cqs                       # noqa: E402
from webapp.routers import projects_chapters as pc                      # noqa: E402

#: 满足全部评委 JSON 契约的合并返回（各评委只取自己需要的键）
_PAYLOAD = {
    "beats": [],
    "connective_excess": False,
    "emotion_excess": False,
    "filler_overuse": False,
    "zero_direct": False,
    "追更欲": 4,
    "issues": [],
    "contradictions": [],
    "violations": [],
}
#: 无面板数字/无重复句的短正文：确定性检查不发起额外 LLM，便于精确断言调用次数
_SHORT = "改写正文" * 40
#: 正常路径正文：长度过 60% 门槛、句末有标点、句子唯一，避免确定性大改干扰
_LONG = "".join(
    f"他走过第{i}条街，看见第{i}盏灯还亮着，心里数着还剩的转角。" for i in range(1, 80))


class _ScriptedLLM:
    """脚本化 LLM：统计真实调用数/并发峰值，按 system 文本区分评委并注入失败。"""

    def __init__(self, delay: float = 0.0, fail_keys: frozenset[str] = frozenset(),
                 fail_all: bool = False, contradiction: bool = False):
        self.delay = delay
        self.fail_keys = set(fail_keys)
        self.fail_all = fail_all
        self.contradiction = contradiction
        self._lock = threading.Lock()
        self.calls = 0      # 真正进入本假模型（= 底层 chat）的次数
        self.active = 0
        self.peak = 0

    @staticmethod
    def _key(system: str) -> str:
        if "追更读者" in system:
            return "reader"
        if "前后自相矛盾" in system:
            return "consistency"
        if "规则条款一致性裁判" in system:
            return "rule"
        return "other"

    def chat(self, system: str, user: str, **kwargs) -> str:
        key = self._key(system)
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.fail_all:
                raise LLMError("[deepseek] 调用失败: Connection error.")
            if key in self.fail_keys:
                raise ValueError("评委返回无法解析为 JSON 对象")
            data = dict(_PAYLOAD)
            if key == "consistency" and self.contradiction:
                data["contradictions"] = [{
                    "type": "身体状态",
                    "detail": "前一处写左臂中箭，后一处又用左臂拉满长弓",
                    "suggestion": "统一为右臂受伤",
                }]
            return json.dumps(data, ensure_ascii=False)
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def store(tmp_path):
    cfg = NovelConfig(title="测试书", genre="玄幻", total_words=10000, chapter_words=2500)
    return ProjectStore.create(tmp_path / "q", cfg)


def _mk_checker(store: ProjectStore, llm, **flags) -> QualityChecker:
    cfg = store.load_config()
    for key, value in flags.items():
        setattr(cfg, key, value)
    return QualityChecker(cfg, llm, store, trace=None, guard=None, memory=None)


def _check(checker: QualityChecker, content: str = _SHORT, budget: float = 10.0) -> dict:
    return checker.check(1, "第1章", "本章目标", content, parallel=True, total_budget=budget)


def _failed_judges(result: dict) -> set[str]:
    return {f["judge"] for f in result.get("llm_failures") or []}


# ---------------------------------------------------------------------------
# 1. 有界并发生效：调用计数 + 并发峰值 + 墙钟小于串行累加
# ---------------------------------------------------------------------------

def test_bounded_parallelism_and_all_judges_succeed(store):
    llm = _ScriptedLLM(delay=0.2)
    checker = _mk_checker(store, llm)
    started = time.monotonic()
    result = _check(checker)
    wall = time.monotonic() - started

    # beats / ai_taste / reader / consistency 四路真实调用（panel 无数字面板不发）
    assert llm.calls == 4
    assert llm.peak <= QUALITY_PARALLEL_WORKERS
    assert llm.peak >= 2, "应确实并行执行，而不是退化为串行"
    assert result["llm_failures"] == []
    assert set(result["llm_attempts"]) == {"beats", "ai_taste", "reader", "consistency"}
    # 并行墙钟必须小于「各路耗时之和」（串行等价耗时）
    serial_equivalent = sum(result["llm_timings_ms"].values()) / 1000.0
    assert wall < serial_equivalent


# ---------------------------------------------------------------------------
# 2. 单路失败不影响其它路；关键评委失败仍 fail-closed
# ---------------------------------------------------------------------------

def test_single_judge_failure_does_not_affect_others(store):
    llm = _ScriptedLLM(fail_keys=frozenset({"reader"}), contradiction=True)
    checker = _mk_checker(store, llm)
    result = _check(checker)

    failed = _failed_judges(result)
    assert "reader" in failed
    assert "consistency" not in failed
    # reader 失败不阻断 consistency 的判定结果
    assert any(i.get("type", "").startswith("自相矛盾") for i in result["issues"])
    assert result["llm_failures"][0]["reason"] == "不可解析"
    assert result["llm_failures"][0]["critical"] is True


def test_critical_judge_failure_still_fail_closed_without_writes(store):
    llm = _ScriptedLLM(fail_keys=frozenset({"reader"}))
    checker = _mk_checker(store, llm)
    with pytest.raises(QualityUnavailable):
        checker.recheck(1, "第1章", "本章目标", _SHORT,
                        parallel=True, total_budget=10.0)
    assert not (store.dir / "review.json").exists()


# ---------------------------------------------------------------------------
# 3. 连接类失败短路：未发起的评委不再逐路等满，且不计入失败
# ---------------------------------------------------------------------------

def test_connection_failure_short_circuits_remaining_judges(store, monkeypatch, caplog):
    # 固定为单 worker，保证派发顺序确定：panel → beats（失败触断）→ 其余跳过
    monkeypatch.setattr(qc, "QUALITY_PARALLEL_WORKERS", 1)
    llm = _ScriptedLLM(fail_all=True)
    checker = _mk_checker(store, llm)

    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.quality"):
        result = _check(checker, budget=6.0)

    # panel 不发调用；beats 首次连接失败即触发短路；后续评委不再进入底层 chat
    assert llm.calls == 1
    assert _failed_judges(result) == {"beats"}
    skipped = {x["judge"] for x in result.get("llm_skipped") or []}
    assert skipped >= {"ai_taste", "reader", "consistency"}
    # 未发起 ≠ 失败：短路项不进 llm_failures
    assert "reader" not in _failed_judges(result)
    assert "连接类失败短路" in caplog.text


# ---------------------------------------------------------------------------
# 4. 总时限截断：显式日志 + 503 语义（QualityDeadlineExceeded）
# ---------------------------------------------------------------------------

def test_total_budget_truncation_is_logged_and_fail_closed(store, caplog):
    llm = _ScriptedLLM(delay=0.8)
    checker = _mk_checker(store, llm)

    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.quality"):
        with pytest.raises(QualityDeadlineExceeded):
            checker.recheck(1, "第1章", "本章目标", _SHORT,
                            parallel=True, total_budget=0.2)

    assert "reason=总时限截断" in caplog.text
    assert "非自然失败" in caplog.text
    assert not (store.dir / "review.json").exists()


# ---------------------------------------------------------------------------
# 5. 开关/降频未发起：不报降级、不计失败
# ---------------------------------------------------------------------------

def test_gated_off_judges_emit_no_degrade_log(store, caplog):
    llm = _ScriptedLLM()
    checker = _mk_checker(
        store, llm,
        llm_semantic_beats=False, llm_semantic_ai_taste=False,
        consistency_llm_check=False, high_quality_mode=False,
        plot_reviewer=False, rule_consistency_check=False,
    )

    with caplog.at_level(logging.WARNING, logger="novel_gen.pipeline.quality"):
        result = _check(checker)

    assert result["llm_attempts"] == ["reader"]
    assert result["llm_failures"] == []
    assert result["llm_skipped"] == []
    assert "[quality-degrade]" not in caplog.text


# ---------------------------------------------------------------------------
# 6/7. 路由正常 200 且写回一致；关键评委失败仍 503 且不写盘
# ---------------------------------------------------------------------------

@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(pc, "_check_project_access", lambda project_id, user: None)
    monkeypatch.setattr(pc, "_find_project", lambda project_id: store)
    monkeypatch.setattr(pc, "_open_pipeline", lambda s, trace_id="": SimpleNamespace(
        cfg=store.load_config(), llm=object(), trace=None, guard=None))
    return store


def _seed(store: ProjectStore, content: str = _LONG) -> None:
    """v1 = 生成稿；v2 = 「按此改」改写稿（重检对象 = 当前选中 v2）。"""
    store.save_chapter(Chapter(no=1, title="第1章", content="初稿正文" * 40, summary="摘要"))
    store.save_chapter(Chapter(no=1, title="第1章", content=content, summary="摘要"),
                       source="rewrite")


def _quality(store: ProjectStore, v: int) -> dict | None:
    return next(x for x in store.list_versions(1) if x["v"] == v)["quality"]


def test_router_normal_path_returns_200_and_writes_back(wired, monkeypatch):
    _seed(wired)
    checker = _mk_checker(wired, _ScriptedLLM())
    monkeypatch.setattr(cqs, "_build_checker", lambda pipeline, s: checker)

    out = pc.recheck_chapter_quality("pid", 1, req={"version": 2}, user={"id": 1})

    assert out["reused"] is False and out["version"] == 2
    quality = _quality(wired, 2)
    assert quality["score"] == out["score"]
    assert quality["issue_count"] == out["issue_count"]
    review = json.loads((wired.dir / "review.json").read_text(encoding="utf-8"))
    rec = next(r for r in review if r["chapter"] == 1)
    assert rec["quality_check"]["score"] == out["score"]
    assert rec["auto_revised"] is False
    # 正文与 HEAD 必须保持原样（重检只判定，不改写）
    assert wired.load_chapter(1).content == _LONG


def test_router_critical_failure_returns_503_without_writes(wired, monkeypatch):
    _seed(wired)
    checker = _mk_checker(wired, _ScriptedLLM(fail_keys=frozenset({"reader"})))
    monkeypatch.setattr(cqs, "_build_checker", lambda pipeline, s: checker)

    with pytest.raises(HTTPException) as ei:
        pc.recheck_chapter_quality("pid", 1, req={"version": 2}, user={"id": 1})

    assert ei.value.status_code == 503
    assert ei.value.detail == "模型暂不可用，请稍后重试"
    assert _quality(wired, 2) is None
    assert not (wired.dir / "review.json").exists()
