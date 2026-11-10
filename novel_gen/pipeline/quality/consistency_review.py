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
                it["review_note"] = _filter_note(signals)
                it["review_signals"] = signals
                filtered.append(it)
                logger.warning(
                    "[quality] 事件重复候选识别为承接/回引形态已剔除（signals=%s）：%s",
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


def _extract_citations(detail: str, evidence: str,
                       norm_content: str = "") -> tuple[str, str] | None:
    """从 detail / evidence 抽取「前一处 / 后一处」两段引文；抽不出两段独立引文返回 None。

    2026-11-10 修复④②：每段不再只取第一个引号（评委常把短台词写在前面，
    「您坐下。」只有 3 字，会直接判证据不足），改为「在正文里可定位的最长引文」优先，
    这样「重新坐下，屁股只沾了椅面三分之一」这类动作引文才能被选中。
    """
    for source in (detail, evidence):
        text = str(source or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _CITE_SPLIT.split(text) if p and p.strip()]
        # split 会丢掉标记本身；两段以上才视为「前一处 + 后一处」结构
        if len(parts) >= 2:
            q1 = _pick_quote(parts[0], norm_content)
            q2 = _pick_quote(parts[1], norm_content)
            if not (q1 and q2):
                continue
            n1, n2 = _normalize(q1), _normalize(q2)
            # 2026-11-10 修复⑤③：两段都达标，或「两段都能在正文里逐字定位」也放行。
            # v7 评委把前一处写成 3 字动作『退回来』（<6 阈值），此前整条证据作废、
            # 闸门 fail-open 保留，导致「未完成→完成」的递进被当成事件重复计大改。
            if (len(n1) >= C._REVIEW_CITE_MIN_CHARS
                    and len(n2) >= C._REVIEW_CITE_MIN_CHARS):
                return q1, q2
            if norm_content and n1 in norm_content and n2 in norm_content \
                    and len(n1) + len(n2) >= C._REVIEW_CITE_MIN_CHARS:
                return q1, q2
    return None


def _pick_quote(segment: str, norm_content: str = "") -> str:
    """取该段代表性引文：可定位的最长引号引文优先，其次首个引文，最后叙述片段。"""
    quotes = [m.group(1).strip() for m in _QUOTED.finditer(segment)]
    if norm_content:
        located = [q for q in quotes
                   if len(_normalize(q)) >= C._REVIEW_CITE_MIN_CHARS
                   and _normalize(q) in norm_content]
        if located:
            return max(located, key=lambda q: len(_normalize(q)))
    if quotes:
        return quotes[0]
    return _first_quote(segment)


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
    # 2026-11-10 修复⑤③：3 字以上的动作短语也允许定位。此前 <6 字一律 None，
    # v7 的『退回来』(3 字) 让整条证据 locate_failed、闸门 fail-open 保留。
    if len(q) < 3:
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


# ---------- 2026-11-10 修复④②：现场搬演 vs 对话回引 ----------

def _narration_only(content: str) -> str:
    """剔除对话引号内容后的叙述文本（现场搬演只可能出现在叙述里）。"""
    return _QUOTED.sub("", str(content or ""))


def count_on_page(content: str, phrase: str) -> tuple[int, int]:
    """返回 (现场搬演次数, 对话内出现次数)。

    现场搬演 = 该短语在**引号外**叙述中的出现次数；引号内出现视为人物回引。
    """
    q = _normalize(phrase)
    if len(q) < C._REVIEW_CITE_MIN_CHARS:
        return 0, 0
    total = _normalize(content).count(q)
    on_page = _normalize(_narration_only(content)).count(q)
    return on_page, max(0, total - on_page)


def _dialogue_recall(content: str, phrase: str) -> bool:
    """后一处引文落在对话引号内，且附近含回引标记（刚才/先前…）→ 是回引不是搬演。"""
    raw = str(content or "")
    idx = raw.find(phrase)
    if idx < 0:
        return False
    if not any(m.start() <= idx < m.end() for m in _QUOTED.finditer(raw)):
        return False
    window = raw[max(0, idx - 30): idx + len(phrase) + 20]
    return any(w in window for w in C._RECALL_MARKERS)


def _single_on_page_closed(content: str, q1: str, q2: str) -> bool:
    """后一处动作现场只发生 1 次，且另一处引文是它的收束（重新坐下/坐回…）。

    《契裂之眼》v6 第1章：起身 @1882 现场一次 → 劝阻 →「重新坐下」@2129 收束，
    @2719 只是对话回引；评委把「一次发生 + 收束」当成了两次现场发生。
    """
    if _normalize(q1) == _normalize(q2):
        return False
    on_page, _recall = count_on_page(content, q2)
    if on_page > 1:
        return False
    return any(w in (_normalize(q1) + _normalize(q2)) for w in C._EPISODE_CLOSE_WORDS)


def _progression_completion(content: str, q1: str, q2: str) -> bool:
    """同一动作现场出现两次，但第一次「未完成」、第二次「完成」→ 阶段递进，不是重演。

    《契裂之眼》v7：@2492「走到门边，手搭上门把，没拧」→ @4856「走到门边，手搭在把手上」
    → @4923「拧下把手」开门。两次都是现场动作，但第二次是推进完成，不是完整重复一遍。
    判据：取后一处动作的首个分句作「动作骨干」，它在正文叙述中恰好出现 2 次，
    第一次之后 100 字内含未完成标记、第二次之后 100 字内含完成动作。
    """
    raw = str(content or "")
    core = ""
    for part in re.split(r"[，。；,;！？!?]", str(q2 or "")):
        seg = part.strip()
        if len(_normalize(seg)) >= 3:
            core = seg
            break
    if len(_normalize(core)) < 3:
        return False
    hits = [m.start() for m in re.finditer(re.escape(core), raw)]
    if len(hits) != 2:
        return False
    win1 = raw[hits[0]: hits[0] + len(core) + 100]
    win2 = raw[hits[1]: hits[1] + len(core) + 100]
    return (any(w in win1 for w in C._UNFINISHED_WORDS)
            and any(w in win2 for w in C._COMPLETION_WORDS))


def _filter_note(signals: list[str]) -> str:
    if "recall_in_dialogue" in signals:
        return "后一处为对话内回引前文动作，非第二次现场发生"
    if "single_on_page_occurrence" in signals:
        return "该动作现场仅发生一次（另一处为收束/回引），非事件重演"
    if "unfinished_then_complete" in signals:
        return "同一动作「未完成 → 完成」属阶段递进，非事件重演"
    return "对话打断后的动作递进承接，非事件重演"


# ---------- 候选分类 ----------

def _classify_candidate(issue: dict, content: str) -> tuple[str, list[str]]:
    """判定单条事件重复候选；返回 (verdict, hit_signals)。证据不足/信号不足 → keep。"""
    detail = str(issue.get("detail", "") or "")
    evidence = str(issue.get("evidence", "") or "")
    norm_content = _normalize(content)
    citations = _extract_citations(detail, evidence, norm_content)
    if citations is None:
        return _VERDICT_KEEP, ["citation_unavailable"]

    q1, q2 = citations
    loc1 = _locate(norm_content, q1)
    loc2 = _locate(norm_content, q2)
    if loc1 is None or loc2 is None:
        # 引文在正文定位不到：fail-open 保留（与离线 mock 负例契约兼容）
        return _VERDICT_KEEP, ["locate_failed"]

    # 2026-11-10 修复④②：先区分「现场搬演」与「对话回引」，再走原有承接三信号。
    # 评委只有把同一动作写成两次独立现场才构成事件重复；一处是对话引述、
    # 另一处是这次动作的收束，属于「一次发生」，不构成候选。
    if not _repeat_conflict(_normalize(q2)):
        if _dialogue_recall(content, q2):
            return _VERDICT_CONTINUE, ["recall_in_dialogue"]
        if _single_on_page_closed(content, q1, q2):
            return _VERDICT_CONTINUE, ["single_on_page_occurrence"]
        # 2026-11-10 修复⑤③：同一动作「第一次未完成 → 第二次完成」是递进，不是重演。
        if _progression_completion(content, q1, q2):
            return _VERDICT_CONTINUE, ["unfinished_then_complete"]

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


# ---------- 2026-11-10 修复④①：事件重复转「硬约束」+ 可验证判据 ----------

def _quoted_actions(issue: dict) -> list[str]:
    """detail / evidence 中全部引号引文（按出现顺序）。"""
    out: list[str] = []
    for key in ("detail", "evidence"):
        out.extend(m.group(1).strip()
                   for m in _QUOTED.finditer(str(issue.get(key, "") or "")))
    return out


def event_duplicate_action(issue: dict, content: str) -> str:
    """取出该事件重复项里「被重复的动作」原文（优先后一处引文），取不到返回 ""。

    2026-11-10 修复④①：不再依赖 `_extract_citations` 的"前一处必须可定位"。
    评委的「前一处」常写短台词（如“您坐下。”），在正文里定位不到就会让整条证据作废，
    导致硬约束取不到动作、判据形同虚设。改为：先取后一处分段的可定位引文；再回退到
    「正文可定位的引文里出现次数最多（并列取更长）」的一条。
    """
    norm = _normalize(content)
    for key in ("detail", "evidence"):
        text = str(issue.get(key, "") or "").strip()
        if not text:
            continue
        parts = [p.strip() for p in _CITE_SPLIT.split(text) if p and p.strip()]
        if len(parts) < 2:
            continue
        quote = _pick_quote(parts[1], norm)
        qn = _normalize(quote)
        if len(qn) >= C._REVIEW_CITE_MIN_CHARS and qn in norm:
            return quote
        break
    located = [q for q in _quoted_actions(issue)
               if len(_normalize(q)) >= C._REVIEW_CITE_MIN_CHARS
               and _normalize(q) in norm]
    if not located:
        return ""
    return max(located, key=lambda q: (norm.count(_normalize(q)),
                                       len(_normalize(q))))


def event_duplicate_hard_block(issue: dict, content: str,
                               span: tuple[int, int] | None = None) -> str:
    """生成给改写模型的【硬约束】文本：明确要求删掉后一版对应段落/合并为一次发生。"""
    action = event_duplicate_action(issue, content)
    if not action:
        return ""
    where = ""
    if span:
        para = str(content[:span[0]]).count("\n") + 1
        where = f"（本段为后一版，约第 {para} 段）"
    return (
        "【硬约束·事件重复（必须执行，不得只做润色）】\n"
        f"- 被重复现场搬演的动作：{action}\n"
        f"- {where or '本段是它的后一版'}：必须删除本段中对该动作的完整重演。\n"
        "- 若本段含不可替代的新信息，只允许压成一句转述，不得再写完整动作序列。\n"
        "- 自检：改后该动作在本章引号外的现场叙述只允许出现 1 次；对话里回引不算。\n"
    )


def event_duplicate_span(content: str, issue: dict) -> tuple[int, int] | None:
    """「后一处重复」所在段落区间（行首, 行尾），供「按此改」定点删除。

    同一动作在正文出现 ≥2 次时取最后一次（要保留前一版、删后一版）；
    取不到精确字面位置时返回 None，由调用方回退到常规定位。
    """
    action = event_duplicate_action(issue, content)
    if not action:
        return None
    raw = str(content or "")
    on_page, _recall = count_on_page(raw, action)
    i = raw.rfind(action) if on_page >= 2 else raw.find(action)
    if i < 0:
        return None
    ps = raw.rfind("\n", 0, i) + 1
    pe = raw.find("\n", i + len(action))
    return ps, (len(raw) if pe == -1 else pe)


def event_duplicate_resolved(before: str, after: str, issue: dict) -> tuple[bool, str]:
    """可验证判据：改前现场 ≥2 次时，改后必须下降；且字数不得塌缩到 60% 以下。"""
    action = event_duplicate_action(issue, before)
    if not action:
        return True, "action_unavailable（取不到动作原文，不阻断）"
    b_on, _ = count_on_page(before, action)
    a_on, _ = count_on_page(after, action)
    if len(after) < int(len(before) * 0.6):
        return False, f"字数塌缩 {len(before)}→{len(after)}（<60%）"
    if b_on >= 2 and a_on >= b_on:
        return False, f"现场叙述仍 {a_on} 次（改前 {b_on} 次，重复段未删除）"
    return True, f"现场叙述 {b_on}→{a_on}"


# ---------- 2026-11-10 修复⑤②：detail 自述「不构成矛盾」的降级 ----------

def detail_self_negates(detail: str) -> bool:
    """评委 detail 自己写明「不构成矛盾/属递进承接/按排除条款」→ 该条不成立。

    反向保护：detail 里若同时出现正向矛盾断言（不能同时为真/前后不一…），
    视为混合表述，仍按大改保留，防止把真问题降掉。
    """
    text = str(detail or "")
    if not text:
        return False
    if any(w in text for w in C._POSITIVE_CONTRADICTION_MARKERS):
        return False
    return any(w in text for w in C._SELF_NEGATE_MARKERS)


def downgrade_self_negated(issues: list[dict]) -> list[dict]:
    """把「自述不构成矛盾却按大改计分」的条目降为中改，并留痕。

    只降级、不删除：问题仍展示、仍可被改稿器处理，但不再阻断 `passed`。
    """
    for it in issues:
        if it.get("level") == "大改" and detail_self_negates(it.get("detail", "")):
            it["level"] = "中改"
            it["level_note"] = "评委 detail 自述非矛盾，按中改计分（不计入 passed 阻断）"
            logger.info("[quality] 自述非矛盾的条目降级为中改：%s", str(it.get("detail", ""))[:80])
    return issues
