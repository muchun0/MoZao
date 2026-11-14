# -*- coding: utf-8 -*-
"""五类确定性冲突规则（REQ-R21 US-4 / TECH-R21 §2.5）。

设计原则：
- **纯函数**：不读网络、不调 LLM，输入结构、输出 violations，可在单测里穷举。
- **宁可漏报不误报**：两侧都必须有显式记录才判定；数据缺失一律跳过。
- **只报不修**：结论供写前提醒与接口展示，修复仍走作者判断与既有重写链路。

输出：`[{rule, ch, expected, actual, evidence_ch, severity}]`，按 severity 排序。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

logger = logging.getLogger(__name__)

#: 规则名（写进结论，便于作者与质检对照）
RULES = ("mechanism_delta", "death_state", "item_owner", "address_drift", "time_reverse")

#: 严重度排序权重（数字越大越靠前）
SEVERITY_WEIGHT = {"high": 3, "medium": 2, "low": 1}

#: 累计型数字：只能增；消耗型数字：只能减。未命中语义的 key 不做方向判定。
_UP_HINTS = ("累计", "进度", "积分", "经验", "总数", "里程")
_DOWN_HINTS = ("剩余", "可死", "余额", "库存", "次数", "存量")

#: 死亡表述（人物状态值里出现即视为已死）
_DEAD_RE = re.compile(r"已死|身死|身亡|死亡|殒命|毙命|断气")

#: 时间锚点里的「第 N 天」
_DAY_RE = re.compile(r"第\s*([0-9]+)\s*天")

_NUM_RE = re.compile(r"\s*[：:]\s*([0-9]+)(?:\s*[／/]\s*[0-9]+)?\s*$")


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _violation(rule: str, ch: int, expected: str, actual: str,
               evidence_ch: int, severity: str = "high") -> dict:
    return {"rule": rule, "ch": int(ch), "expected": expected, "actual": actual,
            "evidence_ch": int(evidence_ch), "severity": severity}


def parse_numbers(raw, registry: dict | None = None) -> dict:
    """把 `["可死次数：3", "累计进度：7/10"]` 解析成 `{"可死次数": 3, "累计进度": 7}`。

    registry 非空时把字段名归一到注册表字段（跨章同键才可比）：逐章 LLM 抽取会把
    同一机制写成「剩余次数 / 可死次数」等变体，不归一则 `check_mechanism_delta`
    的相邻章比对恒为空。
    """
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            n = _int(v, -1)
            if n >= 0:
                out[str(k).strip()] = n
    else:
        for line in (raw or []):
            s = str(line or "").strip()
            m = _NUM_RE.search(s)
            if not m:
                continue
            key = s[:m.start()].strip(" ：:")
            if key:
                out[key] = _int(m.group(1), 0)
    if registry and out:
        from ..chapter_state import canonical_field_name
        canon: dict[str, int] = {}
        for key, val in out.items():
            canon[canonical_field_name(key, registry)] = val
        return canon
    return out


def direction_of(key: str) -> str:
    """按语义判定数字方向：`up` / `down` / `""`（不判）。"""
    if any(h in key for h in _DOWN_HINTS):
        return "down"
    if any(h in key for h in _UP_HINTS):
        return "up"
    return ""


# ---------- 规则 1：机制数字方向 ----------
def check_mechanism_delta(chapters: Iterable[dict], min_delta: int = 1) -> list:
    """相邻两章同一机制数字方向相反且差值绝对值 > min_delta 才报。"""
    out = []
    prev = None
    for ch in sorted(chapters or [], key=lambda c: _int(c.get("no"))):
        nums = ch.get("numbers") or {}
        if prev is not None:
            for key, val in nums.items():
                if key not in (prev.get("numbers") or {}):
                    continue
                old = _int((prev.get("numbers") or {}).get(key), 0)
                delta = _int(val, 0) - old
                direction = direction_of(key)
                if not direction or abs(delta) <= min_delta:
                    continue
                if direction == "up" and delta < 0:
                    out.append(_violation(
                        "mechanism_delta", _int(ch.get("no")), f"{key} 只能增",
                        f"{old}→{_int(val)}（第{prev.get('no')}章到第{ch.get('no')}章）",
                        _int(prev.get("no"))))
                elif direction == "down" and delta > 0:
                    out.append(_violation(
                        "mechanism_delta", _int(ch.get("no")), f"{key} 只能减",
                        f"{old}→{_int(val)}（第{prev.get('no')}章到第{ch.get('no')}章）",
                        _int(prev.get("no"))))
        prev = ch
    return out


# ---------- 规则 2：死亡后仍有主动行为 ----------
def check_death_state(chapters: Iterable[dict], events: Iterable[dict]) -> list:
    """某人物在章 N 已被判定死亡，却在更晚章的事件里作为参与者主动出场。

    只用事件参与人做判定（显式动作），复活机制由本书 canon 声明后由调用方跳过。
    """
    death: dict[str, int] = {}
    for ch in sorted(chapters or [], key=lambda c: _int(c.get("no"))):
        chars = ch.get("characters") or {}
        if not isinstance(chars, dict):
            continue
        for name, state in chars.items():
            if _DEAD_RE.search(str(state or "")):
                death.setdefault(str(name).strip(), _int(ch.get("no")))
    out = []
    for ev in (events or []):
        ch = _int(ev.get("ch"))
        for name in (ev.get("participants") or []):
            name = str(name or "").strip()
            d = death.get(name)
            if d and ch > d:
                out.append(_violation(
                    "death_state", ch, f"{name} 已于第{d}章死亡，不得有主动行为",
                    f"第{ch}章事件：{ev.get('desc') or ''}", d))
    return out


# ---------- 规则 3：物品归属 ----------
_OWNER_VERB_RE = re.compile(
    r"持有|拿着|握着|收进|收起|收着|扔给|递给|还给|交给|给了|塞给|抢走|夺下|夺走|"
    r"丢|落在|放在|放回|藏|取出|挂在|挂在|绑在|叼在|揣")
_OWNER_CLAUSE_RE = re.compile(r"[，,；;。／/]")
#: 明确的「持有」方位句式：在X手中 / 落在X怀里 / 被X收着 → 取 X 为主语
_HOLD_PHRASE_RE = re.compile(
    r"(?:在|落在|掉在|塞在|被)?([^\s，,；;。]{1,6}?)"
    r"(?:手中|怀里|怀|袖中|袖里|领口|身旁|身上|手边|手上|手里)")


def owner_subject(owner: str, names: Iterable[str] = ()) -> str:
    """把归属描述归一到「持有者主体」。

    `「苏妄言扔给陆沉，陆沉未开封，收进抽屉」→ 陆沉`、
    `「在陆沉怀中/领口」→ 陆沉`、`「砧板上」→ ""`。
    做法：①描述里最后出现的已知人物名（末位持有者）；②无已知名时按「持有」句式
    （在X手中/落在X怀里）取 X；③再退化为「第一个分句里转移动词前的主体」；
    都取不到返回空串（该条不参与比对，宁漏不误报）。
    """
    text = str(owner or "").strip()
    if not text:
        return ""
    known = [str(n).strip() for n in (names or []) if str(n).strip()]
    hits = [(text.rfind(n), n) for n in known if n in text]
    if hits:
        return max(hits)[1]
    first = _OWNER_CLAUSE_RE.split(text)[0].strip()
    m = _HOLD_PHRASE_RE.search(first)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = _OWNER_VERB_RE.search(first)
    if not m:
        # 没有持有/转移动词：整段就是一个主体名（「独眼刘」「陆沉」）→ 视为持有者；
        # 带方位后缀的纯位置（「砧板上」「灶台边」）不是持有者。
        if first and len(first) <= 6 and not re.search(r"(?:上|中|里|下|旁|边|内|外|前|后)$", first):
            return first
        return ""
    head = re.sub(r"^(?:在|被|由|归|到)", "", first[:m.start()]).strip(" 的")
    if not head or len(head) > 8:
        return ""
    return head


def check_item_owner(chapters: Iterable[dict], events: Iterable[dict]) -> list:
    """同一物品归属变化，且两次出现之间没有 item 类转移事件。

    比对对象是**持有者主体**而非整段描述：真实样本里「在陆沉怀中」对
    「在陆沉怀中/领口」曾被判成归属变化，属描述性差异误报。
    """
    out = []
    known_names = sorted({
        str(n).strip()
        for ch in (chapters or [])
        for n in (ch.get("characters") or {})
        if str(n).strip()
    })
    prev: dict[str, tuple] = {}
    item_events: list[int] = [
        _int(ev.get("ch")) for ev in (events or [])
        if str(ev.get("kind") or "") == "object"
    ]
    for ch in sorted(chapters or [], key=lambda c: _int(c.get("no"))):
        no = _int(ch.get("no"))
        items = ch.get("items") or {}
        if not isinstance(items, dict):
            continue
        for item, owner in items.items():
            item = str(item or "").strip()
            owner_raw = str(owner or "").strip()
            if not item:
                continue
            owner_now = owner_subject(owner_raw, known_names)
            if not owner_now:
                continue
            if item in prev:
                prev_ch, prev_owner, prev_raw = prev[item]
                if prev_owner != owner_now:
                    moved = any(prev_ch < ec <= no for ec in item_events)
                    if not moved:
                        out.append(_violation(
                            "item_owner", no, f"「{item}」仍应在{prev_owner}手中（或无转移事件）",
                            f"第{no}章记为{owner_now}（{owner_raw}；上次第{prev_ch}章为"
                            f"{prev_owner}（{prev_raw}））", prev_ch))
            prev[item] = (no, owner_now, owner_raw)
    return out


# ---------- 规则 4：称谓漂移 ----------
def check_address_drift(chapters: Iterable[dict], canon_terms: dict) -> list:
    """章内状态文本使用了设定账本登记的非标准写法（term 与 canonical 不同）。"""
    out = []
    registry = {}
    for term, meta in (canon_terms or {}).items():
        term = str(term or "").strip()
        canonical = str((meta or {}).get("canonical") or "").strip() if isinstance(meta, dict) else ""
        if term and canonical and canonical != term:
            registry[term] = canonical
    if not registry:
        return out
    for ch in chapters or []:
        blob = " ".join([
            str(ch.get("state_text") or ""),
            " ".join(str(x) for x in (ch.get("facts") or [])),
            " ".join(str(x) for x in (ch.get("body") or [])),
            " ".join(str(v) for v in (ch.get("characters") or {}).values()),
        ])
        for term, canonical in registry.items():
            if term in blob:
                out.append(_violation(
                    "address_drift", _int(ch.get("no")), f"统一写作「{canonical}」",
                    f"出现非标准写法「{term}」", _int(ch.get("no")), severity="medium"))
    return out


# ---------- 规则 5：时间倒流 ----------
def check_time_reverse(chapters: Iterable[dict]) -> list:
    """两章都有显式「第 N 天」且后一章早于前一章。"""
    out = []
    prev = None
    for ch in sorted(chapters or [], key=lambda c: _int(c.get("no"))):
        m = _DAY_RE.search(str(ch.get("time") or ""))
        if not m:
            continue
        day = _int(m.group(1))
        if prev is not None and day < prev[1]:
            out.append(_violation(
                "time_reverse", _int(ch.get("no")), f"时间应晚于第{prev[1]}天",
                f"第{day}天（第{prev[0]}章结尾为第{prev[1]}天）", prev[0]))
        prev = (_int(ch.get("no")), day)
    return out


# ---------- 汇总 ----------
def detect(chapters: Iterable[dict], events: Iterable[dict] = None,
           canon_terms: dict = None) -> list:
    """跑全五条规则并排序；空数据返回空列表（不产生任何文本）。"""
    chapters = list(chapters or [])
    events = list(events or [])
    violations: list[dict] = []
    for fn in (
        lambda: check_mechanism_delta(chapters),
        lambda: check_death_state(chapters, events),
        lambda: check_item_owner(chapters, events),
        lambda: check_address_drift(chapters, canon_terms or {}),
        lambda: check_time_reverse(chapters),
    ):
        try:
            violations.extend(fn())
        except Exception:
            logger.exception("确定性冲突规则执行失败（跳过该规则，不影响其余规则）")
    violations.sort(key=lambda v: (-SEVERITY_WEIGHT.get(str(v.get("severity")), 0),
                                   _int(v.get("ch")), str(v.get("rule"))))
    return violations


def chapters_from_scenes(scenes: dict, registry: dict | None = None) -> list:
    """把 `scenes.json` 的 {章号: {scenes, state}} 摊平成规则输入。

    registry 非空时把面板数字字段名归一到机制注册表（兼容归一前落盘的历史样本）。
    """
    out = []
    for no, rec in (scenes or {}).items():
        try:
            no = int(no)
        except (TypeError, ValueError):
            continue
        state = (rec or {}).get("state") if isinstance(rec, dict) else {}
        if not isinstance(state, dict):
            continue
        out.append({
            "no": no,
            "numbers": parse_numbers(state.get("numbers"), registry),
            "characters": state.get("characters") or {},
            "items": state.get("items") or {},
            "time": str(state.get("time") or ""),
            "facts": state.get("facts") or [],
            "body": state.get("body") or [],
        })
    return out


def render_block(violations: list, limit: int = 5) -> str:
    """注入块：只取前 limit 条，总长压到 200 字以内；无命中返回空串。"""
    rows = [v for v in (violations or []) if v][:max(1, int(limit or 5))]
    if not rows:
        return ""
    label = {
        "mechanism_delta": "机制数字",
        "death_state": "人物生死",
        "item_owner": "物品归属",
        "address_drift": "称谓",
        "time_reverse": "时间",
    }
    lines = []
    for v in rows:
        line = (f"- [{label.get(v.get('rule'), v.get('rule'))}·第{v.get('ch')}章] "
                f"应为：{v.get('expected')}；本章：{v.get('actual')}")
        if len("\n".join(lines + [line])) > 190:
            break
        lines.append(line)
    if not lines:
        return ""
    return "【冲突提醒·写前必看（确定性规则）】\n" + "\n".join(lines)
