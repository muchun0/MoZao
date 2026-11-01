# -*- coding: utf-8 -*-
"""一致性「事件重复」候选确定性复核闸门（质检语义复核二期）。

## 职责边界

旧 `_consistency_llm_check` 与 `ConsistencyReviewer` 产出的 `自相矛盾-事件重复`
候选，在进入最终 issue 列表前，由本模块做一次**窄职责二次确认**：判断候选是否属于
「同一动作/场景被他人对话、打断、停顿分隔成前后两段，两段是同一动作的前后半程、
阶段递进承接」这一合法形态（TECH §5.1 类别 d）。

## 设计约束（TECH §1.2 / §6.1）

- **零 LLM**：只做本地字符串与正则特征判定，不发起任何模型调用；
- **纯函数**：不访问 `QualityChecker` 状态、无共享可变属性，可离线单测、可并发；
- **fail-open**：引文抽取失败、窗口定位失败、信号不足、模块自身异常时一律**保留候选**，
  绝不静默压制真问题；只有三信号同时满足且无重演反向冲突，才剔除候选；
- **单一权威源**：旧内联方法与新评委共用本模块，避免两套判定口径再次漂移。
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from . import constants as C

logger = logging.getLogger("novel_gen.pipeline.quality")

#: type 中命中该标记才进入事件重复复核；其余一致性类型（身体状态/行为模式/方位指代/比喻）原样放行
EVENT_DUP_MARKER = "事件重复"

#: 排除条款 d 文本（中断后续写承接）。旧内联方法、新评委内置兜底、外置 md 三处语义一致；
#: 新增条款以本常量为单一权威源，a/b/c 历史锁定文本不在此合并（TECH §1.2 I-6）。
EXCLUSION_CLAUSE_D = (
    "d) 同一动作/场景被他人对话、打断、停顿分隔成前后两段时，两段属于同一动作的前后半程、"
    "阶段递进承接（前一处未完成，后一处更深入、出现前一处没有的新对象/动作细节，中间隔着对话对峙、"
    "打断或时间流逝），不是'同一事件两个版本'，不得判为事件重复；只有两处动作都已独立完成闭环、"
    "中间没有打断承接、状态回到起点后又完整重演一遍，才属于事件重复。"
)

#: 判定结果：continue = 识别为承接形态剔除；keep = 保留候选（真重演 / 证据不足 / 信号不足）
_VERDICT_CONTINUE = "continue"
_VERDICT_KEEP = "keep"

#: detail / evidence 中「前一处 / 后一处」分段标记
_CITE_SPLIT = re.compile(r"前一处[:：]|后一处[:：]")
#: 引号包裹的引文（「」『』“”""）
_QUOTED = re.compile(r'[「『“"]([^」』”"]{2,})[」』”"]')
#: 中文片段提取（滑窗词集合只统计汉字）
_HAN = re.compile(r"[^\u4e00-\u9fa5]")


def is_event_duplicate(issue: dict) -> bool:
    """type 含「事件重复」才进入复核；其余一致性类型返回 False 原样保留。"""
    return EVENT_DUP_MARKER in str(issue.get("type", "") or "")


def review_event_duplicates(
    issues: Iterable[dict], content: str
) -> tuple[list[dict], list[dict]]:
    """对事件重复候选做承接形态复核。

    Returns:
        kept: 保留进入最终 issues 的候选（含未进入复核的其他类型）；
        filtered: 被识别为 d 类承接形态剔除的候选（附 `review_filtered / review_note /
        review_signals`，仅留痕审计，不进入最终 issues）。
    """
    kept: list[dict] = []
    filtered: list[dict] = []
    try:
        for it in issues:
            if not isinstance(it, dict) or not is_event_duplicate(it):
                kept.append(it)
                continue
            verdict, signals = _classify_candidate(it, content)
            if verdict == _VERDICT_CONTINUE:
                it["review_filtered"] = True
                it["review_note"] = "对话打断后的动作递进承接，非事件重演"
                it["review_signals"] = signals
                filtered.append(it)
                logger.warning(
                    "[quality] 事件重复候选识别为承接形态已剔除（signals=%s）：%s",
                    signals, str(it.get("detail", ""))[:120])
            else:
                kept.append(it)
    except Exception:  # noqa: BLE001 —— 闸门自身异常必须 fail-open，整批保留不阻塞主链路
        logger.warning("[quality] 事件重复复核闸门异常，fail-open 整批保留", exc_info=True)
        return list(issues), []
    return kept, filtered


# ---------- 引文抽取与定位 ----------

def _normalize(text: str) -> str:
    """归一化：去空白与标点，仅保留汉字与字母数字，供子串匹配。"""
    return re.sub(r'[\s，。！？、；：“”"‘’「」『』（）()…—\-·,.!?;:]', "", str(text or ""))


def _extract_citations(detail: str, evidence: str) -> tuple[str, str] | None:
    """从 detail / evidence 抽取「前一处 / 后一处」两段引文；抽不出两段独立引文返回 None。"""
    for source in (detail, evidence):
        text = str(source or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _CITE_SPLIT.split(text) if p and p.strip()]
        # split 会丢掉标记本身；两段以上才视为「前一处 + 后一处」结构
        if len(parts) >= 2:
            first, second = parts[0], parts[1]
            q1 = _first_quote(first)
            q2 = _first_quote(second)
            if q1 and q2 and len(_normalize(q1)) >= C._REVIEW_CITE_MIN_CHARS \
                    and len(_normalize(q2)) >= C._REVIEW_CITE_MIN_CHARS:
                return q1, q2
    return None


def _first_quote(segment: str) -> str:
    """优先取引号内引文；无引号时取段首到破折号/句末的叙述片段。"""
    m = _QUOTED.search(segment)
    if m:
        return m.group(1).strip()
    cut = re.split(r"——|—|。|；|;", segment, maxsplit=1)
    return cut[0].strip()


def _locate(norm_content: str, quote: str) -> tuple[int, int] | None:
    """归一化子串定位；找不到返回 None（fail-open）。"""
    q = _normalize(quote)
    if len(q) < C._REVIEW_CITE_MIN_CHARS:
        return None
    idx = norm_content.find(q)
    if idx < 0:
        return None
    return idx, idx + len(q)


# ---------- 三信号判定 ----------

def _interruption_signal(middle: str) -> bool:
    """信号 A：两段之间存在他人台词对话或对峙/倒计时打断提示。"""
    if C._REVIEW_INTERRUPTION_DIALOGUE.search(middle):
        return True
    return any(w in middle for w in C._REVIEW_INTERRUPTION_WORDS)


def _progression_signal(window1: str, window2: str) -> bool:
    """信号 B：后一段是递进深化——出现前一段没有的新细节，或命中递进动词/强度词。"""
    if any(w in window2 for w in C._REVIEW_PROGRESSION_VERBS):
        return True
    if any(w in window2 for w in C._REVIEW_PROGRESSION_STRENGTH):
        return True
    grams1 = _char_bigrams(window1)
    grams2 = _char_bigrams(window2)
    # 后窗口独有 bigram ≥ 2 个，视为出现新对象 / 新动作细节
    return len(grams2 - grams1) >= 2


def _no_closed_loop_signal(window1_tail: str, middle: str, window2_head: str) -> bool:
    """信号 C：前一动作未闭环收束——衔接片段中没有场景转换 / 状态重置标志。"""
    joined = f"{window1_tail}{middle}{window2_head}"
    return not any(w in joined for w in C._REVIEW_CLOSED_LOOP_WORDS)


def _repeat_conflict(window2: str) -> bool:
    """反向冲突：后窗口出现重演标志词 → 真重演，禁止剔除。"""
    return any(w in window2 for w in C._REVIEW_REPEAT_MARKERS)


def _char_bigrams(text: str) -> set[str]:
    han = _HAN.sub("", text)
    return {han[i:i + 2] for i in range(max(0, len(han) - 1))}


# ---------- 候选分类 ----------

def _classify_candidate(issue: dict, content: str) -> tuple[str, list[str]]:
    """判定单条事件重复候选；返回 (verdict, hit_signals)。证据不足/信号不足 → keep。"""
    detail = str(issue.get("detail", "") or "")
    evidence = str(issue.get("evidence", "") or "")
    citations = _extract_citations(detail, evidence)
    if citations is None:
        return _VERDICT_KEEP, ["citation_unavailable"]

    norm_content = _normalize(content)
    q1, q2 = citations
    loc1 = _locate(norm_content, q1)
    loc2 = _locate(norm_content, q2)
    if loc1 is None or loc2 is None:
        # 引文在正文定位不到：fail-open 保留（与离线 mock 负例契约兼容）
        return _VERDICT_KEEP, ["locate_failed"]

    (s1, e1), (s2, e2) = loc1, loc2
    if e1 > s2:  # 窗口重叠或顺序颠倒，不构成前后两段
        return _VERDICT_KEEP, ["window_order_invalid"]

    middle = norm_content[e1:s2]
    w1 = norm_content[s1:e1]
    w2 = norm_content[s2:e2]

    if _repeat_conflict(w2):
        return _VERDICT_KEEP, ["repeat_marker"]

    signals: list[str] = []
    if _interruption_signal(middle):
        signals.append("interruption_dialogue")
    if _progression_signal(w1, w2):
        signals.append("progression_new_detail")
    if _no_closed_loop_signal(w1[-40:], middle, w2[:40]):
        signals.append("no_closed_loop")

    # 三信号同时满足才识别为承接剔除；其余任意组合一律保留（保守口径，防误剔真重演）
    if signals == ["interruption_dialogue", "progression_new_detail", "no_closed_loop"]:
        return _VERDICT_CONTINUE, signals
    return _VERDICT_KEEP, signals
