# -*- coding: utf-8 -*-
"""模块1 全链路 trace 测试（P2）。

覆盖：TraceRecorder 记录字段正确、step_order 自增、大文本截断、
NullTraceSink 降级不抛、sink 异常不向上抛、agent_step 与 guard 事件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.pipeline.trace import NullTraceSink, TraceRecorder, TraceSink


class MemorySink(TraceSink):
    def __init__(self):
        self.entries = []

    def record(self, entry: dict) -> None:
        self.entries.append(entry)


@pytest.fixture
def mem_rec():
    rec = TraceRecorder(sink=MemorySink(), trace_id="trace-abc",
                        project_id="proj-1", user_id=7, scene="outline")
    return rec


@pytest.mark.p2
def test_record_fields(mem_rec):
    mem_rec.record(
        step_type="llm_call", step_name="mock mock-model",
        system="sys", user="user", output="out",
        token_in=10, token_out=20, duration_ms=300, success=True, model="m",
        extra={"call_index": 1},
    )
    e = mem_rec.sink.entries[0]
    assert e["trace_id"] == "trace-abc"
    assert e["project_id"] == "proj-1"
    assert e["user_id"] == 7
    assert e["scene"] == "outline"
    assert e["step_type"] == "llm_call"
    assert e["step_order"] == 1
    assert e["token_total"] == 30
    assert e["duration_ms"] == 300
    assert e["success"] == 1
    assert e["model"] == "m"
    assert e["extra"] == {"call_index": 1}


@pytest.mark.p2
def test_step_order_increments(mem_rec):
    mem_rec.record(step_type="agent_step", step_name="a")
    mem_rec.record(step_type="agent_step", step_name="b")
    mem_rec.record(step_type="agent_step", step_name="c")
    orders = [e["step_order"] for e in mem_rec.sink.entries]
    assert orders == [1, 2, 3]


@pytest.mark.p2
def test_large_text_clipped(mem_rec):
    mem_rec.record(step_type="llm_call", system="s" * 20000, user="u" * 50000,
                   output="o" * 100000)
    e = mem_rec.sink.entries[0]
    assert len(e["system_prompt"]) <= 8000
    assert len(e["user_input"]) <= 20000
    assert len(e["raw_output"]) <= 40000


@pytest.mark.p2
def test_null_sink_default_no_error():
    rec = TraceRecorder(trace_id="t", project_id="p")
    rec.record(step_type="llm_call")       # 默认 NullTraceSink，不抛
    rec.record_guard("loop", "detail")
    rec.record_quality(1, {"passed": False}, [{"type": "x"}])


@pytest.mark.p2
def test_sink_error_swallowed():
    class BoomSink(TraceSink):
        def record(self, entry: dict) -> None:
            raise RuntimeError("db down")

    rec = TraceRecorder(sink=BoomSink(), trace_id="t", project_id="p")
    rec.record(step_type="llm_call")       # 不向上抛
    rec.record_agent_step("阶段")          # 不向上抛


@pytest.mark.p2
def test_record_agent_step_and_quality(mem_rec):
    mem_rec.record_agent_step("大纲生成完成", {"chapters": 5})
    mem_rec.record_quality(2, {"passed": False}, [{"type": "结构-字数严重不足"}])
    kinds = [e["step_type"] for e in mem_rec.sink.entries]
    assert kinds == ["agent_step", "quality_check"]
    assert mem_rec.sink.entries[-1]["success"] == 0


@pytest.mark.p2
def test_trace_id_auto_generated():
    rec = TraceRecorder(project_id="p")
    assert rec.trace_id  # 未传时自动生成


@pytest.mark.p2
def test_set_scene_updates():
    rec = TraceRecorder(sink=MemorySink(), trace_id="t", project_id="p")
    rec.set_scene("chapter")
    rec.record(step_type="llm_call")
    assert rec.sink.entries[0]["scene"] == "chapter"
