# -*- coding: utf-8 -*-
"""Agent run 上下文与侧写（TECH-Agent用户化与版本化 §3.3 / §3.9，阶段 1B）。

职责：
1. `RunContext`：一次 agent run 的显式上下文（run_id / agent / skill_ids / user_prompt），
   挂在 `ProjectStore` 实例上（非线程局部），`commit` 经 `store.current_run()` 取 `run_id`；
   无 run 上下文时 `current_run()` 返回 None，调用方降级为空串不中断（B-3 方案 A）。
2. run 侧写文件 `agent_runs/<run_id>.json`：provenance 留痕（用户 prompt 全文 ≤4KB、
   model/temperature/seed、trace_id、计费明细、产物版本列表、compliance 标记）。
3. 每项目保留最近 200 个 run（按 finished_at 删最旧，评审 I-4）。

为什么独立成模块：`project.py` 已超 1000 行红线（AGENTS §11.1），
RunContext 是独立内聚概念（一次 run 的上下文与留痕），不堆进 project.py。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "AGENT_RUNS_KEEP",
    "RunContext",
    "prune_agent_runs",
    "read_run_file",
    "write_run_file",
]

# 每项目保留最近 run 侧写数量上限（TECH §3.6 / 评审 I-4）
AGENT_RUNS_KEEP = 200
# user_prompt 留痕上限（TECH §3.3：≤4KB，超出截断并标记）
_USER_PROMPT_MAX_BYTES = 4 * 1024


@dataclass
class RunContext:
    """一次 agent run 的显式上下文。

    Attributes:
        run_id: 本次 run 唯一 id（uuid hex）。
        agent: 触发 agent（researcher / architect.outline / architect.volume /
            architect.world / writer.chapter）。
        skill_ids: 本 run 挂载的 Skill id 列表（≤5，TECH §6.7）。
        user_prompt: 用户本次输入的 prompt 原文（user prompt 类，与 system 人设区分）。
        task_id: webapp 侧任务 id（task.json 关联，重启后沿用，评审 I-6）。
        started_at: run 开始时间戳。
    """

    run_id: str
    agent: str
    skill_ids: tuple[int, ...] = ()
    user_prompt: str = ""
    task_id: str = ""
    started_at: float = field(default_factory=time.time)


def _runs_dir(store_dir: Path) -> Path:
    return Path(store_dir) / "agent_runs"


def _clip_user_prompt(text: str) -> tuple[str, bool]:
    """user_prompt 截断到 4KB（按 UTF-8 字节），返回 (文本, 是否被截断)。"""
    raw = (text or "").encode("utf-8")
    if len(raw) <= _USER_PROMPT_MAX_BYTES:
        return text or "", False
    # 按字节截后回解码，忽略半个字符
    return raw[:_USER_PROMPT_MAX_BYTES].decode("utf-8", errors="ignore"), True


def write_run_file(store_dir: Path, payload: dict[str, Any]) -> Path:
    """写 run 侧写 `agent_runs/<run_id>.json`（原子写），随后按上限清理。

    payload 必填字段见 TECH §3.3：run_id / task_id / project_id / agent /
    started_at / finished_at / status(ok|failed|stopped) / params /
    prompt_version / prompt_sha256 / user_prompt / skill_ids / model /
    temperature / seed / trace_id / billing_mode / billing / outputs /
    compliance_rejected。user_prompt 在此统一截断并打标记。
    """
    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise ValueError("run 侧写缺少 run_id")
    text, truncated = _clip_user_prompt(str(payload.get("user_prompt") or ""))
    payload = dict(payload)
    payload["user_prompt"] = text
    payload["user_prompt_truncated"] = truncated

    runs_dir = _runs_dir(store_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"{run_id}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    prune_agent_runs(store_dir)
    return path


def read_run_file(store_dir: Path, run_id: str) -> Optional[dict]:
    """读单个 run 侧写；不存在/损坏返回 None。"""
    path = _runs_dir(store_dir) / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def prune_agent_runs(store_dir: Path, keep: int = AGENT_RUNS_KEEP) -> int:
    """按 finished_at 保留最近 keep 个 run 侧写，删最旧；返回删除数。"""
    runs_dir = _runs_dir(store_dir)
    if not runs_dir.is_dir():
        return 0
    files = [p for p in runs_dir.glob("*.json") if not p.name.endswith(".tmp")]
    if len(files) <= keep:
        return 0

    def _finished_at(p: Path) -> float:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return float(data.get("finished_at") or 0.0)
        except (json.JSONDecodeError, OSError, ValueError):
            return 0.0  # 损坏文件排最前优先删

    files.sort(key=_finished_at)
    removed = 0
    for p in files[: len(files) - keep]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed
