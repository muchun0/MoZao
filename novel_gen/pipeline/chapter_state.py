# -*- coding: utf-8 -*-
"""章内/章间状态账本（REQ-章内自洽与机制账本）。

解决两类高发缺陷的根因——模型在「场景级独立生成 + 静态记忆注入」的结构下
维护不了章内/章间可变状态：

- 章间：面板数字 3→2→1→1（生成第 N 章时不知道第 N-1 章结束时数字到几）；
- 章内：疤脸死而复生、物品归属错乱、时间倒挂（场景间不传「已发生事实」）。

状态 schema（dict，落 chapter_state.json）：
{
  "chapter": 5,
  "numbers":   ["可死次数：1", "累计进度：3/10"],   # 机制数字（面板/进度，正文准确写法）
  "characters": {"疤脸": "已死（死在墟内二层）"}, # 人物生死/状态
  "items":     {"灰髓袋": "被独眼刘抢走"},        # 物品归属
  "time":      "第9天，夜间，墟外坡底",          # 时间锚点
  "facts":     ["人头押金由独眼刘垫付，陆沉欠独眼刘"],  # 已发生事实（不可逆事件）
  "body":      ["左臂肘部以下无知觉", "衣袖焦黑翻卷"],  # 主角身体/伤势/服饰状态
}

LLM 抽取失败时降级：确定性正则抓「次数/进度/剩余…：N」面板行，其余字段留空；
再失败返回 None（调用方跳过注入，不阻塞主流程）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 确定性兜底：抓正文中的机制数字面板行（如「今日可死次数：3」「累计进度：98%」）。
#: 后缀只保留通用机制词（次数/进度/剩余/累积/状态/数值），书级字段名（如《万墟之上》
#: 的「蚀痕/蚀化」）由调用方从设定账本派生的字段注册表动态注入（mechanism_field_registry
#: + registry 驱动的提取层），代码层不预置任何单书机制词。
_PANEL_RE = re.compile(
    r"([\u4e00-\u9fff]{0,10}?(?:次数|进度|剩余|累积|状态|数值))\s*[：:]\s*"
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
        "机制数字（按本书机制卡声明的数值/进度，如'剩余次数：2'）、人物生死、物品归属、时间锚点、"
        "主角身体状态（伤口位置/知觉范围/服饰损坏，如'左臂肘部以下无知觉''衣袖焦黑'）。\n"
        '只输出合法 JSON：{"numbers":["剩余次数：2"],"characters":{"同伴":"已死"},'
        '"items":{"旧包袱":"被路人抢走"},"time":"同日午后，城郊旧巷",'
        '"body":["左臂肘部以下无知觉","衣袖焦黑翻卷"]}。\n'
        "规则：只写正文明确呈现的状态；重伤、被抢等不可逆重大事件必须记录；"
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
    old_state = old_state if isinstance(old_state, dict) else {}
    try:
        if getattr(cfg, "chapter_state_llm", True) and _should_llm_extract(no, cfg):
            state = _llm_chapter_extract(llm, chapter, no)
            state["chapter"] = no
            _carry_rule_state(state, old_state)
            return state
    except Exception:
        logger.warning("章末状态 LLM 抽取失败，降级为确定性面板数字: no=%d", no)
    nums = _deterministic_numbers(chapter.content)
    state: dict = {"chapter": no, "numbers": nums} if nums else {"chapter": no}
    _carry_rule_state(state, old_state)
    if not nums and not any(k in state for k in
                            ("current_instance", "rules_active", "rule_events")):
        return None
    return state


def _carry_rule_state(state: dict, old_state: dict) -> None:
    """规则状态跨章累积：生效条款取并集、规则事件追加去重；当前副本沿用旧值兜底。

    数字/人物/物品/身体状态仍按章覆盖（既有行为不变）；规则一旦公布即长期有效，
    只能由 rule_events 标记 broken/resolved，不能随章末抽取丢失。
    """
    if old_state.get("current_instance") and not state.get("current_instance"):
        state["current_instance"] = old_state["current_instance"]
    old_active = old_state.get("rules_active") or []
    new_active = state.get("rules_active") or []
    merged_active = list(dict.fromkeys([str(x) for x in old_active] + [str(x) for x in new_active]))
    if merged_active:
        state["rules_active"] = merged_active
    old_events = old_state.get("rule_events") or []
    new_events = state.get("rule_events") or []
    seen = {(e.get("rule_id"), e.get("action"), e.get("detail")) for e in old_events if isinstance(e, dict)}
    for ev in new_events:
        if isinstance(ev, dict) and (ev.get("rule_id"), ev.get("action"), ev.get("detail")) not in seen:
            old_events.append(ev)
    if old_events:
        state["rule_events"] = old_events


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
        "时间锚点、已发生事实、主角身体状态、当前所在副本/场景、规则生效情况」整理成"
        "状态清单，供下一章续写时承接。\n"
        '只输出合法 JSON：{"numbers":["剩余次数：1","累积进度：3/10"],'
        '"characters":{"同伴":"已死（死在旧巷尽头）"},'
        '"items":{"旧包袱":"被路人抢走"},'
        '"time":"第9天，夜间，城外坡底",'
        '"facts":["过河钱由同伴垫付，主角欠同伴"],'
        '"body":["左臂肘部以下无知觉","左臂衣袖焦黑"],'
        '"current_instance":"旧城区公寓（规则怪谈副本名；非规则怪谈留空字符串）",'
        '"rules_active":["r1","r3"],'
        '"rule_events":[{"rule_id":"r2","action":"broken","instance":"旧城区公寓",'
        '"detail":"角色利用漏洞打破该规则"}]}。\n'
        "规则：只写正文明确呈现的状态；重伤、被抢、负债等不可逆重大事件必须记录；"
        "身体状态写清部位与范围（伤口/知觉/服饰）；数字必须是正文出现的准确写法；"
        "rules_active 只填本章结束时仍然生效的条款编号；rule_events 记录本章内条款被"
        "打破/解除/新揭示的事件（action 取 broken/resolved/discovered）；"
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
    instance = data.get("current_instance")
    if isinstance(instance, str) and instance.strip():
        out["current_instance"] = instance.strip()
    rules_active = data.get("rules_active")
    if isinstance(rules_active, list):
        out["rules_active"] = [str(x).strip() for x in rules_active if str(x).strip()]
    events = data.get("rule_events")
    if isinstance(events, list):
        parsed_events: list[dict] = []
        for ev in events:
            if not isinstance(ev, dict):
                continue
            rule_id = str(ev.get("rule_id", "")).strip()
            action = str(ev.get("action", "")).strip()
            if not rule_id or not action:
                continue
            parsed_events.append({
                "rule_id": rule_id,
                "action": action,
                "instance": str(ev.get("instance", "")).strip(),
                "detail": str(ev.get("detail", "")).strip(),
            })
        if parsed_events:
            out["rule_events"] = parsed_events
    if not any(k in out for k in ("numbers", "characters", "items", "time", "facts",
                                   "body", "current_instance", "rules_active", "rule_events")):
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
    if state.get("current_instance"):
        lines.append(f"当前副本/场景：{state['current_instance']}")
    rules_active = state.get("rules_active") or []
    if rules_active:
        lines.append("当前生效规则编号：" + "、".join(str(x) for x in rules_active)
                     + "（条款内容以静态规则卡为准，不得改写或前后矛盾）")
    if not lines:
        return ""
    return (
        "[上一章结束时的世界状态·本章必须延续，禁止回退或重复叙述已发生事实]\n"
        + "\n".join(f"- {l}" for l in lines)
    )


def _deterministic_numbers(text: str) -> list[str]:
    """确定性兜底：抓正文中的机制数字面板行（如「今日可死次数：3」「累计进度：98%」）。"""
    out: list[str] = []
    for m in _PANEL_RE.finditer(text or ""):
        label, num = m.group(1).strip(), m.group(2).replace(" ", "")
        s = f"{label}：{num}"
        if s not in out:
            out.append(s)
    return out


# ---------- P0-3 面板数字确定性方向校验（2026-10-09 生成端第三轮） ----------
# 机制卡硬规则：累积型数字只增不减、次数型只减不增、禁止面板数字自相矛盾。
# 由管线按"标签→方向规则"确定性校验（零 LLM），违反即带错重试（生成端拦截）。
# 固定规则只含通用语义词，作为 registry 为空时的兜底；书级字段名由调用方传入
# mechanism_field_registry 派生（万墟「蚀化/蚀痕」等字段从设定账本声明，代码不预置）。
_PANEL_DIR_RULES = (
    ("次数", "decrease"),   # 次数/余量类：只许递减（子时/结算重置 0→N 放行）
    ("剩余", "decrease"),
    ("进度", "increase"),   # 进度/累积类：只增不减
)
_PANEL_DIR_NUM_RE = re.compile(
    r"([\u4e00-\u9fff]{0,14}?(?:次数|剩余|进度))\s*[：:]\s*"
    r"([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
)


def _panel_dir_label(label: str) -> str | None:
    """按关键词把面板标签归并到方向规则；不认识的标签返回 None（不校验）。"""
    for kw, _rule in _PANEL_DIR_RULES:
        if kw in (label or ""):
            return kw
    return None


def panel_numbers_sequence(text: str, baseline: dict[str, int],
                           registry: dict[str, str] | None = None) -> dict[str, list[int]]:
    """抽取正文中机制数字序列（每个标签一条序列，前置基线值）。

    - baseline：{归一标签: 基线数值}（场景开始前的权威数字，来自上一场景结算）；
    - registry：mechanism_field_registry 派生的字段注册表（含书级字段名，数据驱动）；
      非空时按注册字段名逐个匹配正文（书级字段也覆盖）；空/None 时退回固定通用后缀
      正则（_PANEL_DIR_NUM_RE）；
    - 返回 {规则关键词: [基线, 正文值1, 正文值2, ...]}（只含出现在正文的标签）。
    """
    out: dict[str, list[int]] = {}
    if not text:
        return out
    if registry:
        # 注册表驱动：书级字段名（如《万墟之上》的「蚀化」）由设定账本声明，
        # 代码不预置单书机制词；前缀（今日/距离/累计…）可空，交给 {0,14}? 吞掉。
        for label in registry:
            _re = re.compile(
                r"[\u4e00-\u9fff]{0,14}?" + re.escape(label)
                + r"\s*[：:]\s*([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
            )
            for m in _re.finditer(text):
                try:
                    val = int(str(m.group(1)).split("/")[0].split("／")[0].strip())
                except Exception:
                    continue
                seq = out.setdefault(label, [])
                if not seq:
                    base = baseline.get(label)
                    if base is not None:
                        seq.append(base)
                seq.append(val)
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


def _registry_dir(registry: dict[str, str], label: str) -> str | None:
    """从设定账本派生的字段注册表查方向：优先精确核心词，其次子串匹配大类关键词。
    registry 为 None/空时返回 None（调用方退回固定规则）。"""
    if not registry:
        return None
    if label in registry:
        return registry[label]
    for k, v in registry.items():
        if label in k or k in label:
            return v
    return None


def panel_direction_violations(text: str, baseline: dict[str, int],
                               registry: dict[str, str] | None = None) -> list[str]:
    """面板数字方向校验：正文中机制数字序列违反方向规则 → 返回违规描述列表。

    - 方向规则来源：registry = mechanism_field_registry(baseline) 从设定账本派生
      （字段名语义通用判定，支持加点/升级等非死亡型机制方向，不写死"次数递减/
      累积递增"）；未传 registry（或空）时退回固定 _PANEL_DIR_RULES（兼容旧调用）。
    - decrease：序列中后值 > 前值 = 回升违规；但前值 == 0 且后值更大视为"重置"放行；
    - increase：序列中后值 < 前值 = 回退违规（只增不减，严格）；
    - 无基线或标签未识别/未注册 → 不校验（避免误伤）。
    """
    if not text or not baseline:
        return []
    dirs = registry if registry else dict(_PANEL_DIR_RULES)
    out: list[str] = []
    for label, seq in panel_numbers_sequence(text, baseline, registry).items():
        rule = dirs.get(label)
        if rule is None and registry:
            rule = _registry_dir(registry, label)
        if not rule:
            continue
        if len(seq) < 2:
            continue
        for a, b in zip(seq, seq[1:]):
            if rule == "decrease" and b > a and a != 0:
                out.append(f"面板「{label}」应从基线 {seq[0]} 递减，正文出现回升 {a}→{b}")
            elif rule == "increase" and b < a:
                out.append(f"面板「{label}」应只增不减（基线 {seq[0]}），正文出现回退 {a}→{b}")
    return out


def panel_baseline_dict(numbers: list[str]) -> dict[str, int]:
    """把累计 numbers（["可死次数：2", "累计进度：98%"]）归一化为基线 dict（每标签取最后值）。

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


