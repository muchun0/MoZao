# -*- coding: utf-8 -*-
"""小墨AI 上下文组装（纯函数为主，便于离线单测）。

分层（TECH §7）：
  层1 system = assistant.md 人设 + <story_bible>（可裁）
  层2 user   = <chapter_context>（质检事实/体检/正向事实/设计卡/前情）+ <chapter_text>
  层3 user   = <dialogue> 最近 N 轮 + 当前提问

只读纪律：本模块不写 projects/<id>/ 任何文件，不做 SQL。
"""
from __future__ import annotations

import json
import math

from ..logger import get_logger

logger = get_logger("assistant")

__all__ = ["estimate_tokens", "snapshot", "positive_snapshot", "build_prompt"]

#: 中文 token 近似（保守）：1 token ≈ 1.6 字符
CHARS_PER_TOKEN = 1.6
#: 正文全量注入上限（字符），超出取首尾各半并标注截断
CHAPTER_TEXT_LIMIT = 12000
#: 正向事实块字符上限
POSITIVE_LIMIT = 2000
_TRUNC = "\n……（正文过长，此处省略中段）……\n"


def estimate_tokens(text: str) -> int:
    """token 估算：ceil(len / 1.6)，用于单轮输入预算护栏。"""
    return int(math.ceil(len(text or "") / CHARS_PER_TOKEN))


def _clip(text: str, limit: int) -> str:
    t = str(text or "")
    return t if len(t) <= limit else t[:limit] + "…"


