# -*- coding: utf-8 -*-
"""版本 diff 与选版推荐纯函数（REQ-版本对比与推荐）。

职责：
  1. `diff_chapter_text`：两版章节正文的段落级对齐 diff（替换块再句级细化）；
  2. `diff_structured`：两版 outline/world（嵌套 dict/list）的字段级 diff；
  3. `recommend_versions`：基于每版已落库的确定性数据（质检/追更欲/字数/来源）
     输出三态徽章 + 可解释理由，**零 LLM、不写盘、不扣费**。

设计纪律（AGENTS.md §11）：
  - 纯函数、novel_gen 内部模块，不依赖 webapp；
  - 对外不合成"质量总分"，badge 只是状态、reasons 只摆事实；
  - user_managed（用户手写/手选）版本永不被推荐顶选；
  - 大文本截断保护（MAX_DIFF_BLOCKS）。
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Optional

__all__ = [
    "diff_chapter_text",
    "diff_structured",
    "recommend_versions",
    "MAX_DIFF_BLOCKS",
]

#: diff 块数上限，超出后中间块折叠（防长文性能与浏览器渲染）
MAX_DIFF_BLOCKS = 200

#: 结构化 diff 的顶层路径 → 展示分组名（业务常量，唯一事实来源）
_GROUP_MAP = {
    "characters": "人物卡",
    "foreshadowing": "伏笔",
    "items": "物品",
    "power_system": "等级体系",
    "key_nodes": "关键节点",
    "volume_pacing": "分卷节奏",
    "ending_card": "结局卡",
    "terminology": "术语表",
    "dialogue_guideline": "对话风格",
    "theme_proposition": "主题命题",
    "protagonist_arc": "主角弧光",
    "character_motives": "配角动机",
    "goldfinger_rules": "金手指规则",
    "setting_taboos": "设定禁忌",
    "conflict_matrix": "冲突矩阵",
    "emotion_curve": "情绪曲线",
}

#: list-of-dict 对齐候选业务键（按优先级取第一个存在的）
_LIST_KEYS = ("name", "title", "volume_no", "no", "id", "key")

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])")
_TRUNC = 300  # 字段 old/new 截断长度


# --------------------------------------------------------------------------- #
# 1. 章节正文 diff
# --------------------------------------------------------------------------- #

def _split_paragraphs(text: str) -> list[str]:
    """按行切段，去掉纯空白行（小说段落以换行分隔，空行只是排版噪音）。"""
    lines = (text or "").split("\n")
    return [ln.strip() for ln in lines if ln.strip()]


def _split_sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENT_SPLIT.split(text or "")]
    return [s for s in parts if s]


def _refine_replace(a_paras: list[str], b_paras: list[str]) -> list[dict]:
    """replace 块进句级细化：返回 mod 块列表。"""
    a_sents = [s for p in a_paras for s in _split_sentences(p)]
    b_sents = [s for p in b_paras for s in _split_sentences(p)]
    if not a_sents and not b_sents:
        return []
    sm = difflib.SequenceMatcher(a=a_sents, b=b_sents, autojunk=False)
    blocks: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            blocks.append({"type": "equal", "text": "".join(a_sents[i1:i2])})
        elif tag == "delete":
            blocks.append({"type": "del", "a_text": "".join(a_sents[i1:i2])})
        elif tag == "insert":
            blocks.append({"type": "add", "b_text": "".join(b_sents[j1:j2])})
        else:  # replace
            blocks.append({
                "type": "mod",
                "a_text": "".join(a_sents[i1:i2]),
                "b_text": "".join(b_sents[j1:j2]),
            })
    return blocks


def diff_chapter_text(text_a: str, text_b: str,
                      max_blocks: int = MAX_DIFF_BLOCKS) -> dict:
    """两版章节正文段落级 diff。

    返回 {"identical": bool, "truncated": bool, "blocks": [...]}。
    block.type ∈ equal/add/del/mod/collapse。
    """
    a_paras = _split_paragraphs(text_a)
    b_paras = _split_paragraphs(text_b)
    if a_paras == b_paras:
        return {"identical": True, "truncated": False, "blocks": []}

    sm = difflib.SequenceMatcher(a=a_paras, b=b_paras, autojunk=False)
    raw: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                raw.append({"type": "equal", "text": a_paras[k]})
        elif tag == "delete":
            for k in range(i1, i2):
                raw.append({"type": "del", "a_text": a_paras[k]})
        elif tag == "insert":
            for k in range(j1, j2):
                raw.append({"type": "add", "b_text": b_paras[k]})
        else:  # replace：整段被改写，句级细化
            raw.extend(_refine_replace(a_paras[i1:i2], b_paras[j1:j2]))

    if len(raw) <= max_blocks:
        return {"identical": False, "truncated": False, "blocks": raw}

    # 截断：保留头/尾各 N/3 块，中间折叠
    keep = max(8, max_blocks // 3)
    head, tail = raw[:keep], raw[-keep:]
    collapsed = len(raw) - keep * 2
    blocks = head + [{"type": "collapse", "count": collapsed}] + tail
    return {"identical": False, "truncated": True, "blocks": blocks}


# --------------------------------------------------------------------------- #
# 2. 结构化 diff（outline / world）
# --------------------------------------------------------------------------- #

def _align_key(item: dict) -> Optional[str]:
    for k in _LIST_KEYS:
        v = item.get(k)
        if v is not None and str(v) != "":
            return f"{k}={v}"
    return None


def _truncate(v: Any) -> Any:
    if isinstance(v, str) and len(v) > _TRUNC:
        return v[:_TRUNC] + "…(截断)"
    return v


def diff_structured(a: Any, b: Any, path: str = "",
                    out: Optional[list[dict]] = None) -> list[dict]:
    """递归字段级 diff。返回 changes：{group, path, type, name, old, new}。

    list-of-dict 按业务键（name/no/volume_no…）对齐，不按下标；
    旧数据缺字段按 None 处理，不抛异常。
    """
    if out is None:
        out = []

    if type(a) is not type(b) and not (a is None or b is None):
        out.append({"group": _group_of(path), "path": path or "/",
                    "type": "modified", "name": path, "old": _truncate(a),
                    "new": _truncate(b)})
        return out

    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}" if path else str(key)
            if key not in a:
                out.append({"group": _group_of(child), "path": child,
                            "type": "added", "name": key, "old": None,
                            "new": _truncate(b[key])})
            elif key not in b:
                out.append({"group": _group_of(child), "path": child,
                            "type": "removed", "name": key, "old": _truncate(a[key]),
                            "new": None})
            else:
                diff_structured(a[key], b[key], child, out)
        return out

    if isinstance(a, list) and isinstance(b, list):
        # list-of-dict 且能取到业务键 → 按键对齐；否则按集合差
        if a and b and isinstance(a[0], dict) and isinstance(b[0], dict):
            a_map = {_align_key(x) or i: x for i, x in enumerate(a)}
            b_map = {_align_key(x) or i: x for i, x in enumerate(b)}
            for k in sorted(set(a_map) | set(b_map), key=str):
                ca, cb = a_map.get(k), b_map.get(k)
                child = f"{path}[{k}]"
                if ca is None:
                    out.append({"group": _group_of(path), "path": child,
                                "type": "added", "name": str(k), "old": None,
                                "new": _truncate(cb)})
                elif cb is None:
                    out.append({"group": _group_of(path), "path": child,
                                "type": "removed", "name": str(k), "old": _truncate(ca),
                                "new": None})
                else:
                    diff_structured(ca, cb, child, out)
        else:
            sa, sb = set(map(str, a)), set(map(str, b))
            for v in sb - sa:
                out.append({"group": _group_of(path), "path": path,
                            "type": "added", "name": str(v), "old": None, "new": v})
            for v in sa - sb:
                out.append({"group": _group_of(path), "path": path,
                            "type": "removed", "name": str(v), "old": v, "new": None})
        return out

    if a != b:
        out.append({"group": _group_of(path), "path": path or "/",
                    "type": "modified", "name": path.split(".")[-1],
                    "old": _truncate(a), "new": _truncate(b)})
    return out


def _group_of(path: str) -> str:
    top = path.split(".")[0].split("[")[0]
    return _GROUP_MAP.get(top, "其他")


# --------------------------------------------------------------------------- #
# 3. 选版推荐（确定性状态机）
# --------------------------------------------------------------------------- #

def _big_issue_count(quality: dict) -> int:
    return sum(1 for i in (quality.get("issues") or [])
               if isinstance(i, dict) and i.get("level") == "大改")


def _word_in_range(words: int, target: Optional[int]) -> bool:
    if not target or target <= 0:
        return True
    return abs(words - target) / target <= 0.35


def recommend_versions(versions: list[dict], *,
                       target_words: Optional[int] = None,
                       user_selected_v: Optional[int] = None,
                       beat_pass: Optional[dict[int, bool]] = None) -> list[dict]:
    """为版本列表就地附加 badge / reasons / fatal_issue。

    - versions：`VersionedProduct.list()` 返回项列表；
    - target_words：本章目标字数（outline/world 传 None）；
    - user_selected_v：当前被用户手动选定的版号（webapp 从 rec.user_managed 传入）；
    - beat_pass：{v: bool}，beat_gate 是否 pass（webapp 从 beat_gate.json 注入）。

    badge ∈ recommended / user_managed / blocked_compliance / neutral。
    不合成总分；user_managed 永不被顶选。
    """
    beat_pass = beat_pass or {}
    scored: list[dict] = []
    for item in versions:
        v = int(item.get("v") or 0)
        qc = item.get("quality") or {}
        words = int(item.get("words") or 0)
        badge = "neutral"
        if qc.get("compliance_rejected"):
            badge = "blocked_compliance"
        elif item.get("source") == "user" or v == user_selected_v:
            badge = "user_managed"

        big = _big_issue_count(qc)
        hook = qc.get("hook_score")
        passed = qc.get("passed")
        eligible = (badge == "neutral" and not qc.get("compliance_rejected")
                    and passed is not False and big == 0)
        key = (eligible, hook or 0, _word_in_range(words, target_words),
               bool(beat_pass.get(v)), -big)
        item["badge"] = badge
        item["reasons"] = []
        item["fatal_issue"] = _fatal(v, qc, words, target_words, badge)
        if badge == "neutral":
            scored.append((key, v))

    # 候选池：eligible=True 中按 key 最大者 recommended
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0][0]:  # 最高且 eligible
        best_v = scored[0][1]
        for item in versions:
            if int(item.get("v")) == best_v:
                item["badge"] = "recommended"
                item["reasons"] = _reasons(item, target_words, beat_pass)
                item["fatal_issue"] = None
                break

    return versions


def _reasons(item: dict, target_words: Optional[int],
             beat_pass: dict[int, bool]) -> list[str]:
    qc = item.get("quality") or {}
    v = int(item.get("v") or 0)
    words = int(item.get("words") or 0)
    reasons: list[str] = []
    hook = qc.get("hook_score")
    if isinstance(hook, (int, float)) and hook >= 4:
        reasons.append(f"读者追更欲 {hook}/5，达标")
    if not qc.get("compliance_rejected") and _big_issue_count(qc) == 0:
        reasons.append("无大改级问题")
    if beat_pass.get(v):
        reasons.append("大纲拍点兑现（beat_gate=pass）")
    if target_words and _word_in_range(words, target_words):
        reasons.append(f"字数 {words} 符合目标 {target_words}")
    return reasons[:3]


def _fatal(v: int, qc: dict, words: int, target: Optional[int],
           badge: str) -> Optional[str]:
    if badge == "blocked_compliance":
        return "合规被拒，不建议采用"
    if badge == "user_managed":
        return None
    hook = qc.get("hook_score")
    if isinstance(hook, (int, float)) and hook <= 1:
        return f"读者追更欲仅 {hook}/5"
    big = _big_issue_count(qc)
    if big:
        return f"存在 {big} 条大改问题"
    if qc.get("passed") is False:
        return "质检未过线"
    if target and target > 0:
        pct = round(words * 100 / target)
        if abs(words - target) / target > 0.35:
            return f"字数仅达目标 {pct}%"
    return None
