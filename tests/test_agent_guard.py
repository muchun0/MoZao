# -*- coding: utf-8 -*-
"""模块2 循环与 Token 成本防护测试（P1）。

覆盖：循环上限拦截、按场景限次、Token 阈值拦截、开关关闭等同改造前、
LLMClient 模板方法与 guard 的集成、超限事件写入 recorder、
webapp 生成线程对 AgentGuardError 的降级处理（保留产物 + 提示）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import AgentGuardConfig
from novel_gen.llm.providers import MockClient
from novel_gen.pipeline.guard import AgentGuard, AgentGuardError
from novel_gen.pipeline.trace import TraceRecorder


# ---------- guard 基础 ----------

@pytest.mark.p1
def test_loop_limit_raises():
    guard = AgentGuard(AgentGuardConfig(max_loops=2))
    guard.before_call()
    guard.before_call()
    with pytest.raises(AgentGuardError):
        guard.before_call()


@pytest.mark.p1
def test_scene_limit_raises():
    guard = AgentGuard(AgentGuardConfig(max_loops=100, scene_limits={"outline": 3}))
    guard.enter_scene("outline")
    guard.before_call()
    guard.before_call()
    guard.before_call()
    with pytest.raises(AgentGuardError):
        guard.before_call()
    # 切场景后计数独立
    guard.enter_scene("world")
    guard.before_call()  # 不抛


@pytest.mark.p1
def test_token_limit_raises():
    guard = AgentGuard(AgentGuardConfig(max_tokens=100))
    usage = type("U", (), {"last_total_tokens": 60})()
    guard.after_call(usage)
    usage2 = type("U", (), {"last_total_tokens": 60})()
    with pytest.raises(AgentGuardError):
        guard.after_call(usage2)


@pytest.mark.p1
def test_disabled_guard_passthrough():
    guard = AgentGuard(AgentGuardConfig(enabled=False, max_loops=1, max_tokens=1))
    for _ in range(10):
        guard.before_call()
        guard.after_call(type("U", (), {"last_total_tokens": 100})())


@pytest.mark.p1
def test_guard_event_written_to_recorder():
    class Sink:
        def __init__(self):
            self.entries = []
        def record(self, e):
            self.entries.append(e)
    sink = Sink()
    rec = TraceRecorder(sink=sink, trace_id="t1", project_id="p1")
    guard = AgentGuard(AgentGuardConfig(max_loops=2), recorder=rec)
    guard.before_call()
    guard.before_call()
    with pytest.raises(AgentGuardError):
        guard.before_call()
    assert sink.entries, "超限事件应写入 recorder"
    assert sink.entries[-1]["step_type"] == "guard"
    assert sink.entries[-1]["success"] == 0


# ---------- LLM 模板方法与 guard 集成 ----------

@pytest.mark.p1
def test_llm_chat_guard_integration():
    llm = MockClient()
    guard = AgentGuard(AgentGuardConfig(max_loops=2))
    llm.guard = guard
    llm.chat("sys", "user")
    llm.chat("sys", "user")
    with pytest.raises(AgentGuardError):
        llm.chat("sys", "user")


@pytest.mark.p1
def test_llm_chat_token_accumulate():
    """真实 provider 的 usage 由 _chat_impl 上报；Mock 无 usage 不累计（不误拦）。"""
    llm = MockClient()
    guard = AgentGuard(AgentGuardConfig(max_tokens=10_000_000))
    llm.guard = guard
    for _ in range(50):
        llm.chat("sys", "user")
    assert guard.total_tokens == 0  # mock 无 usage 上报


@pytest.mark.p1
def test_llm_trace_recorder_attached():
    """LLM 模板方法自动埋点（recorder 挂载后每调用一条 llm_call 记录）。"""
    class Sink:
        def __init__(self):
            self.entries = []
        def record(self, e):
            self.entries.append(e)
    rec = TraceRecorder(sink=Sink(), trace_id="t1", project_id="p1", scene="chapter")
    llm = MockClient()
    llm.recorder = rec
    text = llm.chat("sys", "user")
    assert text
    assert len(rec.sink.entries) == 1
    e = rec.sink.entries[0]
    assert e["step_type"] == "llm_call"
    assert e["scene"] == "chapter"
    assert e["project_id"] == "p1"
    assert e["success"] == 1


# ---------- webapp 生成线程降级处理 ----------

@pytest.mark.p1
def test_run_generation_guard_error_keeps_output(tmp_path, monkeypatch):
    """成本超限：捕获 AgentGuardError，保留已生成产物 + 超限提示，不抛 500。"""
    from webapp import core as server
    from webapp.core import _RunHandle
    from novel_gen.store.project import ProjectStore
    from novel_gen.config import NovelConfig

    store = ProjectStore.create(tmp_path / "gp", NovelConfig(title="测试书", total_words=5000))
    store.dir.joinpath("chapters").mkdir(exist_ok=True)
    handle = _RunHandle()

    def _boom(store_obj, trace_id=""):
        raise AgentGuardError("[成本防护] 单任务 LLM 调用次数超过上限（80 次），已终止生成。")

    monkeypatch.setattr(server, "_open_pipeline", _boom)
    monkeypatch.setattr(server, "_mark_task", lambda *a, **k: None)
    monkeypatch.setattr(server, "_clear_task", lambda *a, **k: None)
    monkeypatch.setattr(server, "_settle_price", lambda *a, **k: None)
    monkeypatch.setattr(server, "_GEN_SEMAPHORE", type("S", (), {
        "release": lambda self: None, "acquire": lambda self, **k: True})())

    # 直接调用线程函数（同步执行）
    server._run_generation(store, handle)
    assert "成本防护" in (handle.error or "")
    assert "已保留当前已生成内容" in (handle.error or "")
    # 已生成产物保留：failed.json 落盘、state.error 可展示
    failed = store.dir / "failed.json"
    assert failed.exists()
    data = __import__("json").loads(failed.read_text(encoding="utf-8"))
    assert data.get("guard") is True


# ---------- P0-8 按章重置场景计数（2026-10-10） ----------

@pytest.mark.p1
def test_reset_scene_counts_only_scene():
    """reset_scene_counts 只清场景计数，保留全局 loop/token 上限
    （v6 第 4-7 章 review 跨章累计 25 次被拦、一致性校验全停的修复）。"""
    guard = AgentGuard(AgentGuardConfig(max_loops=10, scene_limits={"review": 3}))
    guard.enter_scene("review")
    guard.before_call()
    guard.before_call()
    guard.before_call()
    with pytest.raises(AgentGuardError):
        guard.before_call()
    # 按章重置：场景计数清零，全局 loop 计数保留
    guard.reset_scene_counts()
    guard.enter_scene("review")
    guard.before_call()                      # 不抛（场景配额恢复）
    assert guard.loop_count == 5             # 全局计数未清零（3 次正常 + 1 次抛错前 + 重置后 1 次）
    # 全局上限仍生效
    with pytest.raises(AgentGuardError):
        for _ in range(6):
            guard.before_call()