# 2026-10-17 移除死亡信号词表与因果校验（REQ-机制数字软提示与通用化）：不再做语义
# 死亡判断，数字结算改由设定账本 + 被动方向校验 + 事后质检软提示处理。



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


# 2026-10-17 移除蚀化跨章累计校验（REQ-机制数字软提示与通用化）：蚀化随结算推进的
# 精确值改由设定账本链式承接 + 被动方向校验 + 事后质检软提示处理，不再做跨章强校验。


# ---------- P0-15/16 通用版：机制字段注册表 + 链式账本 + 事件驱动双向（2026-10-10） ----------
# 用户约束（v9 评估纠正）：蚀痕寸数/次数 0 等是《万墟之上》单本书机制，不能做成
# 本书特判；应"以机制基线为唯一事实源"做通用机制：
#   ① 字段注册表（closed-world）：基线 panel_format/reset_rule 声明了哪些面板字段，
#      正文面板就只能出现哪些字段；未声明字段（如「累计死亡次数」）出现 = 违规
#      （v9 ch3/ch6 面板幻觉行「累计死亡次数：39/37」即此类）——任何书通用；
#   ② 链式账本：所有注册字段每章末值入账，本章未提及继承上一章末值（治 ch5
#      正文无面板行 → erosion=null 断链）；
#   ③ 事件驱动双向：无死亡事件时 decrease 字段必须与上章末一致（治 ch2 次数 3→2
#      无依据递减）、increase 字段不得回退（治 ch4 四十一寸→ch5 三十七寸矛盾）；
#      发生死亡时 decrease 字段必须递减（次数 0 后仍死亡 = 边界违规）、increase
#      字段必须 ≥ 上章末 + 死亡次数（蚀化/蚀痕随死亡递增）。
# 字段方向由"字段名语义"通用判定（含 次数/剩余/余量 → decrease；进度/深度/累积与
# 单字长度/层级单位 → increase），不点名任何书。
_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100}


