# -*- coding: utf-8 -*-
"""分章设定卡索引（REQ-token优化 P1-1）。

world 阶段落盘后，纯确定性构建 `setting_cards.json`（零 LLM，Python 脚本前置处理）：
每章只记录「本章出场人物（含动机卡关联）、相关地点/生物/势力、相关伏笔」，
Memory.build_context 按章只注入本章子集，替代每章全量注入，预计每章输入 token 降 30~50%。

保守原则（防吃设定）：
- 出场人物沿用现有 Lorebook 预测逻辑（memory.predict_appearing_chars），预测失败回退全量；
- 生物/地点/势力/伏笔按「名称或描述命中本章关键词（goal+core_event+出场人物名）」匹配，
  命中为空则省略该类目（该类目为非强约束补充卡，缺了不至于推翻核心设定）；
- 强约束区（机制卡/主题/弧光/金手指五要素/设定禁忌/时钟/用户锁定）仍由 memory 原逻辑注入，不裁剪；
- 构建失败/缺失 → 调用方回退现有全量注入（质量兜底不变）。

用法：build_setting_cards(store, world, outline) -> dict（返回空 dict = 调用方跳过落盘）
"""
from __future__ import annotations


def _match_items(items: list, keywords: set, goal_blob: str) -> list:
    """返回 items 中与本章相关的项（保守匹配）。

    命中条件（任一）：
    1. 该项 name 直接出现在本章目标文本（goal + core + 出场人物名）中——最直接的出场信号；
    2. 名称或任一文本字段命中任一关键词（出场人物名等）。
    """
    out = []
    for it in items or []:
        if not it:
            continue
        name = (getattr(it, "name", "") or "").strip()
        blob = name + " " + " ".join(
            str(getattr(it, f, "") or "") for f in (
                "desc", "form", "senses", "behavior", "kill",
                "terrain", "light", "exits", "goal", "relation_to_mc",
                "content", "hint",
            )
        )
        if (name and goal_blob and name in goal_blob) or \
                any(k and k in blob for k in keywords):
            out.append(it)
    return out


def build_setting_cards(store, world, outline) -> dict:
    """确定性构建分章设定卡索引。任何异常返回空 dict（调用方跳过，回退全量注入）。"""
    try:
        if world is None or outline is None or not outline.chapters:
            return {}
        chars = [c for c in (world.characters or []) if getattr(c, "name", "")]
        enemies = list(world.enemies or [])
        locations = list(world.locations or [])
        factions = list(world.factions or [])
        fs = list(world.foreshadowing or [])
        try:
            summaries = [s.get("summary", "") for s in store.load_summaries()]
        except Exception:
            summaries = []
        chapters = []
        for plan in outline.chapters:
            no = int(getattr(plan, "no", 0))
            if no <= 0:
                continue
            goal = getattr(plan, "goal", "") or ""
            core = getattr(plan, "core_event", "") or ""
            # 1) 出场人物：沿用 Lorebook 预测（构建期无 char_states，传空 dict；
            #    预测失败返回全量名，保守不裁剪）
            try:
                from .memory import predict_appearing_chars
                cand = predict_appearing_chars(world, goal, summaries[-3:], {}, no)
            except Exception:
                cand = []
            hit_names = cand or [c.name for c in chars]
            # 2) 关键词：本章 goal + core_event + 出场人物名
            keywords = set()
            for kw in (goal, core):
                if kw:
                    keywords.add(kw)
            keywords.update(hit_names)
            # 3) 匹配 生物/地点/势力/伏笔：名称直判 or 关键词包含
            goal_blob = " ".join((goal, core, " ".join(hit_names)))
            ch_enemies = [e.name for e in _match_items(enemies, keywords, goal_blob)]
            ch_locations = [l.name for l in _match_items(locations, keywords, goal_blob)]
            ch_factions = [f.name for f in _match_items(factions, keywords, goal_blob)]
            ch_fs = []
            for x in fs:
                if hasattr(x, "content"):
                    blob = f"{getattr(x, 'name', '') or ''} {getattr(x, 'content', '') or ''} " \
                           f"{getattr(x, 'hint', '') or ''}"
                else:
                    blob = str(x)
                if any(k and k in blob for k in keywords):
                    ch_fs.append((blob or "")[:80])
            chapters.append({
                "no": no,
                "chars": hit_names,
                "enemies": ch_enemies,
                "locations": ch_locations,
                "factions": ch_factions,
                "foreshadows": ch_fs,
            })
        return {"version": 1, "chapters": chapters}
    except Exception:
        return {}
