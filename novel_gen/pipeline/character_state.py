# -*- coding: utf-8 -*-
"""P0-2 人物动态状态快照。

每章正文落盘后更新人物状态：
- 确定性层：正文中出现的人物记录 last_seen（零 LLM 成本，永远保底）；
- LLM 层（cfg.character_state_llm 开启时）：抽取出场人物 位置/身体/情绪/
  关系变化/新信息/当前目标，按人物名合并覆盖。
任何失败静默降级为仅确定性记录，绝不阻塞生成主流程。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_LLM_UPDATE = 6  # 单次最多让 LLM 更新前 N 个出场人物
STATE_FIELDS = ("location", "body", "emotion", "relations", "new_info", "goal")
FIELD_ZH = {"location": "位置", "body": "身体", "emotion": "情绪", "goal": "当前目标"}


def extract_character_states(llm, cfg, world, chapter, no: int, old_states: dict) -> dict:
    """合并更新人物状态快照并返回新快照（永不抛异常）。

    - 确定性层：正文出现的人物记录 last_seen=no
    - LLM 层：抽取状态细节，覆盖同名人物
    """
    states = dict(old_states or {})
    names = [c.name for c in (world.characters or []) if c.name]
    if not names or not chapter or not chapter.content:
        return states

    text = chapter.content
    seen = [n for n in names if n in text]
    for n in seen:
        st = dict(states.get(n) or {})
        st["last_seen"] = no
        states[n] = st

    if seen and getattr(cfg, "character_state_llm", True):
        try:
            updates = _llm_extract(llm, cfg, world, chapter, no, seen[:MAX_LLM_UPDATE])
            for u in updates or []:
                name = (u.get("name") or "").strip()
                if not name or name not in names:
                    continue
                st = dict(states.get(name) or {})
                st["last_seen"] = no
                for f in STATE_FIELDS:
                    v = u.get(f)
                    if v:
                        st[f] = v if isinstance(v, list) else str(v)
                states[name] = st
        except Exception:
            logger.warning("LLM 状态抽取失败，仅保留出场记录: no=%d", no)
    return states


def _llm_extract(llm, cfg, world, chapter, no: int, names: list[str]) -> list[dict]:
    """调用 LLM 抽取出场人物状态，返回 [{name, location, body, emotion, relations, new_info, goal}]。"""
    system = (
        "你是小说人物状态管理员。根据给定章节正文，更新出场人物的当前状态。"
        '只输出合法 JSON：{"updated":[{"name":"人物名","location":"当前位置",'
        '"body":"身体状态（无变化写“无明显变化”）","emotion":"情绪状态",'
        '"relations":["与某人的关系变化：..."],"new_info":["本章新获得的信息/线索"],'
        '"goal":"当前目标"}]}。未出场或状态无变化的人物不要输出。只输出 JSON。'
    )
    char_lines = [
        f"{c.name}（{c.role}）——{c.desc}" for c in (world.characters or []) if c.name in names
    ]
    user = (
        f"人物卡：\n" + "\n".join(char_lines)
        + f"\n\n第{no}章《{chapter.title}》正文：\n{chapter.content[:3500]}"
    )
    data = llm.chat_json(system, user, temperature=0.3, max_tokens=500)
    if not isinstance(data, dict):
        return []
    updates = data.get("updated")
    return updates if isinstance(updates, list) else []


def format_state_line(name: str, st: dict) -> str:
    """把单个人物状态格式化为一行注入文本（中文标签）。"""
    bits = [f"第{st.get('last_seen', '?')}章"]
    for f in ("location", "body", "emotion", "goal"):
        v = st.get(f)
        if v:
            bits.append(f"{FIELD_ZH.get(f, f)}：{v}")
    rel = st.get("relations") or []
    if isinstance(rel, list) and rel:
        bits.append("关系：" + "；".join(str(x) for x in rel[:3]))
    info = st.get("new_info") or []
    if isinstance(info, list) and info:
        bits.append("新信息：" + "；".join(str(x) for x in info[:3]))
    return f"{name}（{'，'.join(bits)}）"