def _cn_to_int(s: str) -> int:
    """中文数字转 int（三十七→37；仅支持常见量词场景，失败返回 0）。"""
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in ("十", "百"):
            cur = (cur * (10 if ch == "十" else 100)) or (10 if ch == "十" else 100)
            total += cur
            cur = 0
        else:
            cur = _CN_DIGIT.get(ch, 0)
    return total + cur


#: 字段名停用限定词（通用归一：「今日可死次数」→「可死次数」；「累计死亡次数」→「死亡次数」）
_FIELD_STOP_RE = re.compile(r"(?:今日|距离|剩余|当前|累计|总共|总|已|可用)")


def _field_core(label: str) -> str:
    """字段核心词：去掉面板常见限定词（今日/距离/累计…），保留机制主体词。"""
    return _FIELD_STOP_RE.sub("", label or "").strip()


#: 字段方向关键词（通用语义：机制数字只许按声明方向变化，无事件不得变化）。
#: 次数/剩余/余量 → decrease；进度/深度/累积 → increase；长度/层级类单位（寸/尺/丈/
#: 米/步/级/层/格/段/点/块）→ increase（累积型状态随机制事件推进，任何书通用）。
#: 单书机制词（如《万墟之上》的「蚀化/蚀痕」）不预置，其字段由 baseline 声明，
#: 方向由注册表按字段名语义派生（含前缀剥离后的主体词命中以上通用关键词）。
_FIELD_DIR_KW = (
    ("次数", "decrease"), ("剩余", "decrease"), ("余量", "decrease"),
    ("进度", "increase"), ("深度", "increase"), ("累积", "increase"),
    ("寸", "increase"), ("尺", "increase"), ("丈", "increase"),
    ("米", "increase"), ("步", "increase"), ("级", "increase"),
    ("层", "increase"), ("格", "increase"), ("段", "increase"),
    ("点", "increase"), ("块", "increase"),
)


