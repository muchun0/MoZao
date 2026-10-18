# -*- coding: utf-8 -*-
"""合规红线处理（REQ-门禁质检正向影响 P0-1/P0-2）：纯函数，无 Web/业务依赖。

- P0-1：合规命中（quality issue 类型含「合规-疑似违规内容」）→ 判定为需删旧稿整章重写、
  不落盘的红线命中（普通质量问题不走本路径）。
- P0-2：提供面向作者的合规教育文本（指向内容不指向人）；"本书内首次"去重由
  调用方通过 book_memory 的 compliance_educated 账本完成，本模块只产文本。

本模块只提供判定与教育文本，不触碰 LLM 调用；失败降级为空串/False 不阻塞。
"""
from __future__ import annotations

_COMPLIANCE_MARKER = "合规-疑似违规内容"


def has_compliance(issues: list[dict]) -> bool:
    """issues 中是否含合规红线命中（大改级，输出侧基础合规校验触发）。

    用 marker 前缀匹配（与 quality.check 合规段 issue.type 一致），
    兼容未来拆分子类（type 前缀扩展）。
    """
    for it in issues or []:
        if _COMPLIANCE_MARKER in str(it.get("type") or ""):
            return True
    return False


def compliance_categories(issues: list[dict]) -> list[str]:
    """从合规 issue 提取 rule_category 去重列表（供首次教育分层；空=无法分类）。

    兼容旧 issue（无 rule_category 字段，由 detail 里的短语兜底）。
    """
    cats: list[str] = []
    seen: set[str] = set()
    for it in issues or []:
        if _COMPLIANCE_MARKER in str(it.get("type") or ""):
            c = str(it.get("rule_category") or "").strip()
            if not c:
                # 旧格式兜底：从 detail 短语反查大类（safety._COMPLIANCE_RULE_CAT）
                try:
                    from ..safety import _COMPLIANCE_RULE_CAT as _rcat
                    for ph, cat in _rcat.items():
                        if ph in str(it.get("detail") or ""):
                            c = cat
                            break
                except Exception:
                    c = ""
            if c and c not in seen:
                seen.add(c)
                cats.append(c)
    return cats


def compliance_education_block(categories: list[str]) -> str:
    """首次教育块（P0-2）：面向作者的合规引导，指向内容不指向人（M6 文化层）。

    仅在本书内该 rule_category 首次命中时由调用方注入（见 quality 侧去重）。
    空输入/无法分类 → 空串。
    """
    if not categories:
        return ""
    cats = "、".join(categories)
    return (
        "【合规教育·首次提示（很重要）】\n"
        f"本章因含「{cats}」被判定为不符合平台内容规范，整稿废弃重写。\n"
        "本类表达不允许在正文中出现，请用不触线的方式改写同一情节的表达，"
        "让情节在合规范围内成立，而不是删除情节本身。\n"
        "（本提示仅在本书首次出现此类问题时注入一次；后续同类问题仅重写，不重复提示）"
    )
