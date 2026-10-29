# -*- coding: utf-8 -*-
"""机制类型枚举与分派（REQ-通用机制类型适配）。

本书机制形态多样：数字面板/状态、等级境界、规则怪谈守则、纯能力、契约关系、
时间循环、无机制。本模块是类型集合清洗、判定与旧书兼容推断的唯一事实源；
下游校验 / 账本 / 注入 / 质检一律以这里返回的类型集合为分派依据，不在各业务
函数内散落词表硬编码（§11.2 跨模块可变状态收敛到单一权威源）。

类型可叠加（如 numeric_panel + rule_set：理智值面板 + 副本守则）；`none` 与
其他类型互斥，混入其他类型时 `none` 不参与分派。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 避免运行期循环依赖
    from ..store.schemas import World

#: 合法机制类型（顺序仅用于展示 / 报告，可叠加）。
MECHANISM_TYPES: tuple[str, ...] = (
    "numeric_panel",   # 数字面板/状态型：次数/进度/积分/血量/累积值/余量
    "power_level",      # 等级境界型：境界阶梯/职业途径（复用 power_system，不进数字账本）
    "rule_set",         # 规则约束型：规则怪谈守则/副本规则/场所禁忌
    "ability",         # 能力/概念金手指型：被动/主动异能，无可视化数字
    "contract",        # 契约/关系/身份型（本期复用人物/物品/事实账本，不单独建账）
    "temporal",        # 时间/循环/因果型（本期仅不误伤，不建专门状态机）
    "none",            # 无机制
)

#: 类型中文标签（日志 / 提示词 / 质检报告统一用词，禁止各模块自造说法）。
TYPE_LABELS: dict[str, str] = {
    "numeric_panel": "数字面板/状态型",
    "power_level": "等级境界型",
    "rule_set": "规则约束型",
    "ability": "能力/概念金手指型",
    "contract": "契约/关系/身份型",
    "temporal": "时间/循环/因果型",
    "none": "无机制",
}

#: LLM 可能回传的中文说法 / 别名 → 枚举值（容错归一；不认识的原样丢弃）。
_ALIASES: dict[str, str] = {
    "数字面板": "numeric_panel", "面板": "numeric_panel", "数字": "numeric_panel",
    "状态型": "numeric_panel", "数值": "numeric_panel",
    "等级": "power_level", "境界": "power_level", "升级流": "power_level",
    "职业途径": "power_level",
    "规则": "rule_set", "规则怪谈": "rule_set", "守则": "rule_set",
    "怪谈": "rule_set", "副本规则": "rule_set", "禁忌": "rule_set",
    "能力": "ability", "金手指": "ability", "异能": "ability",
    "概念能力": "ability", "被动能力": "ability",
    "契约": "contract", "关系": "contract", "身份": "contract", "阵营": "contract",
    "时间": "temporal", "循环": "temporal", "因果": "temporal", "读档": "temporal",
    "无机制": "none", "无": "none", "纯剧情": "none",
}


def _split_csv(raw: str) -> list[str]:
    """把 'rule_set, ability' 或中文顿号逗号串拆成单项。"""
    for sep in ("，", "、", "/", ";", "；"):
        raw = raw.replace(sep, ",")
    return [x for x in raw.split(",") if x.strip()]


def normalize_types(raw) -> list[str]:
    """清洗 LLM / JSON 传入的类型集合：去空白、去重（保序）、剔除非法值。

    - 传入 None / 非序列 → 空列表（是否走旧书兼容推断由调用方决定）；
    - 单项字符串按枚举值或别名整体识别，不拆字符；
    - 全非法 → 空列表（不自动回退 none，避免覆盖显式空判定）；
    - 同时出现 `none` 与其他类型时，剔除 `none`（none 是兜底，不可与叠加类型共存）。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items = _split_csv(raw)
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []

    out: list[str] = []
    for item in items:
        if item is None:
            continue
        key = str(item).strip()
        if not key:
            continue
        mapped = _ALIASES.get(key) or _ALIASES.get(key.lower())
        enum = key.lower() if key.lower() in MECHANISM_TYPES else mapped
        if enum and enum not in out:
            out.append(enum)
    if "none" in out and len(out) > 1:
        out = [t for t in out if t != "none"]
    return out


def has_type(types: list[str], *names: str) -> bool:
    """类型集合是否包含任一指定类型。"""
    return any(name in types for name in names)


def infer_types_from_world(world: "World | None") -> list[str]:
    """旧书兼容推断：`mechanism_types` 缺失时，依据既有静态产物判断类型集合。

    判定顺序（互不排斥，可叠加）：
    - `mechanism_baseline` 有任一数字字段 → numeric_panel；
    - `mechanism_ruleset` 非空 → rule_set；
    - `goldfinger_rules` 有触发条件与上限 → ability；
    - 三者皆无 → none。

    推断结果只用于运行时分派，不回写覆盖用户 / LLM 显式声明。
    """
    if world is None:
        return ["none"]
    types: list[str] = []

    mb = getattr(world, "mechanism_baseline", None)
    if mb is not None and any(
        (getattr(mb, f, "") or "").strip()
        for f in ("panel_format", "reset_rule", "scar_current", "scar_start")
    ):
        types.append("numeric_panel")

    if getattr(world, "mechanism_ruleset", None):
        types.append("rule_set")

    gf = getattr(world, "goldfinger_rules", None)
    if gf is not None and (getattr(gf, "trigger", "") or "").strip() \
            and (getattr(gf, "upper_limit", "") or "").strip():
        types.append("ability")

    return types or ["none"]


def resolve_types(world: "World | None") -> list[str]:
    """取本书生效类型集合：显式声明优先；缺失时走旧书兼容推断。"""
    types = normalize_types(getattr(world, "mechanism_types", None) if world else None)
    return types or infer_types_from_world(world)