def _direction_hint(src: str, core: str) -> str | None:
    """字段名无通用语义关键词时，从基线声明文本的方向描述推导方向。

    - increase 提示：累计/只增/递增/不重置/永久；decrease 提示：重置/递减/恢复
      （「不重置」出现时视为负向，不判 decrease）；
    - 冲突（两种提示都出现）时「累计/只增」优先——字段名无通用语义的多为累积型
      机制（如「距离蚀化永久累计不重置」）；
    - 无任何提示 → None（方向未知不校验，避免误判）。
    """
    inc = dec = False
    for m in re.finditer(re.escape(core), src):
        ctx = src[max(0, m.start() - 4):m.end() + 20]
        if any(h in ctx for h in ("累计", "只增", "递增", "不重置", "永久")):
            inc = True
        if any(h in ctx for h in ("重置", "递减", "恢复")) and "不重置" not in ctx:
            dec = True
    if inc:
        return "increase"
    if dec:
        return "decrease"
    return None


def mechanism_field_registry(baseline: dict | None) -> dict[str, str]:
    """机制字段注册表（通用，closed-world）：从机制基线派生「正文面板允许出现的字段」。

    - 解析 baseline 各键值中「字段名：数字」模式（panel_format/reset_rule 声明）→
      字段核心词 + 方向：优先通用语义关键词（次数/剩余/进度/累积…），未命中时从
      基线声明文本的方向描述推导（累计/只增 → increase；重置/递减 → decrease），
      代码不预置单书机制词；
    - 累积型单位字段：基线文本含「数字+单字中文单位」（如「已伸三十七寸」→ 单位
      「寸」）→ 注册该单位为 increase（累积状态随机制事件推进，任何书通用）。
    - 任何书都有 mechanism_baseline（键值对），正文面板只许出现注册表内字段；
      返回 {核心词: 方向}。无基线 → 空注册表（未知字段检测不启用）。
    """
    reg: dict[str, str] = {}
    if not baseline:
        return reg
    src = " ".join(str(v) for v in baseline.values() if str(v))
    for m in re.finditer(r"([\u4e00-\u9fff]{1,12}?)\s*[：:]\s*[0-9]", src):
        core = _field_core(m.group(1))
        if not core:
            continue
        rule = None
        for kw, _r in _FIELD_DIR_KW:
            if kw in core:
                rule = _r
                break
        if rule is None:
            rule = _direction_hint(src, core)
        if rule:
            reg[core] = rule
    # 累积型单位字段：基线声明文本「数字+单字中文单位」（如「已伸三十七寸」）→
    # 注册该单位为 increase。排除常用量词/时间词，避免把「次数：3」「为期 3 天」误注册。
    _UNIT_BLOCK = ("次", "个", "人", "只", "条", "日", "年", "月", "天", "名", "件", "枚")
    for v in baseline.values():
        m = re.search(r"([0-9]+|[一二三四五六七八九十百两]+)\s*([\u4e00-\u9fff])", str(v or ""))
        if m and m.group(2) not in _UNIT_BLOCK:
            reg[m.group(2)] = "increase"
    return reg


