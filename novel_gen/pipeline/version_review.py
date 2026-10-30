# -*- coding: utf-8 -*-
"""版本对比的「审校 / 评审」评论视角（REQ-版本对比与推荐）。

职责（与 version_diff.py 分工）：
  - `version_diff.py` 负责算出"两版哪里不一样"（difflib 段落对齐、结构化字段差）；
  - 本模块负责把差异 + 各版已落库的质检数据，翻译成**两个 agent 视角的评论**，
    给前端做"选哪一版"的决策依据，取代 git 式的行级增删展示。

两个视角分工（界面上按视角分块，逐条标注来源）：
  - 审校视角：文字 / 表达 / 一致性 / 冗余与重复叙述；
  - 评审视角：结构 / 剧情推进 / 人物塑造 / 节奏与信息密度。

设计纪律（AGENTS.md §11）：
  - **零 LLM、纯函数、不写盘、不扣费**：素材全部来自版本库已落库的质检数据
    （{passed, score, issues:[{level, type, detail}]}）+ 版本正文 + 结构化字段差；
  - 修改建议不新造口径：复用 `quality.diagnosis.suggest_for`（与重写 prompt 同源）；
  - 原文片段不新造定位口径：复用 `quality.text_tools.locate_problem_spans`；
  - **不编造评价**：拿不到依据的条目一律留空（前端展示"本版无该类数据"），
    绝不用"建议优化表达"这类空话充数。
"""
from __future__ import annotations

import re

__all__ = [
    "AGENT_PROOFREAD",
    "AGENT_CRITIQUE",
    "AGENT_LABELS",
    "AGENT_FOCUS",
    "MAX_QUOTE_LEN",
    "MAX_ITEMS_PER_SECTION",
    "classify_review_agent",
    "review_chapter_pair",
    "review_structured_pair",
]

#: 审校 agent 视角标识（文字/表达/一致性/冗余与重复叙述）
AGENT_PROOFREAD = "proofread"
#: 评审 agent 视角标识（结构/剧情推进/人物塑造/节奏与信息密度）
AGENT_CRITIQUE = "critique"
#: 渲染顺序固定：审校在前、评审在后
_AGENT_ORDER = (AGENT_PROOFREAD, AGENT_CRITIQUE)

AGENT_LABELS = {AGENT_PROOFREAD: "审校视角", AGENT_CRITIQUE: "评审视角"}
AGENT_FOCUS = {
    AGENT_PROOFREAD: "文字 / 表达 / 一致性 / 冗余与重复叙述",
    AGENT_CRITIQUE: "结构 / 剧情推进 / 人物塑造 / 节奏与信息密度",
}

#: 引用原文片段的最长字符数（超出截断加省略号，防长段刷屏）
MAX_QUOTE_LEN = 140
#: 每个视角每个区块的最多条目数（超出按级别排序截断）
MAX_ITEMS_PER_SECTION = 5
#: 级别排序权重（大改 > 中改 > 小改），与 quality.constants.LEVEL_RANK 同口径
_LEVEL_RANK = {"大改": 3, "中改": 2, "小改": 1}

#: 审校视角认领的 issue 类型前缀（按前缀匹配，与 quality 的 type 命名同源）
_PROOFREAD_PREFIXES: tuple[str, ...] = (
    "AI痕迹", "文风漂移", "文风同质化", "章内同句复读", "章内大量自重复",
    "内容重复", "对话轮重复", "自相矛盾", "摘要一致性",
)
#: 评审视角认领的 issue 类型前缀
_CRITIQUE_PREFIXES: tuple[str, ...] = (
    "结构-字数", "情节-", "结尾完整性", "读者-", "写作-机制", "合规-",
)
#: 字数类检查项名（需要目标字数才产出，拿不到就不编造"达标"）
_WORD_COUNT_ASPECT = "篇幅与信息密度"

