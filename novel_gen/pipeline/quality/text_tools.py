# -*- coding: utf-8 -*-
"""质检三层重构：无状态纯文本工具与模块级纯函数（L1 共享）。

搬迁自旧 `pipeline/quality.py` 尾部工具区，**行为零变化**（TECH §12 步骤 1）。
本模块不持有 `QualityChecker` 状态、不访问 LLM 之外的账本；可离线单测。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ...llm.json_parse import parse_json_obj
from ...utils.text import longest_common_substr_len as _lcs_len
from ..templates import panel_anchor_check_prompt
from . import constants as C

logger = logging.getLogger("novel_gen.pipeline.quality")


# ---------- 摘要拆分 / 核心事件覆盖 ----------

def split_summary(text: str) -> tuple[str, str]:
    """按『【摘要】』标记拆分正文与摘要（与 stages 保持一致）。"""
    marker = "【摘要】"
    idx = text.rfind(marker)
    if idx > 0:
        body = text[:idx].strip()
        summary = text[idx + len(marker):].strip().replace("\n", " ")
        return body, summary
    return text.strip(), ""


#: 兼容旧模块名（旧 quality.py 对外曾以 `_split_summary` 暴露）
_split_summary = split_summary


def core_event_missing(content: str, core_event: str) -> list[str]:
    """P0-17：core_event 分段后每段与正文最长公共子串匹配，命中段 <60% 返回未覆盖段。

    2 字窗口对专有名词（忘忧阁/阁主密报）与普通动作（下墟/遇袭）均敏感，低误报。
    阈值用整数不等式 hit*10 < len*6 表达「≥60% 通过」——2 段命中 1（50%）必须
    判为未覆盖（v9 ch6 大纲「忘忧阁密报」未进正文即 2 段 1 命中）。
    """
    if not content or not core_event:
        return []
    segs = [s.strip() for s in re.split(r"[→，,。；;]", core_event)
            if len(s.strip()) >= 4]
    if not segs:
        return []
    hit = sum(1 for s in segs if _lcs_len(s, content) >= 2)
    if hit * 10 >= len(segs) * 6:
        return []
    return [s for s in segs if _lcs_len(s, content) < 2]


_core_event_missing = core_event_missing


# ---------- 句子切分 / 归一化 / 面板行判定 ----------

_QUOTE_OPEN = {"“": "”", "「": "」", '"': '"'}


def iter_sentences(text: str):
    """按句末标点切句，返回 (句子原文, 是否在对话引号内)。追踪中英文引号状态。"""
    in_quote = None
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if in_quote is None and ch in _QUOTE_OPEN:
            in_quote = ch
        elif in_quote is not None and ch == _QUOTE_OPEN[in_quote]:
            in_quote = None
        if ch in "。！？…" and buf:
            yield "".join(buf), in_quote is not None
            buf = []
    tail = "".join(buf).strip()
    if tail:
        yield tail, in_quote is not None


_iter_sentences = iter_sentences


def norm_sentence(s: str) -> str:
    """归一化：只保留中文与字母数字，去空白标点（复读判定基准）。"""
    return "".join(ch for ch in (s or "") if ch.isalnum() or "一" <= ch <= "鿿")


_norm_sentence = norm_sentence


def is_panel_line(norm: str) -> bool:
    """数字面板行：含 次数/进度/剩余/累积/状态/面板 关键词且带数字（如「剩余次数2」）。"""
    return any(k in norm for k in C._PANEL_KEYWORDS) and any(c.isdigit() for c in norm)


_is_panel_line = is_panel_line


def iter_repeat_groups(groups: dict):
    """遍历归一化句 → (bucket, norm, 原始句子列表)，按出现次数降序（大问题先报）。"""
    items = []
    for bucket, mp in groups.items():
        for norm, sentences in mp.items():
            items.append((bucket, norm, sentences))
    items.sort(key=lambda t: -len(t[2]))
    return items


_iter_repeat_groups = iter_repeat_groups


# ---------- 定点重写定位（段落 / 句锚点 / 窗口合并 / 段落对齐） ----------

def locate_problem_spans(content: str, issues: list[dict]) -> list[tuple[int, int]]:
    """从 issue 定位问题段落（起点, 终点），供定点重写。

    定位来源优先级：
    1. issue["pos"]（如「段3」「第3段」）→ 按段落序号定位；
    2. issue["detail"] 中 ≥8 字的片段 → 归一化后在正文句子中匹配，
       以句为锚点取前后 _CTX 字符窗口；
    3. 无法定位 → 返回空列表（调用方整章重写）。
    """
    if not content or not issues:
        return []
    spans: list[tuple[int, int]] = []
    # 1) 段落定位
    para_nos = set()
    for it in issues:
        pos = (it.get("pos") or "").strip()
        if not pos:
            continue
        m = re.search(r"(\d+)", pos)
        if m:
            para_nos.add(int(m.group(1)))
    if para_nos:
        paras = content.split("\n")
        for pi in sorted(para_nos):
            if 1 <= pi <= len(paras):
                seg = paras[pi - 1]
                start = content.find(seg)
                if start >= 0:
                    spans.append((start, start + len(seg)))
        if spans:
            return merge_spans(spans)
    # 2) 句锚点定位：detail 中 ≥8 字片段归一化匹配正文句子
    anchors: list[str] = []
    for it in issues:
        detail = it.get("detail") or ""
        for m in re.finditer(r"[“\"『]?([^”\"』，。；：]{8,40})[”\"』]?", detail):
            frag = m.group(1).strip()
            if frag:
                anchors.append(frag)
    if anchors:
        sents = list(iter_sentences(content))
        for frag in anchors:
            nf = norm_sentence(frag)
            if len(nf) < C._MIN_FRAG:
                continue
            for sentence, _ in sents:
                ns = norm_sentence(sentence)
                if ns and (nf in ns or ns in nf):
                    i = content.find(sentence)
                    if i >= 0:
                        spans.append((max(0, i - C._CTX),
                                      min(len(content), i + len(sentence) + C._CTX)))
                    break
        if spans:
            return merge_spans(spans)
    return []


_locate_problem_spans = locate_problem_spans


def merge_spans(spans: list[tuple[int, int]], gap: int = C._SPAN_GAP) -> list[tuple[int, int]]:
    """间距 ≤ gap 的窗口合并为一个（一次调用重写合并窗口）。"""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


_merge_spans = merge_spans


def align_to_paras(content: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """把窗口边界对齐到段落首尾，避免重写拼接处断句。"""
    out = []
    for s, e in spans:
        s0 = content.rfind("\n", 0, s) + 1
        e0 = content.find("\n", e)
        if e0 == -1:
            e0 = len(content)
        out.append((s0, e0))
    return out


_align_to_paras = align_to_paras


# ---------- 面板结算事件锚点（确定性正则兜底 + LLM 语义判定桥接） ----------

def seg_has_settle_event_regex(seg: str) -> bool:
    """确定性兜底：seg 内出现结算事件锚点、且锚点前 16 字无回忆/转述/他人语境词。

    原 _panel_progress_issues 内联逻辑原样抽出（行为零变化）：命中"死了"且前一字为
    "快"=将死未死跳过；锚点前 16 字命中 _PANEL_EVENT_RECALL 视为回忆/转述不算结算。
    """
    for m in C._PANEL_EVENT_ANCHORS.finditer(seg):
        # 特判"快死了"=将死未死（非结算）：锚点"死了"前 1 字为"快"
        if m.group() == "死了" and seg[max(0, m.start() - 1):m.start()] == "快":
            continue
        ctx = seg[max(0, m.start() - 16):m.start()]
        if not C._PANEL_EVENT_RECALL.search(ctx):
            return True
    return False


_seg_has_settle_event_regex = seg_has_settle_event_regex


def llm_panel_seg_settle(seg: str, llm) -> bool | None:
    """LLM 语义判定 seg 段是否发生「真实结算事件」（REQ-词表换LLM 批次二）。

    True/False = LLM 明确判定（调用方直接采用，不回退词表）；
    None = 输出非结构化 / has_settle_event 缺失或非 bool，调用方回退锚点正则兜底。
    异常一律抛出由调用方捕获留痕（本函数不裸吞，§11.6）。
    """
    system, user = panel_anchor_check_prompt(seg)
    raw = llm.chat(system, user, temperature=0.2, max_tokens=200, json_mode=True)
    data = parse_json_obj(raw)
    val = data.get("has_settle_event") if isinstance(data, dict) else None
    return val if isinstance(val, bool) else None


_llm_panel_seg_settle = llm_panel_seg_settle


# ---------- 角色底色懒加载（质检链路共享） ----------

_PERSONA_CACHE: dict[str, str] = {}


def load_persona(name: str) -> str:
    """加载 agent 角色底色（agents/prompts/{name}.md）；失败返回空串不阻塞。"""
    if name not in _PERSONA_CACHE:
        try:
            # __file__ = novel_gen/pipeline/quality/text_tools.py，上溯两级到 pipeline/
            _p = Path(__file__).resolve().parent.parent / "agents" / "prompts" / f"{name}.md"
            _PERSONA_CACHE[name] = _p.read_text(encoding="utf-8")
        except Exception:
            _PERSONA_CACHE[name] = ""
    return _PERSONA_CACHE[name]


_load_persona = load_persona


def has_type(issues, keyword: str) -> bool:
    """issues 中是否存在 type 含指定关键词的问题。"""
    return any(keyword in (it.get("type", "") or "") for it in issues)


_has_type = has_type
