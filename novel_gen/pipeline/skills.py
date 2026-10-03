# -*- coding: utf-8 -*-
"""
Agent × Skill 统一注入管线（REQ-技能统一.md，阶段一）。

收敛四处重复的「技能采集 → 清洗 → 注入」逻辑为单一入口：
- 平台全局技能（writing_skills role=''）/ 平台角色专属技能（writing_skills role=<agent>）
- 用户自定义技能（user_skills，按项目 owner 过滤）
- 项目级技能快照（cfg.skill_prompt_append）/ 自定义提示词（cfg.custom_prompt）

统一规则：
- 阶段过滤：技能 applicable_stages 为空=全阶段生效；否则当前阶段命中才注入。
  过滤在组装层（Python 侧）做，**不新增 DB 查询参数**（兼容既有测试对 db.list_skills 的 mock）。
- 清洗：用户可写内容（用户技能 / custom_prompt）拼入 System Prompt 前统一 sanitize_system_text
  （模块3 防 Prompt 注入）；平台/管理员技能视为可信，直接拼入。
- 可观测：产出结构化 manifest（source/name/role/stages/matched），供落 agent_trace 归因。

兼容性保证：存量技能 applicable_stages 全为 '' → 全量注入，生成输出与改造前逐字节一致。
"""
from __future__ import annotations

# 角色 → 默认适用阶段（联动接线点；未知角色回退空列表=全阶段生效）
# 生产层细分 Agent（theme_character/world_rules/pacing）由 ArchitectAgent 编排，
# 其 persona_prefix() 会经此处路由挂载专属技能；validator 不继承 BaseAgent（纯逻辑校验），
# 不参与技能注入，故不再登记。
STAGE_BY_ROLE: dict[str, list[str]] = {
    "researcher": ["research"],
    "architect": ["outline", "world"],
    "theme_character": ["world"],
    "world_rules": ["world"],
    "pacing": ["outline", "world"],
    "ending": ["outline", "world"],
    "opening": ["outline"],
    "dialogue": ["world"],
    "writer": ["chapter"],
    "reviewer": ["review"],
    "reader": ["chapter", "review"],
    "stylist": ["chapter", "world"],
}


def role_default_stages(role: str) -> list[str]:
    """Agent 角色对应的默认适用阶段；未知角色回退空列表（=全阶段生效）。"""
    return list(STAGE_BY_ROLE.get(role, []))


def parse_stages(stages_field) -> list[str]:
    """解析技能 applicable_stages 字段（逗号/顿号分隔的字符串，或列表），返回去重后的阶段列表。"""
    if not stages_field:
        return []
    if isinstance(stages_field, str):
        raw = stages_field.replace("，", ",").replace("、", ",")
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(stages_field, (list, tuple)):
        return [str(s).strip() for s in stages_field if str(s).strip()]
    return []


def stage_matches(stages_field, ctx) -> bool:
    """阶段匹配规则。

    - 技能 applicable_stages 为空 → 全阶段生效（默认兼容，旧技能不受路由影响）；
    - ctx（当前阶段/角色适用阶段）为空 → 不限（默认兼容）；
    - 否则：技能声明的任一阶段命中 ctx 才注入。
    ctx 可为字符串（如 "chapter"）或列表（如 ["outline","world"]）。
    """
    declared = parse_stages(stages_field)
    if not declared:
        return True
    ctx_stages = parse_stages(ctx)
    if not ctx_stages:
        return True
    return any(s in ctx_stages for s in declared)


def _sanitize_user_text(raw: str):
    """用户可写内容拼入 System Prompt 前的注入过滤兜底（模块3）。"""
    from ..safety import sanitize_system_text
    return sanitize_system_text(raw)


def collect_role_blocks(role: str, store, stage: str = ""):
    """采集 平台全局 + 平台角色专属 + 用户自定义 技能，按角色与阶段过滤，统一清洗。

    返回 (blocks, manifest)：
    - blocks: [(tag, content), ...]，按注入顺序排好、用户内容已清洗、空内容已丢弃；
    - manifest: [{source, name, role, stages, matched}, ...]（含未命中项，供归因）。
    数据库不可用时静默降级为空（不阻塞生成主流程）。
    """
    from webapp import db
    blocks: list = []
    manifest: list = []
    try:
        _collect_role_blocks_inner(role, store, stage, db, blocks, manifest)
    except Exception:
        # 数据库/清洗任一环节异常：静默降级为空（不阻塞生成主流程）
        return [], []
    return blocks, manifest


