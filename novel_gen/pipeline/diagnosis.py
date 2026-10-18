# -*- coding: utf-8 -*-
"""质检诊断回喂（REQ-门禁质检正向影响 P0-3）：从"倒扣制"到"多维评分 + 维度诊断"。

- `suggest_for(issue)`：确定性维度 → {fix_suggestion, expected_gain}（怎么改 + 预计提升）。
  仅当该 issue 未携带 LLM 建议（fix_suggestion 为空）时补齐；expected_gain 取该维度
  单独扣分值（与 quality.check 的 score -= X 同源，便于作者看到"改这个能提多少分"）。
- `build_issue_block(issues)`：带建议的质检问题块（reask 而非盲重写），供
  quality._rewrite_whole / _rewrite_span 复用。

纯函数、无业务依赖；映射缺失的 type 返回 None（调用方保持原 issue_block）。
"""
from __future__ import annotations

#: 确定性维度 → (建议模板, 单独扣分)。模板 {detail} 可引用原 detail 的前缀（截短避免刷屏）。
_SUGGEST_BY_TYPE: dict[str, tuple[str, int]] = {
    "结构-字数严重不足": ("补足正文至目标字数，推进剧情而不是扩写背景", 30),
    "结构-字数偏少": ("将本章充实到目标字数，补充场景细节与情绪递进", 10),
    "内容重复": ("删除与最近章节重复的段落/情节，替换为新的推进内容", 20),
    "章内大量自重复": ("删除本章内部互相重复的草稿段落，保留单一完整版本", 30),
    "章内同句复读": ("删除整句/整动作原样复读，每句只出现一次，用新描写替代", 15),
    "写作-机制数字方向违规": ("按机制卡基线随事件严格结算数字，禁止同值跨段复现或方向回退", 5),
    "写作-机制字段白名单违规": ("只使用机制卡声明的字段名，未声明字段（如自创累计次数）删除或并入合法字段", 5),
    "情节-大纲核心事件未覆盖": ("把大纲核心事件写进正文（≥60% 分段覆盖），不得替换为自创事件", 30),
    "对话轮重复": ("删除同一组问答/对峙的重复轮，只保留一轮完整对话", 30),
    "情节-同事件循环重演": ("同一事件只写一遍，删除换地点/换措辞/换细节的重复版本，合并不可替代信息", 30),
    "摘要一致性": ("让摘要的面板状态与正文最终结算一致，随正文更新摘要数字", 5),
    "结尾完整性": ("以句末标点完整收尾，避免中途截断感", 30),
    "读者-追更欲过低": ("增强本章爽点闭环与章末钩子，让读者期待下一章；详见读者问题清单", 20),
}

#: 前缀兜底（LLM 维度/子类）：type 以某前缀开头且未在上表命中 → 通用建议 + 该前缀代表扣分。
_PREFIX_SUGGEST: list[tuple[str, str, int]] = [
    ("自相矛盾-", "统一前后描写（身体状态/行为模式/方位指代/比喻/事件），消除不能同时为真的硬矛盾", 25),
    ("读者-", "按读者问题清单针对性修改（代入感/爽点闭环/章末钩子/阅读障碍/主角主动性）", 20),
    ("合规-", "本内容不符合平台规范，需删旧稿整章重写，用不触线方式表达同一情节", 40),
    ("文风漂移", "对比前期章基线，收敛句长波动/填充词，恢复本书既有文风", 5),
]

#: AI 痕迹（ai_taste_issues 的 type 是"AI痕迹-xxx"）特殊映射（无法穷举子类，走前缀）
_AI_TASTE_SUGGEST = ("降低模板化表达，增加具体动作/细节/留白，减少总结式与排比堆砌", 20)


def suggest_for(issue: dict) -> dict | None:
    """为确定性 issue 补 {fix_suggestion, expected_gain}；LLM 维度/无法映射返回 None。

    - 若 issue 已带 fix_suggestion（LLM 已给），不覆盖（返回 None）；
    - 命中 _SUGGEST_BY_TYPE 精确表 → 直接取；
    - 命中前缀表（自相矛盾/读者/合规/文风漂移）→ 前缀兜底；
    - AI痕迹 前缀 → _AI_TASTE_SUGGEST；
    - 其余（如 读者-xxx 子类、结构-xxx 变体）→ None。
    """
    if issue.get("fix_suggestion"):
        return None
    t = str(issue.get("type") or "")
    if t in _SUGGEST_BY_TYPE:
        sug, gain = _SUGGEST_BY_TYPE[t]
        return {"fix_suggestion": sug, "expected_gain": gain}
    for prefix, sug, gain in _PREFIX_SUGGEST:
        if t.startswith(prefix):
            return {"fix_suggestion": sug, "expected_gain": gain}
    if t.startswith("AI痕迹"):
        return {"fix_suggestion": _AI_TASTE_SUGGEST[0], "expected_gain": _AI_TASTE_SUGGEST[1]}
    return None


def build_issue_block(issues: list[dict], max_n: int = 8) -> str:
    """带建议的质检问题块（reask 而非盲重写）。

    每条 `- [type] detail｜建议：sug（预计提升 X 分）`；
    LLM 已带 fix_suggestion 的沿用；否则 suggest_for 兜底；无法映射则仅 detail。
    空输入 → 空串。
    """
    lines: list[str] = []
    for it in (issues or [])[:max_n]:
        line = f"- [{it.get('type', '')}] {it.get('detail', '')}"
        sug = it.get("fix_suggestion")
        gain = it.get("expected_gain")
        if not sug:
            fb = suggest_for(it)
            if fb:
                sug, gain = fb.get("fix_suggestion"), fb.get("expected_gain")
        if sug:
            line += f"｜建议：{sug}"
            if gain:
                line += f"（预计提升 {gain} 分）"
        lines.append(line)
    return "\n".join(lines)
