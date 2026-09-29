# -*- coding: utf-8 -*-
"""REQ-技能统一 2.1：把 skills_library/writing-techniques/*.md 蒸馏为平台写作技能。

- 来源：`skills_library/writing-techniques/*.md`（有行动指导的写作技法，跳过 _drafts）；
- 角色：按文件名关键词映射到对应 Agent（见 _ROLE_BY_KEYWORD），避免"全局全量注入"撑爆提示词；
- 幂等：按 (source='platform', name) upsert —— 已存在则更新 prompt_append/role/applicable_stages/version，不重复建行；
- 目标：统一 `skills` 表（source='platform', owner_id=0）。

用法：python -m deploy.sync_skills_library
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp import db  # noqa: E402

_TECH_DIR = Path(__file__).resolve().parent.parent / "skills_library" / "writing-techniques"

# 文件名关键词 → 适用角色（对应 agents 的 role_name；未命中回退空=全局）
_ROLE_BY_KEYWORD: dict[str, str] = {
    "黄金三章": "architect",
    "有血有肉的人物": "architect",
    "番茄节奏": "architect",
    "时间线设计": "architect",
    "副本结构": "architect",
    "好小说结局": "writer",
    "改稿标准化": "reviewer",
    "网文爆款反套路": "researcher",
    "防同质化": "writer",
    "模块化动态提示词": "writer",
}


def _strip_date_stem(stem: str) -> str:
    """去掉文件名末尾日期后缀（-20260916），得到技能名。"""
    for i in range(len(stem) - 1, 0, -1):
        if stem[i] == "-" and stem[i + 1:].isdigit() and len(stem[i + 1:]) >= 6:
            return stem[:i]
    return stem


def _parse_frontmatter(text: str) -> dict:
    """解析 Markdown 头部 YAML frontmatter，返回 {title, source, role, stages}。"""
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    k = k.strip().lower()
                    v = v.strip().strip("'\"")
                    if k in ("title", "source", "role", "stages"):
                        meta[k] = v
    return meta


def _role_for(name: str, meta: dict) -> str:
    if meta.get("role"):
        return meta["role"].strip()
    for kw, role in _ROLE_BY_KEYWORD.items():
        if kw in name:
            return role
    return ""


def run() -> dict:
    if not _TECH_DIR.is_dir():
        return {"skipped": True, "reason": f"目录不存在: {_TECH_DIR}"}
    created, updated = 0, 0
    for f in sorted(_TECH_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        meta = _parse_frontmatter(text)
        name = (_strip_date_stem(f.stem) or meta.get("title") or f.stem).strip()[:80]
        role = _role_for(name, meta)
        stages = (meta.get("stages") or "").strip()
        desc = f"来源：{meta.get('source') or 'skills_library/writing-techniques'}"[:255]
        # 幂等 upsert：平台技能按 name 唯一
        existing = db.get_skill_by_name(name)
        if existing:
            db.update_skill(existing["id"], name=name, description=desc, prompt_append=text,
                            role=role, applicable_stages=stages, version="1")
            updated += 1
        else:
            db.create_skill(name=name, description=desc, prompt_append=text, role=role,
                            applicable_stages=stages, version="1")
            created += 1
    return {"created": created, "updated": updated, "source_dir": str(_TECH_DIR)}


if __name__ == "__main__":
    print("同步完成:", run())
