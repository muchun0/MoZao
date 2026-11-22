# -*- coding: utf-8 -*-
"""R27 确认卡渲染：从产物只读生成摘要、动作与预估价（不含任何状态机逻辑）。

摘要失败不能挡住确认流程：异常时返回空 lines + error，卡片仍可操作。
"""
from __future__ import annotations

from .generation_flow_defs import STAGE_AGENT, STAGE_LABELS

from .logger import get_logger

logger = get_logger("generation_flow_cards")


def _clip(text, limit: int = 90) -> str:
    s = str(text or "").strip().replace("\n", " ")
    return s if len(s) <= limit else s[:limit] + "…"


def price_hint(store, key: str) -> float:
    """只读预估价（BYOK 恒 0；失败降级为 0，不影响卡片渲染）。"""
    try:
        from . import db, generate_service

        uid = db.project_owner(store.dir.name)
        mode = generate_service.resolve_billing_mode(uid)
        if key == "volume":
            return 0.0
        agent = STAGE_AGENT[key]
        params = {"scope": "all"} if key == "chapters" else {}
        return float(generate_service._estimated_price(agent, params, store, mode))
    except Exception:
        logger.exception("阶段预估价计算失败（降级为 0）: project=%s stage=%s",
                         store.dir.name, key)
        return 0.0


def card_actions(key: str, status: str) -> list[str]:
    if key == "chapters":
        if status == "ready":
            return ["continue", "back_outline"]
        if status in ("failed", "stopped"):
            return ["continue"]
        return []
    if status == "ready":
        return ["continue"]
    if status == "done":
        return ["continue", "edit", "regenerate"] if key in ("outline", "world") \
            else ["continue", "regenerate"]
    if status == "skipped" and key == "volume":
        return []
    if status in ("failed", "stopped"):
        return ["regenerate"]
    return []


def build_card(store, st: dict, cfg) -> dict | None:
    """后端从产物只读生成卡片摘要；失败时 lines=[] + error，卡片仍可操作。"""
    key, status = st["key"], st["status"]
    if status not in ("done", "ready", "failed", "stopped", "skipped", "confirmed"):
        return None
    lines: list[str] = []
    error = ""
    try:
        if key == "research" and status in ("done", "confirmed", "skipped"):
            report = store.load_research()
            if report is not None:
                lines = [f"题材：{report.genre or cfg.genre}",
                         f"共性：{_clip(report.summary, 100)}"]
                lines += [f"流行方向：{_clip(x, 50)}" for x in (report.trends or [])[:3]]
                lines += [f"可混合文风：{_clip(x, 50)}" for x in (report.styles or [])[:3]]
        elif key == "outline" and status in ("done", "confirmed"):
            outline = store.load_outline()
            if outline is not None:
                lines = [f"一句话卖点：{_clip(outline.intro, 100)}",
                         f"主线：{_clip(outline.main_line or outline.core_conflict, 100)}",
                         f"结构：{len(outline.chapters)} 章 / {len(outline.volumes)} 卷"]
                for v in (outline.volumes or [])[:3]:
                    lines.append(f"{v.name or ('第%s卷' % v.no)}：{_clip(v.intro, 50)}")
        elif key == "world" and status in ("done", "confirmed"):
            world = store.load_world()
            if world is not None:
                names = [c.name for c in (world.characters or []) if getattr(c, "name", "")]
                lines = [f"世界观：{_clip(world.world_view, 100)}",
                         f"人物卡：{len(names)} 人" + (f"（{'、'.join(names[:5])}）" if names else ""),
                         f"机制类型：{'、'.join(world.mechanism_types or []) or '未声明'}"]
                lines += [f"核心设定：{_clip(x, 60)}" for x in (world.locked_settings or [])[:3]]
        elif key == "volume":
            lines = ["卷纲已并入大纲一次生成（大纲阶段已产出分卷与逐章计划）"]
        elif key == "chapters" and status == "ready":
            outline = store.load_outline()
            total = len(outline.chapters) if outline else 0
            words = int(getattr(cfg, "chapter_words", 2500) or 2500)
            done = len(store.list_chapters())
            lines = [f"计划：全书 {total} 章，待写 {max(0, total - done)} 章",
                     f"目标字数：约 {total * words} 字",
                     f"预估费用：¥{price_hint(store, 'chapters'):.2f}（按实际生成字数结算）"]
    except Exception as exc:  # noqa: BLE001 — 摘要失败不能挡住确认
        logger.exception("确认卡摘要生成失败: project=%s stage=%s", store.dir.name, key)
        lines, error = [], f"{type(exc).__name__}: 摘要暂不可用"
    actions = card_actions(key, status)
    card = {"stage": key, "title": STAGE_LABELS[key], "status": status,
            "lines": lines, "actions": actions, "digest": st.get("digest") or "",
            "edited": bool(st.get("edited_at")), "error": error,
            "reason": st.get("reason") or "",
            "message": (st.get("last_error") or {}).get("message", "")}
    if "regenerate" in actions and key != "chapters":
        card["regenerate_estimate"] = price_hint(store, key)
    return card