def _collect_role_blocks_inner(role, store, stage, db, blocks, manifest) -> None:
    """collect_role_blocks 内部实现：平台技能拉取异常由外层统一兜底。"""
    for sk in db.list_skills(only_active=True, role=""):
        content = (sk.get("prompt_append") or "").strip()
        if not content:
            continue
        name = sk.get("name") or "全局"
        matched = stage_matches(sk.get("applicable_stages"), stage)
        manifest.append({"source": "platform_global", "name": name, "role": "",
                         "stages": parse_stages(sk.get("applicable_stages")), "matched": matched})
        if matched:
            blocks.append((f"【{name}】", content))
    # ---- 平台角色专属技能（role=<agent>，管理员可信） ----
    for sk in db.list_skills(only_active=True, role=role):
        content = (sk.get("prompt_append") or "").strip()
        if not content:
            continue
        name = sk.get("name") or role
        matched = stage_matches(sk.get("applicable_stages"), stage)
        manifest.append({"source": "platform_role", "name": name, "role": role,
                         "stages": parse_stages(sk.get("applicable_stages")), "matched": matched})
        if matched:
            blocks.append((f"【{name}】", content))
    # ---- 用户自定义技能（项目 owner，用户可写，拼入前清洗） ----
    try:
        pid = store.dir.name
        for usk in db.list_user_skills_by_project(pid):
            raw = usk.get("prompt_append") or ""
            clean = _sanitize_user_text(raw)
            if not clean:
                continue
            usk_role = usk.get("role") or ""
            # role 为空=所有角色生效；匹配=只对该角色生效
            role_hit = (not usk_role) or usk_role == role
            matched = role_hit and stage_matches(usk.get("applicable_stages"), stage)
            manifest.append({"source": "user", "name": usk.get("name") or "技能", "role": usk_role,
                             "stages": parse_stages(usk.get("applicable_stages")), "matched": matched})
            if matched:
                blocks.append((f"【用户自定义：{usk.get('name') or '技能'}】", clean))
    except Exception:
        # 用户技能加载失败静默忽略（与既有行为一致，不阻塞）
        pass
    return blocks, manifest


def build_role_skill_block(role: str, store, stage: str = "") -> tuple[str, list]:
    """组装角色技能正文（不含「本角色专属写作要求」标题，标题由调用方 persona_prefix/_system_with_role 统一加）。

    返回 (body, manifest)；body 为空串表示无技能注入。
    """
    blocks, manifest = collect_role_blocks(role, store, stage)
    if not blocks:
        return "", manifest
    return "\n\n".join(f"{tag}\n{content}" for tag, content in blocks), manifest


def collect_project_blocks(cfg, stage: str = ""):
    """采集项目级技能快照（cfg.skill_prompt_append）+ 自定义提示词（cfg.custom_prompt）。

    两者为建书时快照的用户选择，属"用户可写内容"，拼入前统一清洗；
    快照无 applicable_stages 元数据（config 只存文本），故不参与阶段过滤，始终注入。
    返回 (text, manifest)。
    """
    parts: list = []
    manifest: list = []
    skill_txt = (getattr(cfg, "skill_prompt_append", "") or "").strip()
    if skill_txt:
        clean = _sanitize_user_text(skill_txt)
        if clean:
            parts.append("\n\n【写作风格 Skill（必须严格遵循）】\n" + clean)
            manifest.append({"source": "project_snapshot", "name": "写作风格 Skill",
                             "role": "", "stages": [], "matched": True})
    cp = (getattr(cfg, "custom_prompt", "") or "").strip()
    if cp:
        clean_cp = _sanitize_user_text(cp)
        if clean_cp:
            parts.append("\n\n【用户自定义提示词】\n" + clean_cp)
            manifest.append({"source": "project_snapshot", "name": "用户自定义提示词",
                             "role": "", "stages": [], "matched": True})
    return "".join(parts), manifest
