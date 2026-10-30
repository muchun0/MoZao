# -*- coding: utf-8 -*-
"""场景事件规划与文本工具函数（从 stages.py 抽出，§11.1 文件拆分）。

本模块承载章节生成中的确定性工具逻辑：
- 场景事件清单切分/合并/互斥校验（REQ-scene-plan-align）
- 场景正文事件复写检测（零 LLM）
- 拆场景结构校验（场景数/字数预算/事件互斥/核心事件覆盖）
- 摘要/HTML/JSON 文本处理工具

所有函数纯函数、无 LLM 调用、无实例状态，可独立测试。
"""
from __future__ import annotations

import logging
import re

from ..llm.json_parse import parse_json_obj
from ..utils.text import longest_common_substr_len as _lcs_len
from .templates import scene_event_derive_prompt, scene_rewrite_prompt

logger = logging.getLogger(__name__)


#: 场景事件互斥校验（P1-1）：event 文本相似度阈值（归一化后 55% 相同即判重叠）
_SCENE_EVENT_OVERLAP_RATIO = 0.55
#: 事件次数标记正则：「第1/2/3…次」或「第一次/第二次…」，两事件次数不同视为不同事件
_SCENE_ORDINAL_PATTERN = r"第\s*[一二三1-3０-９\d]+\s*次"
#: 事件动作词归一化表
_SCENE_ACTION_NORM = {
    "救": "救援", "救援": "救援", "营救": "救援", "搭救": "救援", "救出": "救援",
    "围": "围攻", "围攻": "围攻", "围住": "围攻", "围杀": "围攻", "包围": "围攻",
    "杀": "击杀", "击杀": "击杀", "杀死": "击杀", "斩杀": "击杀", "猎杀": "击杀",
    "逃": "逃离", "逃离": "逃离", "逃跑": "逃离", "撤": "撤离", "撤离": "撤离", "撤退": "撤离",
    "追": "追击", "追击": "追击", "追逐": "追击", "追捕": "追击",
    "挡": "抵挡", "抵挡": "抵挡", "防守": "抵挡", "阻挡": "抵挡",
    "偷袭": "偷袭", "刺杀": "刺杀", "对峙": "对峙", "埋伏": "埋伏", "侦查": "侦查", "探索": "探索",
}
#: 救援专属动词
_SCENE_RESCUE_VERBS = ("救", "引开", "砸", "挡", "扑", "迎")
#: 拆场景结构校验：场景数上限（2-4 个）
_SCENE_COUNT_MIN, _SCENE_COUNT_MAX = 2, 4
#: target_words 之和允许偏离 chapter_words 的幅度（±10%）
_SCENE_WORDS_TOLERANCE = 0.10
#: 单场景目标字数基准
_SCENE_TARGET_WORDS = 1200
#: 单场景 target_words 下限
_SCENE_MIN_WORDS = 800
_SCENE_MIN_WORDS_RATIO = 0.25
#: 相邻场景输出相似度阈值
_SCENE_ADJACENT_REPEAT_RATIO = 0.55
#: 长段重叠率阈值
_SCENE_ADJACENT_SPAN_RATIO = 0.20
#: 长段重叠判定：句长下限
_SCENE_ADJACENT_MIN_SENT = 15
#: 因果粘连拍标记
_CAUSAL_BEAT_RE = re.compile(r"复活|苏醒|醒来|回神|缓过神|回过神来|睁眼|醒转")
#: 规范动作白名单：词表归一表的值即权威集合，LLM derive 的 action 须落在此集合内
_SCENE_ACTION_SET = set(_SCENE_ACTION_NORM.values())


def _format_action_plan(chars: list) -> str:
    """把拆场景输出中的 characters_action 格式化为注入文本（空则返回空串）。"""
    if not chars:
        return ""
    lines = []
    for c in chars:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        actions = c.get("key_actions") or []
        if isinstance(actions, str):
            actions = [actions]
        lines.append(
            f"- {c['name']}：目标『{c.get('goal', '')}』；关键行动：{'、'.join(str(a) for a in actions)}；"
            f"情绪：{c.get('emotion', '')}；对话意图：{c.get('dialogue_intent', '')}"
        )
    return "\n".join(lines)


def _world_conflict_names(world) -> tuple[str, ...]:
    """从 World 人物卡 + 敌人图鉴提取冲突对象候选名单（通用化 2026-10-27）。"""
    names: list[str] = []
    try:
        for c in (getattr(world, "characters", None) or []):
            n = str(getattr(c, "name", "") or "").strip()
            if n:
                names.append(n)
        for e in (getattr(world, "enemies", None) or []):
            n = str(getattr(e, "name", "") or "").strip()
            if n:
                names.append(n)
    except Exception:
        pass
    return tuple(names)


