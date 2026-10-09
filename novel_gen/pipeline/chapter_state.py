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
  "body":      ["左臂肘部以下无知觉", "衣袖焦黑翻卷"],  # 主角身体/伤势/服饰状态
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

#: 状态抽取文本上限（字符）。3500→12000：LLM 章末/场景抽取必须覆盖全文关键
#: 状态，不能再把后半章状态截出账本（REQ-章内自洽与机制账本·扩展）。
_NORM_TEXT_LIMIT = 12000


def _norm_text(text: str) -> str:
    """抽取输入的归一化：默认全文传入。

    历史版本截断 3500 字符，导致后半章状态（第三次死亡、次数用尽等）不进账本
    ——章末账本变成「前半章账本」，错误数字被当权威状态背书给下一章
    （《万墟之上》第1章实测：facts 只到第二次死亡复活，0/3 用尽状态丢失）。
    现提高上限到 12000 字符（约 2-3 万字，单章调用可覆盖全文关键状态）。
    """
    return (text or "").strip()[:_NORM_TEXT_LIMIT]


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
        "机制数字（面板/进度，如可死次数：2）、人物生死、物品归属、时间锚点、"
        "主角身体状态（伤口位置/知觉范围/服饰损坏，如'左臂肘部以下无知觉''衣袖焦黑'）。\n"
        '只输出合法 JSON：{"numbers":["可死次数：2"],"characters":{"疤脸":"已死"},'
        '"items":{"灰髓袋":"被独眼刘抢走"},"time":"同日午后，墟内一层",'
        '"body":["左臂肘部以下无知觉","衣袖焦黑翻卷"]}。\n'
        "规则：只写正文明确呈现的状态；死亡/被抢等不可逆事件必须记录；"
        "身体状态必须写清部位与范围（'肘部以下'而非笼统'受伤'）；"
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
    body = data.get("body")
    if isinstance(body, list):
        out["body"] = [str(x) for x in body if str(x).strip()]
    t = data.get("time")
    if isinstance(t, str) and t.strip():
        out["time"] = t.strip()
    if not any(k in out for k in ("numbers", "characters", "items", "time", "body")):
        raise ValueError("场景状态抽取结果为空")
    return out