def unknown_mechanism_fields(text: str, registry: dict[str, str]) -> list[str]:
    """正文面板行出现注册表外的机制字段 → 违规（通用 closed-world）。

    基线没声明的机制数字字段不许出现（如「累计死亡次数：39/37」类幻觉行）。
    只扫描「机制词结尾 + 冒号 + 数字」的面板行（_PANEL_RE 通用后缀匹配，书级字段
    名由调用方传入 registry、属白名单内不误报），普通叙述数字不受影响。单书机制
    词未声明时不识别（漏检可接受，书词面板行仅该书写法出现）。
    """
    if not registry or not text:
        return []
    out: list[str] = []
    _MECH_TAIL = ("次数", "剩余", "进度", "死亡", "存活", "生命",
                  "寸", "尺", "丈", "米", "步", "级", "层", "格", "段", "点", "块")
    for m in _PANEL_RE.finditer(text):
        label = m.group(1).strip()
        core = _field_core(label)
        if not core or core in registry:
            continue
        if any(kw in core for kw in _MECH_TAIL):
            out.append(
                f"正文出现未声明机制字段「{label}」（机制字段白名单：{sorted(registry)}；"
                "基线未声明的面板字段禁止出现——删除该行，或改用基线声明的字段写法）"
            )
    return out