#: 审校视角的正向检查项：未命中该类 issue 才产出"写得好的地方"
#: (检查项名, 类型前缀, 理由模板（{words}/{target}/{pct} 回填）, 引用取材位置)
_PROOFREAD_CHECKS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("表达自然度", ("AI痕迹", "文风漂移", "文风同质化"),
     "本版未被点名模板化痕迹或文风漂移；上面这段是本章对白最密集的段落，"
     "人物语气靠对白本身推进，无排比堆砌与总结式表述", "dialogue"),
    ("重复叙述", ("章内同句复读", "章内大量自重复", "内容重复", "对话轮重复"),
     "本版未被点名重复叙述类问题；开篇段（上面这段）直接进入新内容，"
     "不与本章其它段落或近章情节重复", "opening"),
    ("前后一致性", ("自相矛盾", "摘要一致性"),
     "本版未被点名自相矛盾或摘要口径不一致；开篇段（上面这段）与前文设定同源，"
     "人物状态/方位/事件口径对得上", "opening"),
)
#: 评审视角的正向检查项
_CRITIQUE_CHECKS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (_WORD_COUNT_ASPECT, ("结构-字数",),
     "本版正文 {words} 字，达目标 {target} 的 {pct}%，篇幅与信息密度达标", "middle"),
    ("剧情推进", ("情节-", "写作-机制"),
     "本版未被点名情节类问题（核心事件覆盖到位、无同事件循环重演）；"
     "上面这段是本章主场景（最长段），信息量集中", "longest"),
    ("节奏与收尾", ("结尾完整性", "读者-追更欲过低"),
     "本版以完整句收束（末句如上），未被点名结尾截断或章末追更欲过低", "ending"),
)
#: 引用片段的取材位置
_QUOTE_OPENING, _QUOTE_DIALOGUE = "opening", "dialogue"
_QUOTE_LONGEST, _QUOTE_MIDDLE, _QUOTE_ENDING = "longest", "middle", "ending"

#: 结构化产物的"文字表达类"分组（归审校视角）；其余分组归评审视角
_STRUCT_PROOFREAD_GROUPS: tuple[str, ...] = ("术语表", "对话风格", "主题命题")
#: 结构化产物引用片段的候选字段（按优先级取第一个有值的）
_STRUCT_TEXT_FIELDS: tuple[str, ...] = (
    "name", "title", "text", "desc", "content", "detail", "rule", "statement",
)
#: 无条目时的留白提示（前端原样展示，不伪装成评价）
_EMPTY_HINT = {
    AGENT_PROOFREAD: "本版暂无可引用的文字类评价（该版本未落库质检数据）",
    AGENT_CRITIQUE: "本版暂无可引用的结构类评价（该版本未落库质检数据）",
}
_STRUCT_EMPTY_HINT = {
    AGENT_PROOFREAD: "两版在术语/文风类条目上无差异，无可引用的文字类评价",
    AGENT_CRITIQUE: "两版在结构类条目上无差异，无可引用的结构类评价",
}

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])")


# --------------------------------------------------------------------------- #
# 公共小工具
# --------------------------------------------------------------------------- #

def _paras(content: str) -> list[str]:
    """正文切段（去空行），与版本 diff 的段落口径一致。"""
    return [ln.strip() for ln in (content or "").split("\n") if ln.strip()]


def _clip(text: str, limit: int = MAX_QUOTE_LEN) -> str:
    """片段截断：超长加省略号。"""
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _locate_spans(content: str, issues: list[dict]) -> list[tuple[int, int]]:
    """复用 quality 的问题定位（懒加载：避免本模块被导入时就拖起整条质检链路）。"""
    try:
        from .quality.text_tools import locate_problem_spans
    except Exception:  # pragma: no cover - 依赖缺失时静默降级
        return []
    try:
        return locate_problem_spans(content, issues) or []
    except Exception:
        return []


def _quote_for_issue(content: str, issue: dict) -> str:
    """问题条目的原文片段：优先用已落库的 evidence，否则回落到正文定位。

    定位不到就返回空串（前端不显示引用块），绝不拿无关段落冒充证据。
    """
    ev = str(issue.get("evidence") or "").strip()
    if ev:
        return _clip(ev)
    spans = _locate_spans(content, [issue])
    if not spans:
        return ""
    start, end = spans[0]
    return _clip(content[start:end])


def _longest_para(paras: list[str]) -> str:
    return max(paras, key=len) if paras else ""