def merge_scene_state(acc: dict, upd: dict) -> dict:
    """把新场景状态并入累计账本（同 key 后写覆盖，body/facts 累积去重）。"""
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
    # 身体状态：新场景可能改写旧状态（如复活修复伤口），整体覆盖而非累积，
    # 防止"肘部以下无知觉"与"手腕以上无知觉"两个矛盾版本并存。
    if upd.get("body"):
        acc["body"] = [str(x) for x in upd["body"] if str(x).strip()]
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
    body = acc.get("body") or []
    if body:
        lines.append("主角身体状态：" + "；".join(str(x) for x in body) +
                     "（后续场景的伤口/知觉/服饰描写必须与此一致，不得自行改变范围）")
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

    - LLM 层（cfg.chapter_state_llm + 降频 state_extract_frequency，REQ-token优化 P0-4）：
      抽取 numbers/characters/items/time/facts；
    - 失败/非抽取章降级：确定性面板数字正则；
    - 再失败返回 None（调用方跳过）。
    """
    if not chapter or not chapter.content:
        return None
    try:
        if getattr(cfg, "chapter_state_llm", True) and _should_llm_extract(no, cfg):
            state = _llm_chapter_extract(llm, chapter, no)
            state["chapter"] = no
            return state
    except Exception:
        logger.warning("章末状态 LLM 抽取失败，降级为确定性面板数字: no=%d", no)
    nums = _deterministic_numbers(chapter.content)
    if not nums:
        return None
    return {"chapter": no, "numbers": nums}


def _should_llm_extract(no: int, cfg) -> bool:
    """REQ-token优化 P0-4：章末状态 LLM 抽取降频——每 state_extract_frequency 章抽 1 次，
    其余章走确定性面板数字正则（零 LLM）。1=每章（原行为）。"""
    freq = int(getattr(cfg, "state_extract_frequency", 3) or 3)
    if freq <= 1:
        return True
    return (int(no) % freq) == 1


def _llm_chapter_extract(llm, chapter, no: int) -> dict:
    system = (
        "你是小说世界状态管理员。根据给定章节正文，把「机制数字、人物生死、物品归属、"
        "时间锚点、已发生事实、主角身体状态」整理成状态清单，供下一章续写时承接。\n"
        '只输出合法 JSON：{"numbers":["可死次数：1","蚀痕：3/10"],'
        '"characters":{"疤脸":"已死（死在墟内二层）"},'
        '"items":{"灰髓袋":"被独眼刘抢走"},'
        '"time":"第9天，夜间，墟外坡底",'
        '"facts":["人头押金由独眼刘垫付，陆沉欠独眼刘"],'
        '"body":["左臂肘部以下无知觉","左臂衣袖焦黑"]}。\n'
        "规则：只写正文明确呈现的状态；死亡/被抢/负债等不可逆事件必须记录；"
        "身体状态写清部位与范围（伤口/知觉/服饰）；数字必须是正文出现的准确写法；"
        "无某项给空对象/空数组；只输出 JSON。"
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
    body = data.get("body")
    if isinstance(body, list):
        out["body"] = [str(x) for x in body if str(x).strip()]
    t = data.get("time")
    if isinstance(t, str) and t.strip():
        out["time"] = t.strip()
    facts = data.get("facts")
    if isinstance(facts, list):
        out["facts"] = [str(x) for x in facts if str(x).strip()]
    if not any(k in out for k in ("numbers", "characters", "items", "time", "facts", "body")):
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
    body = state.get("body") or []
    if body:
        lines.append("主角身体状态：" + "；".join(str(x) for x in body))
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


# ---------- P0-3 面板数字确定性方向校验（2026-10-09 生成端第三轮） ----------
# 机制卡硬规则：蚀化只增不减、每死一次次数递减、禁止面板数字自相矛盾。
# 由管线按"标签→方向规则"确定性校验（零 LLM），违反即带错重试（生成端拦截）。
_PANEL_DIR_RULES = (
    ("次数", "decrease"),   # 今日可死次数 / 可死次数：只许递减（子时重置 0→N 放行）
    ("剩余", "decrease"),
    ("蚀化", "increase"),   # 距离蚀化：只增不减（97→98→99…）
    ("进度", "increase"),
)
_PANEL_DIR_NUM_RE = re.compile(
    r"([\u4e00-\u9fff]{0,14}?(?:次数|剩余|蚀化|进度))\s*[：:]\s*"
    r"([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
)


def _panel_dir_label(label: str) -> str | None:
    """按关键词把面板标签归并到方向规则；不认识的标签返回 None（不校验）。"""
    for kw, _rule in _PANEL_DIR_RULES:
        if kw in (label or ""):
            return kw
    return None


def panel_numbers_sequence(text: str, baseline: dict[str, int]) -> dict[str, list[int]]:
    """抽取正文中机制数字序列（每个标签一条序列，前置基线值）。

    - baseline：{归一标签: 基线数值}（场景开始前的权威数字，来自上一场景结算）；
    - 返回 {规则关键词: [基线, 正文值1, 正文值2, ...]}（只含出现在正文的标签）。
    """
    out: dict[str, list[int]] = {}
    if not text:
        return out
    for m in _PANEL_DIR_NUM_RE.finditer(text):
        label = _panel_dir_label(m.group(1).strip())
        if not label:
            continue
        try:
            val = int(str(m.group(2)).split("/")[0].split("／")[0].strip())
        except Exception:
            continue
        seq = out.setdefault(label, [])
        if not seq:
            base = baseline.get(label)
            if base is not None:
                seq.append(base)
        seq.append(val)
    return out


def panel_direction_violations(text: str, baseline: dict[str, int]) -> list[str]:
    """面板数字方向校验：正文中机制数字序列违反方向规则 → 返回违规描述列表。

    - 次数/剩余（decrease）：序列中后值 > 前值 = 回升违规；但前值 == 0 且后值更大
      视为"子时重置"（机制允许）放行；
    - 蚀化/进度（increase）：序列中后值 < 前值 = 回退违规（只增不减，严格）；
    - 无基线或标签未识别 → 不校验（避免误伤）。
    """
    if not text or not baseline:
        return []
    out: list[str] = []
    for label, seq in panel_numbers_sequence(text, baseline).items():
        rule = dict(_PANEL_DIR_RULES)[label]
        if len(seq) < 2:
            continue
        for a, b in zip(seq, seq[1:]):
            if rule == "decrease" and b > a and a != 0:
                out.append(f"面板「{label}」应从基线 {seq[0]} 递减，正文出现回升 {a}→{b}")
            elif rule == "increase" and b < a:
                out.append(f"面板「{label}」应只增不减（基线 {seq[0]}），正文出现回退 {a}→{b}")
    return out


def panel_baseline_dict(numbers: list[str]) -> dict[str, int]:
    """把累计 numbers（["可死次数：2", "距离蚀化：98%"]）归一化为基线 dict（每标签取最后值）。

    返回 {规则关键词: 数值}，供注入文本与方向校验共用（同一份基线，杜绝口径漂移）。
    """
    last: dict[str, int] = {}
    for s in numbers or []:
        m = _PANEL_DIR_NUM_RE.search(str(s))
        if not m:
            continue
        label = _panel_dir_label(m.group(1).strip())
        if not label:
            continue
        try:
            last[label] = int(str(m.group(2)).split("/")[0].split("／")[0].strip())
        except Exception:
            continue
    return last


def panel_baseline_text(numbers: list[str]) -> str:
    """把累计 numbers 归一化为基线文本（每标签取最后值）；无识别标签返回空串。"""
    last = panel_baseline_dict(numbers)
    if not last:
        return ""
    return "\n".join(f"- {label}：{val}" for label, val in last.items())


def detect_reveal_hits(text: str, reveal) -> bool:
    """REQ-揭示执行跟踪：正文是否命中 reveal 的真相锚定词（keywords 任一子串）。

    - 命中即视为该章已写出该真相（用于提前剧透 / 到点未揭示判定）；
    - 无 keywords（旧数据）→ 返回 False：不参与确定性检测、不误报；
    - 零 LLM 成本，纯子串匹配（英文统一小写比对，中文原样）。
    """
    kws = [str(k).strip() for k in (getattr(reveal, "keywords", None) or []) if str(k).strip()]
    if not kws:
        return False
    t = (text or "").lower()
    return any(k and k.lower() in t for k in kws)
