# -*- coding: utf-8 -*-
"""章内/章间状态账本（REQ-章内自洽与机制账本）。

解决两类高发缺陷的根因——模型在「场景级独立生成 + 静态记忆注入」的结构下
维护不了章内/章间可变状态：

- 章间：面板数字 3→2→1→1（生成第 N 章时不知道第 N-1 章结束时数字到几）；
- 章内：疤脸死而复生、物品归属错乱、时间倒挂（场景间不传「已发生事实」）。

状态 schema（dict，落 chapter_state.json）：
{
  "chapter": 5,
  "numbers":   ["可死次数：1", "蚀痕：3/10"],   # 机制数字（面板/进度，正文准确写法）
  "characters": {"疤脸": "已死（死在墟内二层）"}, # 人物生死/状态
  "items":     {"灰髓袋": "被独眼刘抢走"},        # 物品归属
  "time":      "第9天，夜间，墟外坡底",          # 时间锚点
  "facts":     ["人头押金由独眼刘垫付，陆沉欠独眼刘"],  # 已发生事实（不可逆事件）
}

LLM 抽取失败时降级：确定性正则抓「次数/进度/蚀痕…：N」面板行，其余字段留空；
再失败返回 None（调用方跳过注入，不阻塞主流程）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 确定性兜底：抓正文中的机制数字面板行（如「今日可死次数：3」「蚀痕：1/10」）。
#: 后缀（次数/进度/蚀痕/剩余）作为标签的结尾部分（前缀可空），避免「蚀痕」类
#: 无前缀标签因「后缀必须跟在标签之后」而漏配。
_PANEL_RE = re.compile(
    r"([\u4e00-\u9fff]{0,10}?(?:次数|进度|蚀痕|剩余))\s*[：:]\s*"
    r"([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
)


def _norm_text(text: str) -> str:
    return (text or "").strip()[:3500]


def extract_scene_state(llm, text: str) -> dict | None:
    """抽取「本场景结束后已达成、后续场景必须延续」的轻量状态。

    LLM 抽取为主；失败降级为确定性面板数字正则；再失败返回 None。
    """
    text = _norm_text(text)
    if not text:
        return None
    try:
        return _llm_scene_extract(llm, text)
    except Exception:
        logger.warning("场景状态 LLM 抽取失败，降级为确定性面板数字")
        nums = _deterministic_numbers(text)
        return {"numbers": nums} if nums else None


def _llm_scene_extract(llm, text: str) -> dict:
    system = (
        "你是小说场景状态记录员。根据场景正文，抽取「已达成且后续场景必须延续」的状态："
        "机制数字（面板/进度，如可死次数：2）、人物生死、物品归属、时间锚点。\n"
        '只输出合法 JSON：{"numbers":["可死次数：2"],"characters":{"疤脸":"已死"},'
        '"items":{"灰髓袋":"被独眼刘抢走"},"time":"同日午后，墟内一层"}。\n'
        "规则：只写正文明确呈现的状态；死亡/被抢等不可逆事件必须记录；"
        "数字必须是正文出现的准确写法；无某项给空对象/空数组；只输出 JSON。"
    )
    data = llm.chat_json(system, f"场景正文：\n{text}", temperature=0.3, max_tokens=400)
    if not isinstance(data, dict):
        raise ValueError("场景状态抽取返回非对象")
    out: dict = {}
    nums = data.get("numbers")
    if isinstance(nums, list):
        out["numbers"] = [str(x) for x in nums if str(x).strip()]
    chars = data.get("characters")
    if isinstance(chars, dict):
        out["characters"] = {str(k): str(v) for k, v in chars.items() if str(k).strip()}
    items = data.get("items")
    if isinstance(items, dict):
        out["items"] = {str(k): str(v) for k, v in items.items() if str(k).strip()}
    t = data.get("time")
    if isinstance(t, str) and t.strip():
        out["time"] = t.strip()
    if not any(k in out for k in ("numbers", "characters", "items", "time")):
        raise ValueError("场景状态抽取结果为空")
    return out


def merge_scene_state(acc: dict, upd: dict) -> dict:
    """把新场景状态并入累计账本（同 key 后写覆盖，facts 累积去重）。"""
    acc = dict(acc or {})
    if not upd:
        return acc
    nums = list(dict.fromkeys([str(x) for x in acc.get("numbers", [])] + [str(x) for x in upd.get("numbers", [])]))
    if nums:
        acc["numbers"] = nums
    chars = dict(acc.get("characters") or {})
    chars.update(upd.get("characters") or {})
    if chars:
        acc["characters"] = chars
    items = dict(acc.get("items") or {})
    items.update(upd.get("items") or {})
    if items:
        acc["items"] = items
    if upd.get("time"):
        acc["time"] = str(upd["time"])
    return acc


def format_scene_state(acc: dict) -> str:
    """格式化为注入下一场景的「已推进状态」提示（空则返回空串）。"""
    acc = acc or {}
    lines: list[str] = []
    nums = acc.get("numbers") or []
    if nums:
        lines.append("机制数字：" + "；".join(str(x) for x in nums))
    chars = acc.get("characters") or {}
    if chars:
        lines.append("人物生死：" + "；".join(f"{k}（{v}）" for k, v in chars.items()))
    items = acc.get("items") or {}
    if items:
        lines.append("物品归属：" + "；".join(f"{k}（{v}）" for k, v in items.items()))
    if acc.get("time"):
        lines.append(f"时间锚点：{acc['time']}")
    if not lines:
        return ""
    return (
        "【本章已推进状态·后续场景必须延续，禁止回退或重复叙述已发生事实】\n"
        + "\n".join(f"- {l}" for l in lines)
    )


def extract_chapter_state(llm, cfg, world, chapter, no: int, old_state: dict | None = None) -> dict | None:
    """每章落盘后抽取章末状态，供下一章承接。

    - LLM 层（cfg.chapter_state_llm）：抽取 numbers/characters/items/time/facts；
    - 失败降级：确定性面板数字正则；
    - 再失败返回 None（调用方跳过）。
    """
    if not chapter or not chapter.content:
        return None
    try:
        if getattr(cfg, "chapter_state_llm", True):
            state = _llm_chapter_extract(llm, chapter, no)
            state["chapter"] = no
            return state
    except Exception:
        logger.warning("章末状态 LLM 抽取失败，降级为确定性面板数字: no=%d", no)
    nums = _deterministic_numbers(chapter.content)
    if not nums:
        return None
    return {"chapter": no, "numbers": nums}


def _llm_chapter_extract(llm, chapter, no: int) -> dict:
    system = (
        "你是小说世界状态管理员。根据给定章节正文，把「机制数字、人物生死、物品归属、"
        "时间锚点、已发生事实」整理成状态清单，供下一章续写时承接。\n"
        '只输出合法 JSON：{"numbers":["可死次数：1","蚀痕：3/10"],'
        '"characters":{"疤脸":"已死（死在墟内二层）"},'
        '"items":{"灰髓袋":"被独眼刘抢走"},'
        '"time":"第9天，夜间，墟外坡底",'
        '"facts":["人头押金由独眼刘垫付，陆沉欠独眼刘"]}。\n'
        "规则：只写正文明确呈现的状态；死亡/被抢/负债等不可逆事件必须记录；"
        "数字必须是正文出现的准确写法；无某项给空对象/空数组；只输出 JSON。"
    )
    data = llm.chat_json(
        system,
        f"第{no}章《{chapter.title}》正文：\n{_norm_text(chapter.content)}",
        temperature=0.3, max_tokens=500,
    )
    if not isinstance(data, dict):
        raise ValueError("章末状态抽取返回非对象")
    out: dict = {}
    nums = data.get("numbers")
    if isinstance(nums, list):
        out["numbers"] = [str(x) for x in nums if str(x).strip()]
    chars = data.get("characters")
    if isinstance(chars, dict):
        out["characters"] = {str(k): str(v) for k, v in chars.items() if str(k).strip()}
    items = data.get("items")
    if isinstance(items, dict):
        out["items"] = {str(k): str(v) for k, v in items.items() if str(k).strip()}
    t = data.get("time")
    if isinstance(t, str) and t.strip():
        out["time"] = t.strip()
    facts = data.get("facts")
    if isinstance(facts, list):
        out["facts"] = [str(x) for x in facts if str(x).strip()]
    if not any(k in out for k in ("numbers", "characters", "items", "time", "facts")):
        raise ValueError("章末状态抽取结果为空")
    return out


def format_chapter_state(state: dict) -> str:
    """格式化为注入下一章提示词的「承接状态」文本（空则返回空串）。"""
    state = state or {}
    lines: list[str] = []
    nums = state.get("numbers") or []
    if nums:
        lines.append("机制数字：" + "；".join(str(x) for x in nums))
    chars = state.get("characters") or {}
    if chars:
        lines.append("人物状态：" + "；".join(f"{k}（{v}）" for k, v in chars.items()))
    items = state.get("items") or {}
    if items:
        lines.append("物品归属：" + "；".join(f"{k}（{v}）" for k, v in items.items()))
    if state.get("time"):
        lines.append(f"时间锚点：{state['time']}")
    facts = state.get("facts") or []
    if facts:
        lines.append("已发生事实：" + "；".join(str(x) for x in facts))
    if not lines:
        return ""
    return (
        "[上一章结束时的世界状态·本章必须延续，禁止回退或重复叙述已发生事实]\n"
        + "\n".join(f"- {l}" for l in lines)
    )


def _deterministic_numbers(text: str) -> list[str]:
    """确定性兜底：抓正文中的机制数字面板行（如「今日可死次数：3」「蚀痕：1/10」）。"""
    out: list[str] = []
    for m in _PANEL_RE.finditer(text or ""):
        label, num = m.group(1).strip(), m.group(2).replace(" ", "")
        s = f"{label}：{num}"
        if s not in out:
            out.append(s)
    return out
