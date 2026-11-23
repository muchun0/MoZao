# -*- coding: utf-8 -*-
"""角色契约（REQ-R32 F2 / TECH-R32 §7）。

单一事实来源：Supervisor 只认角色，不认 agent；角色→可派发 agent、可执行节点、
必需产物的映射全部收敛在这里。非法组合在入口 400，不静默改派。

约定：
- 外部客户端（HTTP `agent-runs`）只能派发 4 个既有 agent，且必须显式带 `role`；
  不传/传错 → 400。角色→agent 必须精确匹配，不做自动映射。
- 内部 step（reviewer/loremaster/repair）不开放外部 HTTP，只由 Supervisor 派发。
- reviewer/loremaster 是逻辑角色，物理执行者是同一个 `write_chapter` 的章后步骤；
  派发签名 `agent=write_chapter, action=review|loremaster`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 外部 HTTP 允许的 agent（与 generate_service.ALLOWED_AGENTS 对齐）
EXTERNAL_AGENTS = ("researcher", "architect.outline", "architect.world",
                   "architect.volume", "writer.chapter")
# 逻辑角色可使用的“物理执行入口”
INTERNAL_ACTIONS = ("review", "loremaster", "repair")
# agent → 默认角色：老客户端不带 role 时用于标注，避免 on 模式下把旧入口一刀切 400
AGENT_ROLE = {
    "researcher": "researcher",
    "architect.outline": "planner",
    "architect.world": "worldbuilder",
    "architect.volume": "volume",
    "writer.chapter": "writer",
}


@dataclass(frozen=True)
class RoleSpec:
    name: str
    label: str
    agent: str                  # 物理 agent（None 表示仅逻辑角色，不可外部派发）
    actions: tuple[str, ...]    # 可执行动作；空=按 agent 默认
    node_kinds: tuple[str, ...]  # 允许的节点类型
    external: bool              # 是否允许外部 HTTP 派发
    required_outputs: tuple[str, ...] = field(default=())  # 产物校验项（store 侧解释）


ROLE_REGISTRY: dict[str, RoleSpec] = {
    "researcher": RoleSpec(
        "researcher", "研究员", "researcher", (), ("stage:research",), True,
        ("research",),
    ),
    "planner": RoleSpec(
        "planner", "规划师", "architect.outline", (), ("stage:outline",), True,
        ("outline",),
    ),
    "worldbuilder": RoleSpec(
        "worldbuilder", "世界观架构师", "architect.world", (), ("stage:world",), True,
        ("world",),
    ),
    "volume": RoleSpec(
        "volume", "卷纲架构师", "architect.volume", (), ("stage:outline",), True,
        ("outline",),
    ),
    "writer": RoleSpec(
        "writer", "写手", "writer.chapter", ("write", "repair"),
        ("stage:chapters", "chapter"), True,
        ("chapter",),
    ),
    "reviewer": RoleSpec(
        "reviewer", "审校编辑", "writer.chapter", ("review",), ("chapter",), False,
        ("review",),
    ),
    "loremaster": RoleSpec(
        "loremaster", "设定管理员", "writer.chapter", ("loremaster",), ("chapter",), False,
        ("commit",),
    ),
}


class RoleContractError(ValueError):
    """角色契约不满足（入口 400）。"""


def get_role(role: str) -> RoleSpec:
    spec = ROLE_REGISTRY.get(str(role or ""))
    if spec is None:
        raise RoleContractError(f"未知角色：{role or '(空)'}")
    return spec


def infer_role(agent: str) -> str:
    """agent → 默认角色；未知 agent 返回空（由调用方决定是否拒绝）。"""
    return AGENT_ROLE.get(str(agent or ""), "")


def node_kind(node_id: str) -> str:
    """节点类型：stage:xxx / chapter:N。"""
    nid = str(node_id or "")
    if nid.startswith("stage:"):
        return nid
    if nid.startswith("chapter:"):
        return "chapter"
    raise RoleContractError(f"非法节点：{nid or '(空)'}")


def _chapter_no(node_id: str) -> int:
    try:
        return int(str(node_id).split(":", 1)[1])
    except Exception as exc:
        raise RoleContractError(f"非法章节节点：{node_id}") from exc


def validate_dispatch(role: str, agent: str | None, node_id: str, action: str = "",
                      *, internal: bool = False) -> RoleSpec:
    """校验 role↔agent↔node↔action；不合法抛 RoleContractError（Web 层转 400）。"""
    spec = get_role(role)
    kind = node_kind(node_id)
    if kind not in spec.node_kinds:
        raise RoleContractError(f"角色 {role} 不能执行节点 {node_id}")
    if action and spec.actions and action not in spec.actions:
        raise RoleContractError(f"角色 {role} 不支持动作 {action}")
    if agent is not None and str(agent) != spec.agent:
        raise RoleContractError(
            f"角色 {role} 只能由 {spec.agent} 执行，拒绝 {agent}")
    if spec.external and agent is None:
        raise RoleContractError(f"角色 {role} 派发必须显式指定 agent")
    if not internal and not spec.external:
        raise RoleContractError(f"角色 {role} 不开放外部派发")
    return spec


def required_outputs(role: str) -> tuple[str, ...]:
    return get_role(role).required_outputs


def outcome_check(store, role: str, node_id: str) -> list[str]:
    """机械校验“说完了但产物没到”的缺失项（空列表=通过）。

    只读产物、不读正文；inline 内联章节按章节文件存在判定（内联稿也会落章节）。
    """
    spec = get_role(role)
    kind = node_kind(node_id)
    missing: list[str] = []
    for item in spec.required_outputs:
        if item == "research":
            if not store.research_path().exists():
                missing.append("research.json")
        elif item == "outline":
            if not store.outline_path().exists():
                missing.append("outline.json")
        elif item == "world":
            if not store.world_path().exists():
                missing.append("world.json")
        elif item == "chapter":
            no = _chapter_no(node_id)
            if not store.chapter_path(no).exists():
                missing.append(f"chapters/{no}.json")
        elif item == "review":
            no = _chapter_no(node_id)
            try:
                import json
                recs = json.loads(store.review_path().read_text(encoding="utf-8"))
                if not any(int(r.get("chapter", 0)) == no for r in recs):
                    missing.append(f"review.json[{no}]")
            except Exception:
                missing.append(f"review.json[{no}]")
        elif item == "commit":
            no = _chapter_no(node_id)
            try:
                import json
                d = json.loads(store.chapter_commit_path().read_text(encoding="utf-8"))
                if str(no) not in ((d.get("chapters") or {}) if isinstance(d, dict) else {}):
                    missing.append(f"chapter_commit.json[{no}]")
            except Exception:
                missing.append(f"chapter_commit.json[{no}]")
    return missing