def _event_conflict(text: str, names: tuple[str, ...] = ()) -> str:
    """提取事件槽位核心对象：优先被救/受害人物，无人物才取敌人。"""
    src = text or ""
    for name in names:
        if name and name in src:
            return name
    m = re.search(r"(?:三头?|三只?|几只?|一头?|一只?|个)?(学员|弟子|少年|少女)", src)
    if m:
        return m.group(1)
    return ""


def _scene_text_rewrites_event(text: str, done_event_descs: list[str],
                               names: tuple[str, ...] = (),
                               llm=None, use_llm: bool = True) -> str:
    """场景正文事件级复写检测。

    优先走 LLM 语义判定（`scene_rewrite_prompt`，识别换措辞复写）；无 llm /
    use_llm=False / 调用异常 / 无已发生事件时，回退确定性词表逻辑
    （`_event_conflict` + `_SCENE_RESCUE_VERBS`）。命中则返回复写的事件原文，否则 ""。
    """
    if not done_event_descs:
        return ""
    if llm is not None and use_llm:
        try:
            system, user = scene_rewrite_prompt(text or "", list(done_event_descs))
            raw = llm.chat(system, user, temperature=0.2, max_tokens=256)
            data = parse_json_obj(raw)
            if not isinstance(data, dict) or "rewrite_of" not in data:
                logger.warning("scene_rewrite LLM 输出非结构化，回退词表逻辑")
            else:
                idx = int(data["rewrite_of"])
                if 0 <= idx < len(done_event_descs):
                    return done_event_descs[idx]
                return ""  # LLM 明确判定不复写（如 -1），不回退词表
        except Exception as e:
            logger.warning("scene_rewrite LLM 判定失败，回退词表逻辑: %s", e)
    # 词表兜底（确定性）
    c = _event_conflict(text, names)
    if not c or not any(v in text for v in _SCENE_RESCUE_VERBS):
        return ""
    for ev in done_event_descs:
        ec = _event_conflict(ev, names)
        if not ec or not any(v in ev for v in _SCENE_RESCUE_VERBS):
            continue
        if c == ec or c in ec or ec in c:
            return ev
    return ""


