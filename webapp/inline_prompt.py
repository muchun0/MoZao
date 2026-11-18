# -*- coding: utf-8 -*-
"""R25 内联写作的上下文与提示词组装（纯函数为主，便于离线单测）。

复用 `Memory.build_context`（3 层结构 + 4 档裁剪）与风格锚，不另写一套设定注入，
避免与整章生成长出两套上下文口径导致风格漂移。
"""
from __future__ import annotations

import difflib
import json
import re

from novel_gen.pipeline.memory import Memory
from novel_gen.pipeline.style_anchor import (
    PERSONAL_ANCHOR_HEADER, extract_personal_anchor, wrap_style_anchor)
from novel_gen.pipeline.templates import _style_reference
from novel_gen.store.inline_edit import normalize_text

from .logger import get_logger

logger = get_logger("inline")

__all__ = ["build_prompt", "max_tokens", "parse_candidates", "chapter_plan"]

CONTEXT_MAX_CHARS = 12000
NEAR_CHARS = 200
_SIMILARITY_MAX = 0.90


def chapter_plan(store, ch_no: int) -> dict:
    """本章大纲卡（只读）；缺失/异常降级为空 dict。"""
    try:
        outline = store.load_outline()
        if outline and 1 <= int(ch_no) <= len(outline.chapters):
            return outline.chapters[int(ch_no) - 1].to_dict()
    except Exception:
        logger.warning("读取本章大纲卡失败（降级为空）: ch=%s", ch_no)
    return {}


def _plan_card(plan: dict) -> str:
    if not plan:
        return ""
    fields = (("core_event", "核心事件"), ("conflict", "冲突设计"),
              ("goal", "本章目标"), ("character_moves", "人物动线"),
              ("foreshadowing", "伏笔"), ("cliffhanger", "章末钩子"))
    return "\n".join(f"{label}：{plan[key]}" for key, label in fields if plan.get(key))


def _memory_context(store, ch_no: int, goal: str, cfg) -> str:
    """复用 Memory.build_context；超预算只截尾部（保留强约束区），不改装配结构。"""
    try:
        world = store.load_world()
        window = int(getattr(cfg, "memory_window", 5) or 5)
        ctx = Memory(store, memory_window=window).build_context(world, ch_no, goal)
    except Exception:
        logger.warning("内联上下文装配失败（降级为空上下文）: ch=%s", ch_no)
        return ""
    if len(ctx) > CONTEXT_MAX_CHARS:
        logger.info("内联上下文超预算，保留尾部: ch=%s len=%d", ch_no, len(ctx))
        return ctx[-CONTEXT_MAX_CHARS:]
    return ctx


def build_prompt(store, cfg, chapter, req: dict) -> tuple[str, str]:
    """组装 (system, user)。`req` 需含 mode/anchor/instruction/candidates/target_words/ch。"""
    plan = chapter_plan(store, req["ch"])
    content = normalize_text(chapter.content)
    start, end = req["anchor"]["start"], req["anchor"]["end"]
    before = content[max(0, start - NEAR_CHARS):start]
    after = content[end:end + NEAR_CHARS]
    selected = content[start:end]

    mode_desc = {
        "continue": "从光标处自然承接后文",
        "rewrite": "在保持原意的前提下改写选中片段",
        "expand": "把选中片段扩写得更细腻生动",
    }[req["mode"]]
    system_parts = [
        f"你是中文网络小说写手，正在为《{cfg.effective_title()}》第{chapter.no}章做内联写作。",
        f"本次任务：{mode_desc}。严格沿用本书的人称、时态、叙事节奏与文风，不要出现解释性旁白。",
        f"输出严格 JSON 数组，恰好 {req['candidates']} 项，每项形如 {{\"text\": \"正文\"}}；"
        "不要输出 Markdown 代码块、不要写「以下是」等说明。"
        "每个候选之间必须有可辨认的方向差异，首句不得重复。",
    ]
    try:
        ref = _style_reference(cfg)
        if ref:
            system_parts.append(ref)
    except Exception:
        logger.warning("风格范文读取失败（降级跳过）: ch=%s", chapter.no)
    try:
        personal = extract_personal_anchor(store.list_chapters())
        if personal:
            system_parts.append(wrap_style_anchor(PERSONAL_ANCHOR_HEADER, personal))
    except Exception:
        logger.warning("个人风格锚读取失败（降级跳过）: ch=%s", chapter.no)

    user_parts = []
    card = _plan_card(plan)
    if card:
        user_parts.append("【本章大纲卡】\n" + card)
    ctx = _memory_context(store, chapter.no, str(plan.get("goal") or ""), cfg)
    if ctx:
        user_parts.append("【本书设定与前文】\n" + ctx)
    if selected:
        user_parts.append("【选中原文】\n" + selected)
    near = (f"【光标前文】\n{before}" if before else "") + \
           (f"\n【光标后文】\n{after}" if after else "")
    if near.strip():
        user_parts.append(near)
    demand = [f"每个候选约 {req['target_words']} 字。"]
    if req["instruction"]:
        demand.append(f"作者要求（优先满足）：{req['instruction']}")
    user_parts.append("【写作要求】\n" + "\n".join(demand))
    return "\n\n".join(system_parts), "\n\n".join(user_parts)


def max_tokens(req: dict) -> int:
    return min(6000, max(800, int(req["target_words"] * 1.8 * req["candidates"]) + 200))


_LEAD_LINE = re.compile(r"^(以下是|好的|当然|没问题)[^\n]{0,40}[:：]?\s*\n?")


def _clean_text(text: str) -> str:
    t = normalize_text(text).strip()
    t = _LEAD_LINE.sub("", t, count=1).strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    return t


def parse_candidates(raw: str, count: int) -> list[dict]:
    """解析候选 JSON；过滤空串、解释性文字与高相似度重复（确定性，零额外 LLM）。"""
    text = str(raw or "").strip()
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        items = json.loads(match.group(0))
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        chunk = item.get("text") if isinstance(item, dict) else item
        cleaned = _clean_text(chunk if isinstance(chunk, str) else "")
        if len(cleaned) < 20:
            continue
        if any(difflib.SequenceMatcher(None, cleaned, prev["text"]).ratio() > _SIMILARITY_MAX
               for prev in out):
            continue
        out.append({"id": f"c{len(out) + 1}", "text": cleaned, "direction": "", "tags": []})
        if len(out) >= count:
            break
    return out