def _pick_quote(content: str, kind: str) -> str:
    """按取材位置挑一段原文（用于"写得好的地方"的引用）。

    取材口径：opening=首段 / dialogue=对白最密段 / longest=最长段 /
    middle=正中段 / ending=末段末句。取不到时逐级回落。
    """
    paras = _paras(content)
    if not paras:
        return ""
    if kind == _QUOTE_DIALOGUE:
        best = max(paras, key=lambda p: p.count("“") + p.count("「"))
        if best.count("“") + best.count("「") > 0:
            return _clip(best)
        return _clip(_longest_para(paras))
    if kind == _QUOTE_LONGEST:
        return _clip(_longest_para(paras))
    if kind == _QUOTE_MIDDLE:
        return _clip(paras[len(paras) // 2])
    if kind == _QUOTE_ENDING:
        sents = [s.strip() for s in _SENT_SPLIT.split(paras[-1]) if s.strip()]
        return _clip(sents[-1] if sents else paras[-1])
    return _clip(paras[0])


def _issues_of(quality: dict | None) -> list[dict]:
    """取出版本质检条目（只信 dict，脏数据一律当"无质检"）。"""
    issues = (quality or {}).get("issues")
    if not isinstance(issues, list):
        return []
    return [it for it in issues if isinstance(it, dict)]


def _has_prefix(issue: dict, prefixes: tuple[str, ...]) -> bool:
    """issue 的 type 是否命中任一前缀。"""
    t = str(issue.get("type") or "")
    return any(t.startswith(p) for p in prefixes)


def classify_review_agent(issue: dict) -> str:
    """一条质检问题归给哪个视角。

    命中审校前缀 → 审校；命中评审前缀 → 评审；两者都不命中（未知类型/自定义 type）
    默认给评审（结构/读者体验为主），保证任何 issue 都有人认领、不被静默吞掉。
    """
    if _has_prefix(issue, _PROOFREAD_PREFIXES):
        return AGENT_PROOFREAD
    return AGENT_CRITIQUE


def _suggestion_for(issue: dict) -> tuple[str, int]:
    """取"可直接照做的修改建议"：先取已落库建议，否则用 diagnosis 兜底（同源口径）。"""
    sug = str(issue.get("fix_suggestion") or "").strip()
    if sug:
        return sug, int(issue.get("expected_gain") or 0)
    try:
        from .diagnosis import suggest_for
    except Exception:  # pragma: no cover
        return "", 0
    fb = suggest_for(issue)
    if not fb:
        return "", 0
    return str(fb.get("fix_suggestion") or "").strip(), int(fb.get("expected_gain") or 0)


def _empty_reviews(hints: dict[str, str]) -> list[dict]:
    """两个视角的空壳（顺序固定：审校 → 评审）。"""
    return [
        {"agent": a, "agent_label": AGENT_LABELS[a], "focus": AGENT_FOCUS[a],
         "strengths": [], "improvements": [], "empty_hint": hints[a]}
        for a in _AGENT_ORDER
    ]


# --------------------------------------------------------------------------- #
# 1. 章节版本：两视角评论
# --------------------------------------------------------------------------- #

def _improvements(content: str, quality: dict | None, agent: str) -> list[dict]:
    """该视角认领的问题条目：引用原文 + 问题定性 + 具体改法（按级别从重到轻）。"""
    mine = [it for it in _issues_of(quality) if classify_review_agent(it) == agent]
    mine.sort(key=lambda it: -_LEVEL_RANK.get(str(it.get("level") or ""), 0))
    out: list[dict] = []
    for it in mine[:MAX_ITEMS_PER_SECTION]:
        sug, gain = _suggestion_for(it)
        out.append({
            "quote": _quote_for_issue(content, it),
            "type": str(it.get("type") or ""),
            "level": str(it.get("level") or ""),
            "problem": str(it.get("detail") or ""),
            "suggestion": sug,
            "expected_gain": gain,
        })
    return out


def _strengths(content: str, quality: dict | None, agent: str, *,
               words: int, target_words: int | None,
               other_issues: list[dict], other_v: int) -> list[dict]:
    """该视角的正向条目：只从"该类问题本版未被点名"推导，附原文片段。

    字数类检查项额外要求真的知道目标字数，拿不到就不产出（不编造"达标"）。
    """
    # 无质检记录 ≠ 没有问题（legacy 版本/未跑质检的稿子）：此时一条都不产出，
    # 否则"没数据"会被读成"没毛病"，是假好评。
    if not isinstance(quality, dict):
        return []
    checks = _PROOFREAD_CHECKS if agent == AGENT_PROOFREAD else _CRITIQUE_CHECKS
    mine = _issues_of(quality)
    out: list[dict] = []
    for aspect, prefixes, reason_tpl, quote_kind in checks:
        if any(_has_prefix(it, prefixes) for it in mine):
            continue  # 本版被点名 → 不把它吹成优点
        if aspect == _WORD_COUNT_ASPECT and not target_words:
            continue
        pct = round(words * 100 / target_words) if target_words else 0
        reason = reason_tpl.format(words=words, target=target_words or 0, pct=pct)
        # 跨版事实（可验证）：对比版在此项被点名了几条
        other_hits = sum(1 for it in other_issues if _has_prefix(it, prefixes))
        if other_hits:
            reason += f"；对比 v{other_v} 在此项被点名 {other_hits} 条，本版已消除"
        out.append({
            "aspect": aspect,
            "quote": _pick_quote(content, quote_kind),
            "reason": reason,
        })
        if len(out) >= MAX_ITEMS_PER_SECTION:
            break
    return [x for x in out if x["quote"]]


def _chapter_panel(item: dict, other: dict, *, target_words: int | None) -> dict:
    """单个章节版本的评论面板（两视角）。"""
    quality = item.get("quality") if isinstance(item.get("quality"), dict) else None
    other_quality = other.get("quality") if isinstance(other.get("quality"), dict) else None
    content = str(item.get("content") or "")
    words = int(item.get("words") or 0)
    reviews = _empty_reviews(_EMPTY_HINT)
    for rv in reviews:
        rv["strengths"] = _strengths(
            content, quality, rv["agent"], words=words, target_words=target_words,
            other_issues=_issues_of(other_quality), other_v=int(other.get("v") or 0))
        rv["improvements"] = _improvements(content, quality, rv["agent"])
    return {
        "v": int(item.get("v") or 0),
        "words": words,
        "score": (quality or {}).get("score"),
        "passed": (quality or {}).get("passed"),
        "issue_count": (quality or {}).get("issue_count"),
        "reviews": reviews,
    }


def review_chapter_pair(item_a: dict, item_b: dict, *,
                        target_words: int | None = None,
                        changes: dict | None = None) -> dict:
    """章节两版的评论视角结果（零 LLM）。

    - item_a / item_b：{"v", "words", "quality", "content"}（content 为版本正文）；
    - target_words：本章目标字数（config.chapter_words），拿不到传 None；
    - changes：`diff_chapter_text` 的结果，仅用于概览计数，**不参与前端分块展示**。

    返回 {"kind", "v1", "v2", "panels": [panel_a, panel_b], "summary": {...}}。
    """
    blocks = (changes or {}).get("blocks") or []
    summary = {
        "changed_blocks": sum(1 for b in blocks if b.get("type") in ("add", "del", "mod")),
        "added_paragraphs": sum(1 for b in blocks if b.get("type") == "add"),
        "deleted_paragraphs": sum(1 for b in blocks if b.get("type") == "del"),
        "rewritten_paragraphs": sum(1 for b in blocks if b.get("type") == "mod"),
        "truncated": bool((changes or {}).get("truncated")),
    }
    return {
        "kind": "chapter",
        "v1": int(item_a.get("v") or 0),
        "v2": int(item_b.get("v") or 0),
        "panels": [_chapter_panel(item_a, item_b, target_words=target_words),
                   _chapter_panel(item_b, item_a, target_words=target_words)],
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# 2. outline/world 版本：两视角评论
# --------------------------------------------------------------------------- #

def _struct_label(change: dict) -> str:
    """结构化条目的可读名（name 缺失时退回路径末段，绝不返回空串）。"""
    name = str(change.get("name") or "").strip()
    if name and name != "None":
        return name
    path = str(change.get("path") or "")
    return path.split(".")[-1] or "未命名条目"


def _struct_quote(change: dict) -> str:
    """结构化条目的引用内容：取"较新一侧"的正文类字段值（不再展示 old→new 对照）。"""
    for key in ("new", "old"):
        val = change.get(key)
        if isinstance(val, dict):
            for field in _STRUCT_TEXT_FIELDS:
                sub = val.get(field)
                if isinstance(sub, str) and sub.strip():
                    return _clip(sub)
        elif isinstance(val, str) and val.strip():
            return _clip(val)
    return ""


def _group_label(agent: str) -> str:
    """结构化条目在两视角下的叫法（文风/术语 vs 结构）。"""
    return "文风/术语条目" if agent == AGENT_PROOFREAD else "结构条目"


def _struct_panel(v: int, words: int, changes: list[dict], other_v: int) -> dict:
    """单个产物版本的评论面板：把字段差异翻译成"信息量增减"的两视角评论。

    判定口径（确定性、可解释）：
      - 本版持有（added/modified）→ 需要保留 → 记为"写得好的地方"（信息更完整）；
      - 本版缺失（对比版有、本版删了）→ 记为"需要改进的地方"并给出补回动作；
      - 条目内容为空 → 记为"需要改进的地方"（空壳，§11.7）。
    """
    reviews = _empty_reviews(_STRUCT_EMPTY_HINT)
    by_agent: dict[str, list[dict]] = {AGENT_PROOFREAD: [], AGENT_CRITIQUE: []}
    for c in changes:
        agent = (AGENT_PROOFREAD if str(c.get("group") or "") in _STRUCT_PROOFREAD_GROUPS
                 else AGENT_CRITIQUE)
        by_agent[agent].append(c)

    for rv in reviews:
        label_group = _group_label(rv["agent"])
        strengths: list[dict] = []
        improvements: list[dict] = []
        for c in by_agent[rv["agent"]]:
            label = _struct_label(c)
            ctype = str(c.get("type") or "")
            quote = _struct_quote(c)
            if ctype in ("added", "modified"):
                strengths.append({
                    "aspect": str(c.get("group") or ""),
                    "quote": quote or label,
                    "reason": (f"本版{label_group}「{label}」"
                               f"{'为新增内容' if ctype == 'added' else '在对比版基础上做了调整'}，"
                               f"是 v{other_v} 未提供的信息"),
                })
                if not quote.strip():
                    improvements.append({
                        "aspect": str(c.get("group") or ""),
                        "quote": "",
                        "type": f"{label_group}空条目",
                        "level": "中改",
                        "problem": f"{label_group}「{label}」内容为空，下游注入会显示空标题",
                        "suggestion": f"补齐「{label}」的具体内容，或删除该条目（§11.7：不落空壳）",
                        "expected_gain": 0,
                    })
            elif ctype == "removed":
                improvements.append({
                    "aspect": str(c.get("group") or ""),
                    "quote": quote or label,
                    "type": f"{label_group}缺失",
                    "level": "中改",
                    "problem": f"{label_group}「{label}」在 v{other_v} 中存在，本版已删除",
                    "suggestion": (f"若该{label_group}仍需生效，把「{label}」从 v{other_v} 补回本版；"
                                   f"确认废弃可忽略（下游不再依赖该条目时）"),
                    "expected_gain": 0,
                })
        rv["strengths"] = strengths[:MAX_ITEMS_PER_SECTION]
        rv["improvements"] = improvements[:MAX_ITEMS_PER_SECTION]
    return {"v": v, "words": words, "score": None, "passed": None,
            "issue_count": None, "reviews": reviews}


def _mirror_type(change: dict) -> str:
    """把 v2 视角的字段差异镜像成 v1 视角（added ↔ removed，modified 不变）。"""
    t = str(change.get("type") or "")
    if t == "added":
        return "removed"
    if t == "removed":
        return "added"
    return t


def review_structured_pair(v1: int, v2: int, changes: list[dict], *,
                           words_map: dict[int, int] | None = None) -> dict:
    """outline/world 两版的评论视角结果（零 LLM）。

    - changes：`diff_structured` 的字段差异（内部信号，不对外展示 old→new 对照）；
    - words_map：{版本号: 字数}，缺失按 0。

    注意：changes 是"以 v1 为旧、v2 为新"的单向结果，本函数把
    added/modified 记为 v2 的信息增量、removed 记为 v2 的信息缺失，
    再按镜像方向（added ↔ removed）生成 v1 视角的面板。
    """
    words_map = words_map or {}
    reversed_changes = [dict(c, type=_mirror_type(c)) for c in changes]
    return {
        "kind": "structured",
        "v1": v1,
        "v2": v2,
        "panels": [
            _struct_panel(v1, int(words_map.get(v1, 0)), reversed_changes, v2),
            _struct_panel(v2, int(words_map.get(v2, 0)), changes, v1),
        ],
        "summary": {
            "changed_blocks": len(changes),
            "added_paragraphs": sum(1 for c in changes if c.get("type") == "added"),
            "deleted_paragraphs": sum(1 for c in changes if c.get("type") == "removed"),
            "rewritten_paragraphs": sum(1 for c in changes if c.get("type") == "modified"),
            "truncated": False,
        },
    }
