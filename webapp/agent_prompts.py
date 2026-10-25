# -*- coding: utf-8 -*-
"""Agent 人设提示词的项目级版本化资源（TECH-Agent用户化与版本化 §3.4，阶段 1B）。

存储结构（项目目录下）：
    projects/<id>/agent_prompts/<agent_safe>/
    ├── v1.md       # 不可变快照（author="default" 为系统懒快照；author="user" 为用户保存）
    ├── v2.md
    └── meta.json   # {"current": 2, "versions": [{"version", "author", "sha256", "created_at"}]}

设计要点：
1. **不可变版本**：每次保存新建 `v<n+1>` 快照，历史版本永不改写——run 侧写与产物版本
   history 里的 `prompt_version`（`<agent>@v<n>`）+ `prompt_sha256` 可据此反查当时原文。
2. **懒快照 v1**：无用户覆盖时，首次读取把系统默认人设快照为 v1（author="default"），
   保证 prompt_version 格式统一（评审 S-2）。
3. 现网提示词由 `pipeline/templates.py` 运行时动态拼装、无静态 per-agent 模板文件，
   故默认人设为各 agent 职责描述常量（`_DEFAULT_PROMPTS`），注入位置为
   `cfg.custom_prompt`（追加到 system 末尾，与现网 custom_prompt 语义一致）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

__all__ = [
    "EDITABLE_AGENTS",
    "get_prompt_meta",
    "save_user_prompt",
]

# 可独立触发 / 可编辑人设的 agent 清单（TECH §4.1 守卫白名单同源）
EDITABLE_AGENTS = (
    "researcher",
    "architect.outline",
    "architect.volume",
    "architect.world",
    "writer.chapter",
)

# 系统默认人设（懒快照 v1 的原文）。现网无静态模板文件，此处为职责描述常量。
_DEFAULT_PROMPTS = {
    "researcher": "你是题材调研助手。基于项目题材与设定做联网调研，产出可参考的题材资料与写作建议。",
    "architect.outline": "你是小说大纲架构师。依据项目设定设计全书大纲，结构完整、节奏合理、冲突递进。",
    "architect.volume": "你是卷纲架构师。依据全书大纲设计指定卷的卷纲，明确本卷主线、高潮与收束。",
    "architect.world": "你是世界观架构师。依据项目设定构建世界观、人物卡与伏笔体系，设定自洽可长线支撑。",
    "writer.chapter": "你是章节写手。依据大纲、世界观与记忆上下文撰写章节正文，文风统一、节奏达标。",
}

_PROMPT_VERSION_FMT = "{agent}@v{n}"


def _agent_safe(agent: str) -> str:
    """agent 名转安全目录名（architect.outline → architect_outline）。"""
    return re.sub(r"[^A-Za-z0-9_]+", "_", agent.strip())


def _agent_dir(store, agent: str) -> Path:
    return Path(store.dir) / "agent_prompts" / _agent_safe(agent)


def _read_meta(agent_dir: Path) -> dict:
    meta_path = agent_dir / "meta.json"
    if not meta_path.exists():
        return {"current": 0, "versions": []}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("versions"), list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"current": 0, "versions": []}


def _write_meta(agent_dir: Path, meta: dict) -> None:
    meta_path = agent_dir / "meta.json"
    tmp = meta_path.with_name(meta_path.name + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, meta_path)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot(store, agent: str, text: str, author: str) -> tuple[str, str]:
    """新建一个不可变版本快照，返回 (prompt_version, sha256)。"""
    agent_dir = _agent_dir(store, agent)
    agent_dir.mkdir(parents=True, exist_ok=True)
    meta = _read_meta(agent_dir)
    n = int(meta.get("current") or 0) + 1
    digest = _sha256(text)
    (agent_dir / f"v{n}.md").write_text(text, encoding="utf-8")
    meta["versions"].append({
        "version": n,
        "author": author,
        "sha256": digest,
        "created_at": time.time(),
    })
    meta["current"] = n
    _write_meta(agent_dir, meta)
    return _PROMPT_VERSION_FMT.format(agent=agent, n=n), digest


def get_prompt_meta(store, agent: str) -> tuple[str, str, str]:
    """读取 agent 当前人设，返回 (text, prompt_version, sha256)。

    无覆盖时把系统默认人设懒快照为 v1（author="default"）后返回；
    meta 损坏或版本文件缺失时重建快照（不抛错，留痕链路不能阻断生成）。
    """
    if agent not in EDITABLE_AGENTS:
        raise ValueError(f"不可编辑人设的 agent: {agent}")
    agent_dir = _agent_dir(store, agent)
    meta = _read_meta(agent_dir)
    current = int(meta.get("current") or 0)
    if current > 0:
        path = agent_dir / f"v{current}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            rec = next((v for v in meta["versions"] if v.get("version") == current), {})
            digest = str(rec.get("sha256") or _sha256(text))
            return text, _PROMPT_VERSION_FMT.format(agent=agent, n=current), digest
    # 懒快照：默认人设落 v1
    text = _DEFAULT_PROMPTS[agent]
    version, digest = _snapshot(store, agent, text, author="default")
    return text, version, digest


def save_user_prompt(store, agent: str, text: str) -> tuple[str, str]:
    """保存用户人设为新版本快照，返回 (prompt_version, sha256)。"""
    if agent not in EDITABLE_AGENTS:
        raise ValueError(f"不可编辑人设的 agent: {agent}")
    text = (text or "").strip()
    if not text:
        raise ValueError("人设内容不能为空")
    return _snapshot(store, agent, text, author="user")