def _chapter_content(text: str, limit: int = CHAPTER_TEXT_LIMIT) -> str:
    """正文注入：不超限全量；超限取「首 half + 尾 half」并标注截断。"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = max(1, limit // 2)
    return text[:half] + _TRUNC + text[-half:]


def _read_review(store, chapter_no: int) -> dict | None:
    """读 review.json 中该章记录（质检事实权威源；无记录返回 None）。"""
    try:
        path = store.dir / "review.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        for rec in data:
            if isinstance(rec, dict) and int(rec.get("chapter", -1)) == int(chapter_no):
                return rec
    except Exception:
        logger.warning("review.json 读取失败（继续无质检事实回答）: chapter=%s", chapter_no)
    return None


def _load_design(store, chapter_no: int) -> dict | None:
    try:
        data = store.load_chapter_design()
        for item in (data or {}).get("chapters", []):
            if int(item.get("no", -1) if item.get("no") is not None else -1) == int(chapter_no):
                return item.get("design") or None
    except Exception:
        logger.warning("章级设计卡读取失败（降级跳过）: chapter=%s", chapter_no)
    return None


def _bible_text(store) -> str:
    """层1 设定库精简：书名/题材/文风/世界观摘要/主要人物/机制规则/术语。"""
    try:
        cfg = store.load_config()
    except Exception:
        logger.warning("项目配置读取失败，Bible 为空")
        return ""
    lines = [f"书名：{cfg.effective_title()}", f"题材：{cfg.genre}",
             f"单章目标字数：{cfg.chapter_words}"]
    if cfg.style:
        lines.append(f"文风：{_clip(cfg.style, 200)}")
    if cfg.premise:
        lines.append(f"核心设定：{_clip(cfg.premise, 400)}")
    try:
        world = store.load_world()
    except Exception:
        world = None
    if world is not None:
        if world.world_view:
            lines.append(f"世界观：{_clip(world.world_view, 800)}")
        if world.mechanism_rules:
            lines.append(f"金手指/机制规则：{_clip(world.mechanism_rules, 500)}")
        chars = [c for c in (world.characters or []) if c.name][:6]
        if chars:
            briefs = "；".join(
                f"{c.name}（{c.role or '角色'}）：{_clip(c.desc or c.personality, 80)}"
                for c in chars)
            lines.append(f"主要人物：{briefs}")
        terms = [f"{t.name}={_clip(t.definition, 60)}" for t in (world.terminology or []) if t.name][:10]
        if terms:
            lines.append("术语表：" + "；".join(terms))
    lines.append("（以上为设定背景，人物状态与伏笔以后文账本数据为准）")
    return "\n".join(lines)


def _prev_summaries(store, chapter_no: int, n: int = 2) -> list[str]:
    """前 n 章摘要（按章号倒序取最近的）。"""
    out: list[str] = []
    try:
        for no in range(int(chapter_no) - 1, max(0, int(chapter_no) - 1 - n), -1):
            ch = store.load_chapter(no)
            if ch is not None and ch.summary:
                out.append(f"第{no}章 {ch.title}：{_clip(ch.summary, 300)}")
    except Exception:
        logger.warning("前情摘要读取失败（降级跳过）: chapter=%s", chapter_no)
    return out


def snapshot(store, chapter_no: int, target_words: int = 0) -> dict:
    """层2 事实快照：章节正文 + 质检事实 + 体检 + 设计卡 + Bible + 前情。"""
    chapter = None
    try:
        chapter = store.load_chapter(chapter_no)
    except Exception:
        logger.warning("章节读取失败: chapter=%s", chapter_no)
    content = (chapter.content if chapter else "") or ""
    inspector: dict = {}
    try:
        from novel_gen.pipeline.scene_inspector import inspect_chapter
        inspector = inspect_chapter(store, chapter_no, target_words) or {}
    except Exception:
        logger.warning("场景体检失败（降级跳过）: chapter=%s", chapter_no)
    return {
        "chapter_no": int(chapter_no),
        "title": (chapter.title if chapter else "") or "",
        "content": content,
        "summary": (chapter.summary if chapter else "") or "",
        "exists": chapter is not None,
        "review": _read_review(store, chapter_no),
        "inspector": inspector,
        "design": _load_design(store, chapter_no),
        "bible": _bible_text(store),
        "prev_summaries": _prev_summaries(store, chapter_no),
    }


def _goods_items(store) -> list[dict]:
    """正向范本结构化摘要：demo 池优先、5 类各 ≤1 条、来源优先级 user > external > book。"""
    try:
        data = store.load_book_memory() or {}
        samples = (data.get("goods") or {}).get("samples") or []
    except Exception:
        logger.warning("goods 账本读取失败（降级无范本）")
        return []
    pool = [s for s in samples if s.get("level") == "demo"] or samples
    priority = {"user": 0, "external": 1, "book": 2}
    cats = ("对话-潜台词", "战斗-动作链", "情绪-白描+点破", "环境-物象白描", "叙事-节奏")
    out: list[dict] = []
    for cat in cats:
        cand = [s for s in pool if s.get("cat") == cat and s.get("text")]
        if not cand:
            continue
        cand.sort(key=lambda s: priority.get(s.get("src_type"), 3))
        s = cand[0]
        out.append({
            "cat": cat,
            "text": _clip(s.get("text"), 300),
            "src_ch": s.get("src_ch"),
            "src_type": s.get("src_type") or "book",
            "note": "手法标签（规则粗分）",
        })
    return out


def positive_snapshot(store, chapter_no: int, cfg=None, snap: dict | None = None) -> dict:
    """正向事实快照：ai_taste 指标现算 + 直白点破 + goods 摘要（零 LLM、零落盘）。

    snap 可传入已建好的层2 快照，避免重复读章节与体检（只算一次）。
    """
    if snap is None:
        snap = snapshot(store, chapter_no,
                        target_words=getattr(cfg, "chapter_words", 0) or 0)
    content = snap.get("content") or ""
    result = {"metrics": {}, "zero_direct": None, "goods_text": "", "goods_items": [],
              "errors": []}
    try:
        from novel_gen.pipeline.ai_taste import style_drift_metrics
        result["metrics"] = style_drift_metrics(content) or {}
        if not result["metrics"]:
            result["errors"].append("正文字数不足，指标不可用")
    except Exception:
        logger.warning("正向指标计算失败（降级）: chapter=%s", chapter_no)
        result["errors"].append("指标计算失败")
    try:
        from novel_gen.pipeline.ai_taste import _zero_direct  # 单一事实来源，不复制规则
        result["zero_direct"] = bool(_zero_direct(content)) if content else None
    except Exception:
        logger.warning("直白点破检测失败（降级）: chapter=%s", chapter_no)
    try:
        from novel_gen.pipeline.book_memory import BookMemory
        result["goods_text"] = BookMemory(store, cfg).good_block(top_n=3, max_chars=900)
    except Exception:
        logger.warning("goods 范本块生成失败（降级）")
    result["goods_items"] = _goods_items(store)
    return result


# ---------------- 组装 ----------------

def _quality_block(review: dict | None, issue_limit: int, detail_limit: int) -> str:
    if not review:
        return "本章未跑质检（无质检记录）。"
    qc = review.get("quality_check") or {}
    head = [f"是否触发过自动重写：{'是' if review.get('auto_revised') else '否'}",
            f"质检扣分（缺陷分，仅反映缺陷扣减，不代表质量）：{qc.get('score', '-')}",
            f"质检是否通过：{'是' if qc.get('passed') else '否'}"]
    if qc.get("hook_score") is not None:
        head.append(f"追更欲评分：{qc['hook_score']}/5")
    issues = (review.get("issues") or [])[:issue_limit]
    lines = list(head)
    if not issues:
        lines.append("问题清单：无")
    else:
        order = {"大改": 0, "中改": 1, "小改": 2}
        issues = sorted(issues, key=lambda i: order.get(i.get("level"), 3))
        lines.append(f"问题清单（共 {len(issues)} 条）：")
        for idx, it in enumerate(issues, 1):
            lines.append(
                f"- Q-{idx}【{it.get('level', '?')}｜{it.get('type', '?')}】"
                f"{_clip(it.get('detail', ''), detail_limit)}")
    return "\n".join(lines)


def _inspector_block(inspector: dict, brief: bool) -> str:
    dims = (inspector or {}).get("dimensions") or []
    if not dims:
        return "无场景体检记录。"
    lines = [f"字数：{(inspector or {}).get('words', 0)}"
             f" / 目标 {(inspector or {}).get('target', 0)}"]
    for d in dims:
        if brief:
            lines.append(f"- {d.get('dim')}：{d.get('level')}｜{_clip(d.get('detail'), 60)}")
        else:
            lines.append(f"- {d.get('dim')}：{d.get('level')}｜{_clip(d.get('detail'), 160)}"
                         f"｜建议：{_clip(d.get('suggestion'), 120)}")
    return "\n".join(lines)


def _positive_block(positive: dict, with_goods: str) -> str:
    """正向事实块：指标实测值 + 阈值 + 命中项（不含合成分数）。"""
    m = (positive or {}).get("metrics") or {}
    lines: list[str] = []
    if m:
        lines.append(
            f"- 句长波动系数：{m.get('sentence_cv')}（参考区间 0.45–1.20；过低=句式趋同，过高=节奏失控）")
        lines.append(f"- 词汇多样性 TTR：{m.get('ttr')}（参考 ≥0.35）")
        lines.append(f"- 填充词占比：{m.get('filler_ratio')}（参考 ≤0.006）")
        lines.append(f"- 明喻密度：{m.get('simile_per_1k')}/千字（参考 <2.0）")
        if positive.get("zero_direct") is True:
            lines.append("- 直白点破：全文无一处（整章级判断，无法定位到场景）")
    else:
        lines.append("- 指标不可用（正文缺失或过短）")
    for err in (positive or {}).get("errors") or []:
        lines.append(f"- 注意：{err}")
    if with_goods == "full" and (positive or {}).get("goods_text"):
        lines.append("")
        lines.append(positive["goods_text"])
    elif with_goods == "none":
        lines.append("- 本作范本：本次不注入")
    lines.append("（以上指标是警示线参考，不是质量评分；不得据此判定章节好坏）")
    return _clip("\n".join(lines), POSITIVE_LIMIT)


def _design_block(design: dict | None, mode: str) -> str:
    if not design:
        return "无本章设计卡记录。"
    keep = ("a1", "A1", "a2", "A2", "a4", "A4") if mode == "brief" else None
    if keep is None:
        return _clip(json.dumps(design, ensure_ascii=False), 900)
    picked = {k: v for k, v in design.items() if k in keep}
    return _clip(json.dumps(picked or design, ensure_ascii=False), 500)


def _dialogue_block(dialogue: list[dict], turns: int) -> str:
    if turns <= 0 or not dialogue:
        return ""
    msgs = [m for m in dialogue if m.get("role") in ("user", "assistant")][-turns * 2:]
    lines = []
    for m in msgs:
        who = "用户" if m.get("role") == "user" else "小墨"
        lines.append(f"{who}：{_clip(m.get('content'), 600)}")
    return "\n".join(lines)


#: 裁剪档位（从宽到严）；budget 超限时逐级降档（TECH §7.1 固定顺序）
_LEVELS = (
    {"issues": 30, "detail": 200, "design": "full", "prev": True,
     "text": CHAPTER_TEXT_LIMIT, "goods": "full", "insp_brief": False, "range": 1600},
    {"issues": 20, "detail": 160, "design": "brief", "prev": True,
     "text": 8000, "goods": "full", "insp_brief": False, "range": 1600},
    {"issues": 10, "detail": 120, "design": "brief", "prev": False,
     "text": 4000, "goods": "none", "insp_brief": True, "range": 1600},
    {"issues": 5, "detail": 90, "design": "none", "prev": False,
     "text": 0, "goods": "none", "insp_brief": True, "range": 1200},
)


def _render(persona: str, snap: dict, positive: dict, dialogue: list[dict],
            question: str, mode: str, lv: dict) -> tuple[str, str]:
    system = (f"{persona}\n\n<story_bible>\n{_clip(snap.get('bible'), lv['range'])}\n"
              "</story_bible>")
    ctx = [
        f"章节：第{snap.get('chapter_no')}章 {snap.get('title')}",
        "<quality_facts>",
        _quality_block(snap.get("review"), lv["issues"], lv["detail"]),
        "</quality_facts>",
        "<inspector>",
        _inspector_block(snap.get("inspector"), lv["insp_brief"]),
        "</inspector>",
        "<positive_facts>",
        _positive_block(positive, lv["goods"]),
        "</positive_facts>",
        "<design_card>",
        _design_block(snap.get("design"), lv["design"]),
        "</design_card>",
    ]
    if lv["prev"] and snap.get("prev_summaries"):
        ctx += ["<prev_summaries>", "\n".join(snap["prev_summaries"]), "</prev_summaries>"]
    parts = ["<chapter_context>", *ctx, "</chapter_context>"]
    if lv["text"] and snap.get("content"):
        parts += ["<chapter_text>", _chapter_content(snap["content"], lv["text"]),
                  "</chapter_text>"]
    dlg = _dialogue_block(dialogue, 3 if lv["issues"] <= 10 else 6)
    if dlg:
        parts += ["<dialogue>", dlg, "</dialogue>"]
    parts += [f'<question mode="{mode}">', _clip(question, 4000), "</question>"]
    return system, "\n".join(parts)


def build_prompt(persona: str, snap: dict, positive: dict, dialogue: list[dict],
                 question: str, mode: str, token_limit: int = 20000) -> dict:
    """组装并裁剪：返回 {system, user, tokens, level}；level=所用裁剪档位（从 0 起）。"""
    system = user = ""
    for idx, lv in enumerate(_LEVELS):
        system, user = _render(persona, snap, positive, dialogue, question, mode, lv)
        used = estimate_tokens(system) + estimate_tokens(user)
        if used <= token_limit:
            return {"system": system, "user": user, "tokens": used, "level": idx}
    # 极端兜底：砍掉正文与对话后仍超限时，硬截断 user（保留人设与问题）
    system, user = _render(persona, snap, positive, [], question, mode, _LEVELS[-1])
    limit_chars = int(max(1000, token_limit * CHARS_PER_TOKEN)) - len(system)
    if len(user) > limit_chars:
        user = user[:max(500, limit_chars)]
        logger.warning("上下文超预算，已硬截断 user（chapter=%s）", snap.get("chapter_no"))
    used = estimate_tokens(system) + estimate_tokens(user)
    return {"system": system, "user": user, "tokens": used,
            "level": len(_LEVELS), "truncated": True}