def ledger_extract_numbers(text: str, registry: dict[str, str]) -> dict[str, int]:
    """抽取正文中每个注册字段的末值（链式账本入账用）。

    - 面板字段：按注册字段名逐个匹配正文（书级字段由设定账本声明，数据驱动），
      取最后一个值（末值）；前缀（今日/距离/累计…）可空，交给 {0,12}? 吞掉；
    - 累积型单位字段（registry 中单字 increase 字段，如「寸/层」）：正文「累计
      前缀 + N单位」数字（如「已延伸三十七寸」），取最大值；增量叙事（「爬了一
      寸」）与局部描述（「三寸长的裂口」）因无累计前缀不入账（继承上一章末），
      累计格式仍可查回退；
    - 未出现的字段不入账（由入账层继承上一章末值）。
    """
    out: dict[str, int] = {}
    if not text or not registry:
        return out
    for label in registry:
        _re = re.compile(
            r"[\u4e00-\u9fff]{0,12}?" + re.escape(label)
            + r"\s*[：:]\s*([0-9]+(?:\s*[／/]\s*[0-9]+)?)"
        )
        for m in _re.finditer(text):
            try:
                val = int(str(m.group(1)).split("/")[0].split("／")[0].strip())
            except Exception:
                continue
            out[label] = val   # 顺序覆盖 → 末值
    # 累积型单位字段（单字 increase）：正文「累计前缀 + N单位」提取（v12 收紧，
    # 排除增量叙事/局部描述），取其中最大值。
    for label, rule in registry.items():
        if rule != "increase" or len(label) != 1:
            continue
        unit_vals: list[int] = []
        for m in re.finditer(
            r"(?:已延伸|延伸至|延伸|累计|累积|达到|升至)\s*"
            r"([0-9]+|[一二三四五六七八九十百两]+)\s*" + re.escape(label),
            text,
        ):
            try:
                unit_vals.append(_cn_to_int(m.group(1)))
            except Exception:
                continue
        if unit_vals:
            out[label] = max(unit_vals)
    return out


def _registry_initial(baseline: dict | None, registry: dict[str, str]) -> dict[str, int]:
    """注册字段的初始基线值（prev 缺失时用）：panel_format/reset_rule 中的数字
    + 累积型单位字段（基线文本「数字+单位」，如「已伸三十七寸」→ 寸=37）。"""
    init: dict[str, int] = {}
    if not baseline or not registry:
        return init
    for k in ("panel_format", "reset_rule", "面板格式"):
        v = str(baseline.get(k) or "")
        for m in re.finditer(r"([\u4e00-\u9fff]{1,12}?)\s*[：:]\s*([0-9]+)", v):
            core = _field_core(m.group(1))
            if core in registry:
                try:
                    init[core] = int(m.group(2))
                except Exception:
                    pass
    for label, rule in registry.items():
        if rule != "increase" or len(label) != 1:
            continue
        for v in baseline.values():
            m = re.search(r"([0-9]+|[一二三四五六七八九十百两]+)\s*" + re.escape(label), str(v or ""))
            if m:
                try:
                    init[label] = _cn_to_int(m.group(1))
                except Exception:
                    pass
                break
    return init


