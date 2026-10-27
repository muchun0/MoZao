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
3. **默认人设 = 真实角色底色全文**：各 agent 的默认人设直接读取
   `novel_gen/pipeline/agents/prompts/*.md`（角色底色，会被拼到具体任务提示词之前），
   按 agent 组装对应的 md 组合（见 `_DEFAULT_PROMPT_FILES`），而不是一句话占位描述。
   注入位置为各 generator/engine 的 role_prompt（system 开头，见 stages/research 的
   `_sys` 与 `cfg.persona_override`）。
4. **安全清洗**：用户保存的人设属用户可写内容，落快照前统一 `sanitize_system_text`
   剥离注入话术（与 custom_prompt / 用户技能同源，纵深防御）。
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

# 角色底色 md 目录（webapp → novel_gen 允许的依赖方向）
_PROMPTS_DIR = (
    Path(__file__).resolve().parent.parent
    / "novel_gen" / "pipeline" / "agents" / "prompts"
)

# 各 agent 的默认人设 = 对应角色底色 md 的有序拼接（与运行时 role_prompt 同源）。
# - 大纲架构师：架构师 + 节奏伏笔
# - 世界观架构师：架构师 + 主题人物 + 世界观规则
# - 卷纲架构师：架构师（卷纲复用结构判断标准）
# - 写手：写手
# - 调研员：调研员
_DEFAULT_PROMPT_FILES: dict[str, list[str]] = {
    "researcher": ["researcher.md"],
    "architect.outline": ["architect.md", "pacing.md"],
    "architect.world": ["architect.md", "theme_character.md", "world_rules.md"],
    "architect.volume": ["architect.md"],
    "writer.chapter": ["writer.md"],
}

# 默认人设读取缓存（md 内容在进程内不变，避免每次读盘）
_default_cache: dict[str, str] = {}

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


def _load_default_prompt(agent: str) -> str:
    """读取并拼接该 agent 的默认角色底色 md（带进程内缓存）。"""
    cached = _default_cache.get(agent)
    if cached is not None:
        return cached
    parts: list[str] = []
    for name in _DEFAULT_PROMPT_FILES[agent]:
        path = _PROMPTS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"角色底色 md 缺失: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if text:
            parts.append(text)
    text = "\n\n".join(parts)
    _default_cache[agent] = text
    return text


def get_prompt_meta(store, agent: str) -> tuple[str, str, str]:
    """读取 agent 当前人设，返回 (text, prompt_version, sha256)。

    无覆盖时把系统默认人设（角色底色 md 全文）懒快照为 v1（author="default"）后返回；
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
    # 懒快照：默认人设（角色底色 md 全文）落 v1
    text = _load_default_prompt(agent)
    version, digest = _snapshot(store, agent, text, author="default")
    return text, version, digest


def save_user_prompt(store, agent: str, text: str) -> tuple[str, str]:
    """保存用户人设为新版本快照，返回 (prompt_version, sha256)。

    用户内容属可写文本，落快照前先 sanitize 剥离注入话术；清洗后无有效内容则拒绝。
    """
    if agent not in EDITABLE_AGENTS:
        raise ValueError(f"不可编辑人设的 agent: {agent}")
    text = (text or "").strip()
    if not text:
        raise ValueError("人设内容不能为空")
    from novel_gen.safety import sanitize_system_text
    cleaned = sanitize_system_text(text)
    if not cleaned:
        raise ValueError("人设内容包含异常指令，无法保存")
    return _snapshot(store, agent, cleaned, author="user")