def _long_sentence_overlap_ratio(prev: str, cur: str) -> float:
    """相邻场景"长段复刻率"（确定性、零 LLM）。"""
    norm = lambda s: "".join(ch for ch in s if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    prev_sents = {norm(s) for s in re.split(r"[。！？\n]", prev or "") if len(norm(s)) >= _SCENE_ADJACENT_MIN_SENT}
    cur_sents = [norm(s) for s in re.split(r"[。！？\n]", cur or "") if len(norm(s)) >= _SCENE_ADJACENT_MIN_SENT]
    if not cur_sents:
        return 0.0
    hit = sum(len(s) for s in cur_sents if s in prev_sents)
    total = sum(len(s) for s in cur_sents)
    return (hit / total) if total > 0 else 0.0


def _norm_scene_event(text: str) -> str:
    """归一化 event 文本：去标点空白、保留中文与字母数字。"""
    return "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _scene_event_ordinals(text: str) -> set[str]:
    """提取 event 文本中的事件次数标记。"""
    return set(re.findall(_SCENE_ORDINAL_PATTERN, text))


def _scene_count_for_words(chapter_words: int) -> int:
    """按章节字数反推目标场景数。"""
    if chapter_words and chapter_words >= 1200:
        return max(1, min(4, int(chapter_words / _SCENE_TARGET_WORDS + 0.5)))
    return 4


def _llm_event_derive(beats: list[str], names: tuple[str, ...], llm
                      ) -> tuple[list[tuple[str, str]], set[int]] | None:
    """一次 LLM 调用产出全部节拍的语义槽 + 因果合并决策。

    返回 (slots, merge_set)：slots 与 beats 一一对应 [(conflict, action)]，
    action 不在规范动作集合内置 ""；merge_set 为 0 基节拍下标集合（该拍并入前一拍）。
    beats 不足 2 拍、输出非 dict、events 缺失/非 list/长度不符等任一校验失败 → None（调用方走词表）。
    """
    if not beats or len(beats) < 2:
        return None
    system, user = scene_event_derive_prompt(list(beats), tuple(names))
    raw = llm.chat(system, user, temperature=0.2, max_tokens=1024)
    data = parse_json_obj(raw)
    events = data.get("events")
    if not isinstance(events, list) or len(events) != len(beats):
        return None
    slots: list[tuple[str, str]] = []
    for ev in events:
        if not isinstance(ev, dict):
            return None
        conflict = str(ev.get("conflict") or "").strip()
        action = str(ev.get("action") or "").strip()
        if action not in _SCENE_ACTION_SET:
            action = ""
        slots.append((conflict, action))
    merge_set: set[int] = set()
    for v in (data.get("merge") or []):
        try:
            i0 = int(v) - 1  # LLM 输出 1 基 → 内部 0 基
        except Exception:
            continue
        if 1 <= i0 < len(beats):
            merge_set.add(i0)
    return slots, merge_set


def _derive_events_from_moves(moves: str, chapter_words: int = 0,
                              names: tuple[str, ...] = (),
                              llm=None, use_llm: bool = True) -> list[dict]:
    """大纲 character_moves 切分事件清单；有 llm 用语义槽+因果合并，失败回退确定性词表。"""
    if not moves or not str(moves).strip():
        return []
    text = str(moves).strip()
    if "→" in text:
        beats = [b.strip() for b in text.split("→")]
    else:
        beats = [b.strip() for b in re.split(r"[。；;]", text) if b.strip()]
    beats = [b for b in beats if b]
    if len(beats) < 2:
        return []
    llm_slots: list[tuple[str, str]] | None = None
    merge_set: set[int] = set()
    if llm is not None and use_llm:
        try:
            derived = _llm_event_derive(beats, names, llm)
            if derived is not None:
                llm_slots, merge_set = derived
            else:
                logger.warning("scene_event LLM derive 输出校验失败，回退词表路径")
        except Exception as e:
            logger.warning("scene_event LLM derive 失败，回退词表路径: %s", e)
            llm_slots, merge_set = None, set()
    n_target = _scene_count_for_words(chapter_words)
    i = len(beats) - 1
    while i >= 1 and len(beats) > n_target:
        causal = (i in merge_set) if llm_slots is not None else bool(_CAUSAL_BEAT_RE.search(beats[i]))
        if causal:
            beats[i - 1] = f"{beats[i - 1]}→{beats[i]}"
            if llm_slots is not None:
                del llm_slots[i]  # 保留前一拍槽位，与 beats 同步收缩
            del beats[i]
            i -= 2
        else:
            i -= 1
    while len(beats) > n_target:
        j = min(range(1, len(beats)), key=lambda k: len(beats[k]))
        beats[j - 1] = f"{beats[j - 1]}→{beats[j]}"
        if llm_slots is not None:
            del llm_slots[j]
        del beats[j]
    events = []
    for idx, b in enumerate(beats, start=1):
        c, a = llm_slots[idx - 1] if llm_slots is not None else _event_semantic_slots(b, names=names)
        events.append({"id": f"事件{idx}", "desc": b, "conflict": c, "action": a, "final_state": ""})
    return events


def _event_semantic_slots(desc: str, conflict: str = "", action: str = "",
                          names: tuple[str, ...] = ()) -> tuple[str, str]:
    """提取事件的语义槽位（核心对象, 归一化动作）。"""
    conflict_v = ""
    action_v = (action or "").strip()
    if not action_v and desc:
        for w, norm in _SCENE_ACTION_NORM.items():
            if w in desc:
                action_v = norm
                break
    src = (desc or "").strip() or conflict
    conflict_v = _event_conflict(src, names)
    return conflict_v, action_v


def _scene_events_semantically_overlap(events: list[dict],
                                       names: tuple[str, ...] = ()) -> bool:
    """事件清单语义槽位互斥（零 LLM）。"""
    slots: list[tuple[str, str]] = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        desc = str(e.get("desc") or "").strip()
        c, a = _event_semantic_slots(desc, str(e.get("conflict") or ""),
                                      str(e.get("action") or ""), names)
        if not c and not a:
            continue
        slots.append((c, a))
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            ci, ai = slots[i]
            cj, aj = slots[j]
            if ai and aj and ai == aj and ci and cj and (ci == cj or ci in cj or cj in ci):
                return True
    return False


def _merge_duplicate_events(events_list: list, scenes: list,
                            pairs: list[tuple[int, int, str]]) -> tuple[list, list] | None:
    """拆场景评审合并（确定性，零额外 LLM）：按评审重复对合并事件。"""
    if not events_list or not pairs:
        return None
    drop_idx: set[int] = set()
    for i, j, _r in pairs:
        if 0 <= i < len(events_list) and 0 <= j < len(events_list) and i != j:
            drop_idx.add(j)
    if not drop_idx:
        return None
    new_events = [e for idx, e in enumerate(events_list) if idx not in drop_idx]
    drop_ids = {str(events_list[idx].get("id") or "") for idx in drop_idx}
    new_scenes: list = []
    for sc in scenes or []:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref in drop_ids:
            continue
        new_scenes.append(sc)
    if len(new_events) < 2 or not new_scenes:
        return None
    return new_events, new_scenes


def _expand_scene_event_refs(scenes: list, events_list: list) -> None:
    """把场景 scene_event_ref 引用展开为事件原文。"""
    by_id: dict[str, dict] = {}
    for e in events_list or []:
        if isinstance(e, dict):
            eid = str(e.get("id") or "").strip()
            if eid:
                by_id[eid] = e
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref and ref in by_id:
            e = by_id[ref]
            sc["event"] = str(e.get("desc") or "").strip()
            sc["scene_event_ref"] = ref
            for k in ("conflict", "action", "final_state"):
                if e.get(k):
                    sc[k] = e[k]


def _normalize_scene_targets(scenes: list, chapter_words: int = 0) -> list:
    """场景字数预算收敛（确定性、零 LLM）。"""
    if not scenes or chapter_words <= 0:
        return scenes
    total = sum(int(sc.get("target_words") or 0) for sc in scenes if isinstance(sc, dict))
    if total <= 0:
        base = chapter_words // len(scenes)
        rem = chapter_words - base * len(scenes)
        for i, sc in enumerate(scenes):
            if isinstance(sc, dict):
                sc["target_words"] = base + (rem if i == len(scenes) - 1 else 0)
        return scenes
    scaled = [int(round(chapter_words * int(sc.get("target_words") or 0) / total))
              for sc in scenes if isinstance(sc, dict)]
    diff = chapter_words - sum(scaled)
    if diff:
        scaled[-1] += diff
    for i, sc in enumerate(scenes):
        if isinstance(sc, dict):
            sc["target_words"] = scaled[i]
    return scenes


def _validate_scene_plan(scenes: list, chapter_words: int = 0,
                         core_event: str = "") -> tuple[bool, str]:
    """拆场景结构校验（零 LLM 成本）。"""
    if not scenes:
        return True, ""
    if len(scenes) > _SCENE_COUNT_MAX:
        return False, f"场景数 {len(scenes)} 超出上限 4（疑似把同一事件拆多遍凑场景数）"
    if len(scenes) >= 2 and chapter_words > 0:
        total = sum(int(sc.get("target_words") or 0) for sc in scenes if isinstance(sc, dict))
        if total > 0 and abs(total - chapter_words) > chapter_words * _SCENE_WORDS_TOLERANCE:
            return False, f"场景 target_words 之和 {total} 偏离目标 {chapter_words} 超 ±10%"
    if chapter_words > 0:
        min_w = max(_SCENE_MIN_WORDS, int(chapter_words * _SCENE_MIN_WORDS_RATIO))
        for sc in scenes:
            tw = int(sc.get("target_words") or 0)
            if 0 < tw < min_w:
                return False, (f"场景 target_words {tw} 低于下限 {min_w}"
                               "（拆得太碎，疑似注水，应合并场景）")
    if core_event:
        segs = [s.strip() for s in re.split(r"[→，,。；;]", core_event)
                if len(s.strip()) >= 4]
        if segs:
            ev_all = " ".join(
                str(sc.get("event") or "") for sc in scenes if isinstance(sc, dict)
            )
            hit = sum(1 for s in segs if _lcs_len(s, ev_all) >= 2)
            if hit * 10 < len(segs) * 6:
                miss = [s for s in segs if _lcs_len(s, ev_all) < 2]
                return False, (f"拆场景未覆盖大纲核心事件（命中 {hit}/{len(segs)} 段，"
                               f"未覆盖段：{' / '.join(miss[:3])}）")
    refs: list[str] = []
    texts: list[tuple[str, set[str]]] = []
    for sc in scenes:
        if not isinstance(sc, dict):
            continue
        ref = str(sc.get("scene_event_ref") or "").strip()
        if ref:
            refs.append(ref)
        ev = (sc.get("event") or "").strip()
        if ev:
            texts.append((_norm_scene_event(ev), _scene_event_ordinals(ev)))
    if refs:
        seen: set[str] = set()
        for r in refs:
            if r in seen:
                return False, f"事件编号 {r} 被多个场景引用（同一事件拆成两场景）"
            seen.add(r)
    import difflib
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, a_ord = texts[i]
            b, b_ord = texts[j]
            if not a or not b:
                continue
            if a_ord and b_ord and not (a_ord & b_ord):
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() > _SCENE_EVENT_OVERLAP_RATIO:
                return False, "场景事件高度相似（同一事件换措辞拆成两场景）"
    return True, ""


def _scene_events_overlap(scenes: list) -> bool:
    """兼容别名：仅事件互斥维度。"""
    ok, _ = _validate_scene_plan(scenes, chapter_words=0)
    return not ok


def _split_summary(text: str) -> tuple[str, str]:
    """按『【摘要】』标记拆分正文与摘要。"""
    marker = "【摘要】"
    idx = text.rfind(marker)
    if idx > 0:
        content = text[:idx].strip()
        summary = text[idx + len(marker):].strip().replace("\n", " ")
        return content, summary
    return text.strip(), ""


def _is_repetitive_text(text: str) -> bool:
    """整句复读型注水检测。"""
    if not text or len(text) < 200:
        return False
    grams = {text[i:i + 2] for i in range(max(0, len(text) - 1))}
    return len(grams) < len(text) * 0.15


def _summary_invented_words(summary: str, content: str) -> float:
    """摘要一致性：摘要 2-gram 与正文 2-gram 的命中率。"""
    if not summary or not content:
        return 1.0
    if len(summary) < 8:
        return 1.0
    grams = {summary[i:i + 2] for i in range(max(0, len(summary) - 1))}
    if len(grams) < 4 or len(summary) < 8:
        return 1.0
    body = {content[i:i + 2] for i in range(max(0, len(content) - 1))}
    if not body:
        return 1.0
    return sum(1 for g in grams if g in body) / len(grams)


def _strip_html_tags(text: str) -> str:
    """剥离模型偶尔夹带的 HTML 标签，返回纯文本。"""
    if not text:
        return ""
    t = re.sub(r"(?i)</?(?:p|br|div|section|h[1-6])\s*/?>", "\n", text)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _strip_json_block(text: str) -> str:
    """从模型输出中提取 JSON 对象（容忍 ```json 包裹、前后赘述、截断、尾逗号）。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    s = t.find("{")
    if s < 0:
        return ""
    import json as _json
    closes = [m.end() - 1 for m in re.finditer(r"\}", t)]
    for e in closes[::-1]:
        if e <= s:
            break
        sub = t[s:e + 1]
        sub = re.sub(r",\s*([}\]])", r"\1", sub)
        try:
            _json.loads(sub)
            return sub
        except Exception:
            continue
    t = t[s:t.rfind("}") + 1] if t.rfind("}") > s else t[s:]
    return re.sub(r",\s*([}\]])", r"\1", t)


def _extract_json_field(raw: str, field: str) -> list:
    """整体 JSON 解析失败时，用正则从模型输出里单独提取某字段的数组字面量。"""
    import json as _json
    m = re.search(r'"%s"\s*:\s*(\[.*?\])\s*(?:,|$)' % field, raw, re.S)
    if not m:
        return []
    lit = m.group(1)
    lit = re.sub(r",\s*([}\]])", r"\1", lit)
    try:
        val = _json.loads(lit)
        return val if isinstance(val, list) else []
    except Exception:
        return []


def _dedup_events_by_pairs(events: list[dict],
                           pairs: list) -> tuple[list[dict], int]:
    """通用事件合并：按语义重复对清单丢弃后者（保留较早完整事件）。返回 (去重后, 丢弃数)。

    纯函数、零 LLM、无实例状态。重复对来源不限——可由 _scene_split_review 的 LLM
    语义评审产生，也可人工给定；pairs 元素可为 (i, j) 或 (i, j, reason)。

    设计为通用：任何需要"按重复对合并事件清单"的场景（派生事件去重、拆场景评审、
    未来其他语义去重）都可复用。调用方负责在去重后 <2 时回退到安全路径。
    """
    if not events or len(events) < 2 or not pairs:
        return events, 0
    drop: set[int] = set()
    for p in pairs:
        try:
            i, j = int(p[0]), int(p[1])
        except Exception:
            continue
        if 0 <= i < len(events) and 0 <= j < len(events) and i != j:
            drop.add(j)
    if not drop:
        return events, 0
    new_events = [e for idx, e in enumerate(events) if idx not in drop]
    if len(new_events) < 2:
        # 去重后事件过少，不返回残缺清单（由调用方回退）
        return [], len(drop)
    return new_events, len(drop)