def ledger_check(content: str, prev_ledger: dict, registry: dict[str, str],
                 deaths: int, initial: dict | None = None) -> list[str]:
    """链式账本校验（质检 2.8c 通用版）：注册字段跨章一致性 + 事件驱动双向。

    - 无事件（deaths=0）：decrease/中性字段必须与上一章末一致（无事件不得变化——
      治次数 3→2 无依据递减）；increase 字段不得回退（治累计长度 41→37 回退矛盾；
      允许合法正向增长如碎片 +1，不误伤）；
    - 有事件（deaths>0）：decrease 字段必须递减（上一章末已 0 仍死亡 = 边界违规，
      基线未声明该行为）；increase 字段必须 ≥ 上一章末 + deaths（累积型字段随事件
      递增；单字单位字段除外，增量依赖机制基线自由文本无法通用推导）；
    - 只校验正文出现的字段；prev 缺失用基线初始值（initial）；再缺失跳过。
    """
    if not registry or not content:
        return []
    cur = ledger_extract_numbers(content, registry)
    if not cur:
        return []
    out: list[str] = []
    for core, rule in registry.items():
        if core not in cur:
            continue
        val = cur[core]
        prev = prev_ledger.get(core)
        if prev is None:
            prev = (initial or {}).get(core)
        if prev is None:
            continue
        if deaths <= 0:
            msg = _check_no_event_consistency(core, rule, val, prev)
        else:
            msg = _check_event_direction(core, rule, val, prev, deaths)
        if msg:
            out.append(msg)
    return out


def _check_no_event_consistency(core: str, rule: str, val: int, prev: int) -> str | None:
    """无事件（deaths=0）：decrease/中性字段保持上一章末；increase 字段不得回退。

    单位字段（寸/里/丈…）允许合法正向增长（外力/碎片），只查不回退。
    """
    if rule == "increase" and "寸" in core:
        if val < prev:
            return (f"机制字段「{core}」本章无结算事件但值 {val} 较上一章末 {prev} "
                    "回退（未发生事件时累计类机制数字不得回退）")
        return None
    if val != prev:
        return (f"机制字段「{core}」本章无结算事件但值 {val} 与上一章末 {prev} 不一致"
                "（未发生事件时机制数字必须保持上一章末值不变）")
    return None


def _check_event_direction(core: str, rule: str, val: int, prev: int, deaths: int) -> str | None:
    """有事件（deaths>0）：decrease 必须递减；increase（多字符字段）必须 ≥ prev+deaths。

    单字单位字段（寸/层/级…）有事件时不校验增量（增量依赖机制基线自由文本，
    无法通用推导「应 ≥ prev+deaths」；无事件回退已在上方拦截）。
    """
    if rule == "decrease":
        if val > prev:
            return (f"机制字段「{core}」本章发生结算事件但值 {val} 未从上一章末 {prev} 递减"
                    "（应随事件递减）")
        if prev == 0 and val == 0:
            return (f"机制字段「{core}」上一章末已为 0，本章仍触发结算事件（次数用尽后"
                    "不得再安排同类事件，基线未声明该边界行为）")
    elif rule == "increase" and len(core) > 1 and val < prev + max(0, deaths):
        return (f"机制字段「{core}」本章发生 {deaths} 次结算事件，值 {val} 应 ≥ 上一章末"
                f" {prev} + {deaths}（随事件累计递增）")
    return None


# ---------- REQ-通用机制类型适配：规则约束型条款作用域过滤 ----------

def active_rules_for_instance(ruleset: list, instance: str = "") -> list:
    """筛出当前副本/场景生效的条款：global + instance:<同名>，排除已解除/已打破。

    - instance 为空：只返回 global 条款；
    - `character:` / `ability:` 作用域条款不随副本切换，默认一并纳入全局上下文；
    - status != active 的条款不参与一致性判定（已解除/已打破）。
    """
    instance = (instance or "").strip()
    out: list = []
    for rule in ruleset or []:
        status = getattr(rule, "status", "active")
        if status != "active":
            continue
        scope = getattr(rule, "scope", "global") or "global"
        if scope == "global" or scope.startswith(("character:", "ability:")):
            out.append(rule)
        elif instance and scope == f"instance:{instance}":
            out.append(rule)
    return out
