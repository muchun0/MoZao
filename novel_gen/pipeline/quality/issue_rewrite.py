# -*- coding: utf-8 -*-
"""单条改稿建议的定点局部改写（R1：改稿建议可解释、可执行）。

复用质检层既有的定位（`text_tools.locate_problem_spans`）与段落重写
（`QualityChecker._rewrite_span`），把「按此改」暴露给上层：只替换命中的段落，
其余正文逐字符保持不变。定位失败或窗口过大时明确返回失败原因，不做整章兜底
（整章重写会改变用户未选中的内容，不符合「只改这一处」的语义）。
"""
from __future__ import annotations

import re
from typing import Optional

from .checker import QualityChecker
from .text_tools import align_to_paras, iter_sentences, locate_problem_spans, norm_sentence

__all__ = ["locate_issue_span", "rewrite_issue"]

# 命中窗口占正文比例上限：超过视为「定位失准」，拒绝改写而不是悄悄改成整章
_MAX_SPAN_RATIO = 0.6
# 与 text_tools 的 detail 片段口径保持一致：8~40 字、非标点片段
_FRAG_RE = re.compile(r"[“\"『]?([^”\"』，。；：]{8,40})[”\"』]?")


def _narrow_to_anchor_para(content: str, span: tuple[int, int],
                           issue: dict) -> tuple[int, int]:
    """把句锚点窗口收窄到命中句所在段落，避免 ±200 字窗口连带改写邻段。"""
    start, end = span
    seg = content[start:end]
    for match in _FRAG_RE.finditer(str(issue.get("detail") or "")):
        frag = norm_sentence(match.group(1).strip())
        if len(frag) < 8:
            continue
        for sentence, _ in iter_sentences(seg):
            raw = sentence.strip()  # 分句可能带上一段遗留的换行，定位需用去空白后的句子
            norm = norm_sentence(raw)
            if norm and (frag in norm or norm in frag):
                i = start + seg.find(raw)
                ps = content.rfind("\n", 0, i) + 1
                pe = content.find("\n", i + len(raw))
                return ps, (len(content) if pe == -1 else pe)
    return span


def _span_by_pos(content: str, pos: str) -> Optional[tuple[int, int]]:
    """按「段3 / 第3段」定位段落；按非空行计数，跳过空行造成的错位。"""
    m = re.search(r"(\d+)", str(pos or ""))
    if not m:
        return None
    paras = [(seg.start(), seg.end()) for seg in re.finditer(r"[^\n]+", content)
             if seg.group().strip()]
    n = int(m.group(1))
    return paras[n - 1] if 1 <= n <= len(paras) else None


def locate_issue_span(content: str, issue: dict) -> Optional[tuple[int, int]]:
    """定位该问题对应的正文段落窗口（起点, 终点）；无法安全定位返回 None。

    优先级：真实 evidence 原文精确匹配 → detail 引用片段所在段落 → pos 段落。
    每层都收窄到单段并对齐段落边界，避免「按此改」连带改动邻段。
    """
    if not content:
        return None
    span: Optional[tuple[int, int]] = None
    evidence = str(issue.get("evidence") or "").strip()
    if evidence:
        i = content.find(evidence)
        if i >= 0:
            span = (i, i + len(evidence))
    if span is None:
        anchored = locate_problem_spans(content, [{**issue, "pos": ""}])
        if anchored:
            span = _narrow_to_anchor_para(content, align_to_paras(content, anchored)[0], issue)
    if span is None:
        span = _span_by_pos(content, issue.get("pos", ""))
    if span is None:
        return None
    span = align_to_paras(content, [span])[0]
    start, end = span
    if end <= start or (end - start) >= len(content) * _MAX_SPAN_RATIO:
        return None
    return start, end


def rewrite_issue(cfg, llm, store, chapter_no: int, content: str, issue: dict,
                  *, plan_title: str = "", plan_goal: str = "",
                  trace=None, guard=None, memory=None) -> dict:
    """按单条 issue 局部改写，返回 {ok, content, span, rewritten_chars, reason}。

    llm/store/cfg 由调用方注入（webapp 走 `_open_pipeline`），本函数不落盘、不计费。
    """
    span = locate_issue_span(content, issue)
    if span is None:
        return {"ok": False, "content": "", "span": None, "rewritten_chars": 0,
                "reason": "无法在正文中定位该问题所在段落，未做改动"}
    start, end = span
    checker = QualityChecker(cfg, llm, store, trace=trace, guard=guard, memory=memory)
    new_content = checker._rewrite_span(
        chapter_no, plan_title or f"第{chapter_no}章", plan_goal or "",
        content, span, [issue])
    if not new_content or new_content == content:
        return {"ok": False, "content": "", "span": span, "rewritten_chars": 0,
                "reason": "模型未返回可用的局部改写结果，原文未改动"}
    # 字符级校验：窗口外文本必须逐字符不变（拼接构造保证，这里显式兜底）
    tail_len = len(content) - end
    tail_same = new_content[-tail_len:] == content[end:] if tail_len else True
    if new_content[:start] != content[:start] or not tail_same:
        return {"ok": False, "content": "", "span": span, "rewritten_chars": 0,
                "reason": "局部改写越界（窗口外文本被改动），已放弃本次结果"}
    replacement = new_content[start:len(new_content) - tail_len] if tail_len else new_content[start:]
    return {"ok": True, "content": new_content, "span": span,
            "rewritten_chars": len(replacement), "reason": ""}
