# -*- coding: utf-8 -*-
"""R26 剧情走向候选：上下文组装 / 提示词 / 解析（纯函数为主，便于离线单测）。

与 R25 `inline_prompt` 的分工：
- 走向候选只产出「方向 + 2~3 句」，不写正文，因此不注入风格范文与个人锚；
- 上下文增三块（未回收伏笔 / 章末承诺 / 本章及后一章大纲节点），各设上限；
- 解析严格「先剥代码围栏，再 json.loads」，不做正则截取（旧实现正则脆弱）。
"""
from __future__ import annotations

import json
import re

from novel_gen.pipeline.memory import Memory
from novel_gen.store.wordcount import count_words

from .inline_prompt import chapter_plan
from .logger import get_logger

logger = get_logger("branch")

__all__ = ["SOURCE_TAGS", "STUCK_TYPES", "build_prompt", "parse_directions",
           "max_tokens", "joined_text"]

SOURCE_TAGS = ("foreshadow", "commitment", "conflict", "twist", "pacing")
STUCK_TYPES = {
    "foreshadow": "伏笔该收了：作者不知道哪条伏笔该在本章/下一章回收",
    "commitment": "承诺要兑现：章末留下的话头必须接住",
    "conflict": "冲突不够强：局面需要更大的压力或对抗",
    "pace": "节奏拖了：需要推进速度或换场景",
}
STUCK_CUSTOM = "custom"

MAX_TOKENS = 1600
TEMPERATURE = 0.9
CONTEXT_MAX_CHARS = 6000    # Memory 上下文（正文/设定）预算，给三块账本留空间
FORESHADOW_TOP = 5
COMMITMENT_CHAPTERS = 3
SUMMARY_MAX = 200           # 服务端容错上限（提示词要求 ≤120 字）

_ACTIVE_FS_STATES = {"buried", "due", "overdue", "stalled"}


def _foreshadow_block(store, cfg, ch_no: int) -> str:
    """未回收伏笔 Top-N（与体检页同一 `build_foreshadow` 口径）。"""
    try:
        from novel_gen.pipeline.consistency_report import build_foreshadow
        rows = build_foreshadow(store, cfg, ch_no)
    except Exception:
        logger.warning("走向候选：伏笔台账读取失败（省略该块）: ch=%s", ch_no)
        return ""
    active = [r for r in rows if str(r.get("state") or "") in _ACTIVE_FS_STATES]
    lines = []
    for r in active[:FORESHADOW_TOP]:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        lines.append(
            f"- {name}（第{r.get('buried_ch') or '?'}章埋，计划第{r.get('reveal_ch') or '?'}章回收，"
            f"状态：{r.get('state_label') or r.get('state')}）")
    return "\n".join(lines)


def _commitment_block(store, ch_no: int) -> str:
    """最近几章未兑现的章末承诺（pending_commitments 台账）。"""
    try:
        data = store.load_pending_commitments()
    except Exception:
        logger.warning("走向候选：承诺台账读取失败（省略该块）: ch=%s", ch_no)
        return ""
    if not isinstance(data, dict):
        return ""
    keys = sorted(int(k) for k in data if str(k).isdigit() and int(k) <= int(ch_no))
    lines = []
    for key in keys[-COMMITMENT_CHAPTERS:]:
        ledger = data.get(str(key)) or {}
        missed = list(ledger.get("missed") or ledger.get("must_carry_to_next") or [])
        for beat in missed[:3]:
            text = beat if isinstance(beat, str) else str(
                (beat or {}).get("beat") or (beat or {}).get("desc") or beat)
            text = text.strip()
            if text:
                lines.append(f"- 第{key}章承诺未兑现：{text}")
    return "\n".join(lines[:6])


def _outline_block(store, ch_no: int) -> str:
    """本章 + 后一章的大纲节点（只读；缺失只保留已有部分）。"""
    parts = []
    for no, label in ((ch_no, "本章"), (ch_no + 1, "下一章")):
        plan = chapter_plan(store, no)
        if not plan:
            continue
        fields = (("core_event", "核心事件"), ("goal", "目标"),
                  ("character_moves", "人物动线"), ("cliffhanger", "章末钩子"))
        body = "；".join(f"{name}：{plan[k]}" for k, name in fields if plan.get(k))
        if body:
            parts.append(f"【{label}】{body}")
    return "\n".join(parts)


def _memory_context(store, ch_no: int, goal: str, cfg) -> str:
    """复用 Memory.build_context，按更小预算截尾部（方向候选不需要整段近文）。"""
    try:
        world = store.load_world()
        window = int(getattr(cfg, "memory_window", 5) or 5)
        ctx = Memory(store, memory_window=window).build_context(world, ch_no, goal)
    except Exception:
        logger.warning("走向候选：上下文装配失败（降级为空）: ch=%s", ch_no)
        return ""
    if len(ctx) > CONTEXT_MAX_CHARS:
        return ctx[-CONTEXT_MAX_CHARS:]
    return ctx


