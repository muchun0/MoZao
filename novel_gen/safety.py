# -*- coding: utf-8 -*-
"""
输入防护与 Prompt 注入对抗（模块3）：纯函数工具，无 Web 依赖。

三层能力：
1. detect_prompt_injection(text)：检测典型注入话术（忽略以上指令 / 修改系统设定 /
   泄露 system prompt / 越狱模式 / 角色扮演突破限制 等，含中文与常见英文变体），
   返回命中的规则名列表（空 = 未命中）。
2. sanitize_system_text(text)：拼入 System Prompt 之前调用，剥离高危片段——
   用户可控内容（custom_prompt / skill_prompt_append / 用户自定义技能）即使
   通过 API 校验，也会在此再兜底一层（纵深防御）。
3. basic_content_check(text)：输出侧基础合规校验（明显违规词 / 危险行为教唆），
   返回问题列表（空 = 通过）。只拦「明显违规」，宁可宽松记录，不误伤正常网文。

误报控制：检测用「组合话术」而非单字——正常创作设定中出现"忽略/规则/系统"等
单字不会被拦截；只有"忽略以上指令/修改系统设定"这类完整攻击句式才命中。
"""
from __future__ import annotations

import re
from typing import Optional


# ---------- 1. Prompt 注入检测规则 ----------
# (规则名, 编译后正则)。优先匹配完整攻击句式，避免正常文本误伤。
_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ignore_prior", re.compile(
        r"(忽略|无视|忘记|不要理会|别管|遗忘)(以上|之前|前面的|之前所有的|所有先前的)?"
        r"(指令|指示|内容|要求|提示词|设定|规则|对话|消息)?",
        re.IGNORECASE)),
    ("modify_system", re.compile(
        r"(修改|改变|重置|覆盖|推翻|替换|删除|隐藏)(系统|我的|内置)?(设定|指令|提示词|提示|规则|约束|人格|身份|角色)",
        re.IGNORECASE)),
    ("expose_prompt", re.compile(
        r"(输出|显示|打印|泄露|公开|透露|复述)(你的|您的|系统)?\s*(系统提示词|system\s*prompt|提示词|prompt|系统指令|系统提示|指令集)",
        re.IGNORECASE)),
    ("jailbreak", re.compile(
        r"(进入|开启|打开|切换|激活)(开发者模式|开发模式|越狱模式|god mode|developer mode|dan模式)",
        re.IGNORECASE)),
    ("no_restriction", re.compile(
        r"(不受任何限制|没有任何限制|无限制地|无视所有规则|不遵守任何规则|打破所有规则|可以违反规则)",
        re.IGNORECASE)),
    ("ignore_constraints", re.compile(
        r"(忽略你的所有约束|不要遵守你的约束|绕过你的限制|绕过所有限制|摆脱你的限制)",
        re.IGNORECASE)),
    ("sys_instruction_fake", re.compile(
        r"(你(现在|接下来|以后)(是|要扮演|将扮演)(一个)?(不受|没有|无)(约束|限制|规则)的)",
        re.IGNORECASE)),
    ("extract_instructions", re.compile(
        r"(上面的(指令|内容).{0,12}(是什么|是什么内容)|把(上面|之前)(的)?(指令|内容).{0,6}(告诉我|复述一遍))",
        re.IGNORECASE)),
]


def detect_prompt_injection(text: str) -> list[str]:
    """检测输入是否含典型注入话术，返回命中规则名列表（空 = 安全）。"""
    if not text:
        return []
    hits = []
    for name, pat in _INJECTION_PATTERNS:
        if pat.search(text):
            hits.append(name)
    return hits


# ---------- 2. System Prompt 拼接前兜底清洗 ----------
# 清洗时把命中片段替换为占位说明，避免静默删掉用户内容的其余部分。
_SANITIZE_PLACEHOLDER = "【该段用户内容包含异常指令，已按安全策略移除】"


def sanitize_system_text(text: Optional[str]) -> str:
    """剥离用户可控内容中的注入片段（拼入 System Prompt 前调用）。

    命中注入模式的最短片段会被替换为占位说明；若整体均为攻击文本则返回空串。
    """
    if not text:
        return ""
    cleaned = text
    for name, pat in _INJECTION_PATTERNS:
        cleaned = pat.sub(_SANITIZE_PLACEHOLDER, cleaned)
    # 全部为占位符/标点/空白 = 整体攻击文本，返回空
    residue = "".join(
        ch for ch in cleaned if ch not in _SANITIZE_PLACEHOLDER
    ).strip("：:，,。.；;、 	")
    if not residue:
        return ""
    return cleaned.strip()


# ---------- 3. 输出侧基础合规校验 ----------
# 明显违规词库（只放小说内容中几乎不会出现、出现即为违规的明确词组）。
# 预留：后续可在管理后台配置化管理（阶段2）。
_CONTENT_BLOCK_PHRASES: list[str] = [
    # 露骨色情描写（固定词组，非单字，避免误伤正常情感/情节描写）
    "赤裸的性器官", "性交全过程", "口交细节", "精液射入",
    # 危险行为教唆（恐怖/暴力袭击的操作细节）
    "制作炸弹的步骤", "自制炸药的配方", "投毒教程", "枪支改装教程",
    # 未成年人性化（任何形式都不允许）
    "幼女做爱", "儿童性行为",
]


def basic_content_check(text: str) -> list[str]:
    """输出侧基础合规校验，返回违规短语列表（空 = 通过）。"""
    if not text:
        return []
    return [w for w in _CONTENT_BLOCK_PHRASES if w in text]