def build_prompt(store, cfg, chapter, req: dict) -> tuple[str, str]:
    """组装 (system, user)。`req` 含 ch/count/stuck_type/stuck_text。"""
    count = int(req["count"])
    stuck = req.get("stuck_type") or ""
    system = "\n".join([
        f"你是中文网络小说的剧情策划，正在为《{cfg.effective_title()}》第{chapter.no}章之后"
        "设计下一步剧情走向。你只提供方向，不写正文。",
        f"严格输出 JSON 数组，恰好 {count} 项，每项字段："
        '{"direction": "10 字内的方向标签", '
        '"summary": "2-3 句话说明这一步会发生什么、带来什么后果", '
        '"source_tag": "foreshadow|commitment|conflict|twist|pacing", '
        '"tags": ["不超过 3 个短标签"]}',
        "要求：",
        "1. 每个方向必须真正不同，不得同义改写；",
        f"2. summary 每条不超过 120 字，只讲走向，不写正文段落；",
        "3. 优先「还债」：能收未回收伏笔就标 foreshadow，能兑现章末承诺就标 commitment；"
        "其余按加冲突(conflict)/反转(twist)/调节奏(pacing)选择；",
        "4. 至少 1 条不是 pacing；",
        "5. 不得剧透超出下一章大纲节点的情节；",
        "6. 只输出 JSON 数组，不要 Markdown 代码块、不要解释文字。",
    ])

    user_parts = []
    plan = chapter_plan(store, chapter.no)
    if plan:
        fields = (("goal", "本章目标"), ("core_event", "核心事件"),
                  ("cliffhanger", "章末钩子"))
        card = "；".join(f"{name}：{plan[k]}" for k, name in fields if plan.get(k))
        if card:
            user_parts.append("【本章大纲卡】\n" + card)
    outline = _outline_block(store, chapter.no)
    if outline:
        user_parts.append("【大纲节点】\n" + outline)
    foreshadow = _foreshadow_block(store, cfg, chapter.no)
    if foreshadow:
        user_parts.append("【未回收伏笔（作者欠读者的）】\n" + foreshadow)
    commitment = _commitment_block(store, chapter.no)
    if commitment:
        user_parts.append("【章末承诺（未兑现）】\n" + commitment)
    ctx = _memory_context(store, chapter.no, str(plan.get("goal") or ""), cfg)
    if ctx:
        user_parts.append("【本书设定与前文】\n" + ctx)

    if stuck == STUCK_CUSTOM:
        text = str(req.get("stuck_text") or "").strip()[:200]
        if text:
            user_parts.append("【卡点（作者自述）】\n" + text)
    elif stuck:
        user_parts.append("【卡点】\n" + STUCK_TYPES.get(stuck, stuck))

    user_parts.append(
        f"【要求】\n请给出 {count} 个不同的下一步走向，优先用伏笔/承诺驱动；只输出 JSON 数组。")
    return system, "\n\n".join(user_parts)


def max_tokens() -> int:
    return MAX_TOKENS


_FENCE_HEAD = re.compile(r"^```[a-zA-Z]*\s*")
_FENCE_TAIL = re.compile(r"\s*```$")


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE_HEAD.sub("", t)
        t = _FENCE_TAIL.sub("", t)
    return t.strip()


def _normalize_item(raw: dict) -> dict:
    direction = str(raw.get("direction") or "").strip()[:20]
    summary = str(raw.get("summary") or "").strip()[:SUMMARY_MAX]
    source = str(raw.get("source_tag") or "").strip().lower()
    if source not in SOURCE_TAGS:
        source = "pacing"
    tags = raw.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip()[:12] for t in tags if str(t).strip()][:3]
    return {"direction": direction, "summary": summary,
            "source_tag": source, "tags": tags}


def parse_directions(raw: str, count: int) -> list[dict]:
    """剥代码围栏后 json.loads；字段不完整/重复方向直接丢弃（确定性，零额外 LLM）。"""
    text = _strip_fence(str(raw or ""))
    if not text:
        return []
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        norm = _normalize_item(item)
        if not norm["direction"] or not norm["summary"]:
            continue
        if norm["direction"] in seen:
            continue
        seen.add(norm["direction"])
        norm["id"] = f"d{len(out) + 1}"
        out.append(norm)
        if len(out) >= count:
            break
    return out


def joined_text(items: list[dict]) -> str:
    """候选合计文本（合规校验与字数统计共用）。"""
    return "\n\n".join(f"{i.get('direction')}：{i.get('summary')}" for i in items)


def words_of(items: list[dict]) -> int:
    return sum(count_words("chapter", {"content": joined_text([i])}) for i in items)
