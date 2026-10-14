# -*- coding: utf-8 -*-
"""Beat 覆盖度门禁（三期 F1，2026-10-14）。

把大纲章计划（core_event / character_moves 的逐拍动作序列）作为"计划 beat"权威来源，
正文生成后比对实际覆盖了几成拍，分级判定（pass/warn/fail/skip）并落 `beat_gate.json`。

设计约束（AGENTS.md §11 / REQ-beat-gate）：
- **纯确定性、零 LLM**：同一输入恒得同一输出，可复现、零 token 成本；
- **检测+记录+提示，不独立重生成正文**：重写统一由既有 quality.ensure_quality 链路负责
  （已 ≤3 轮 + best-version 兜底），本模块不新起 LLM 循环，防 token 成本失控；
- 拍级命中用"关键片段子串匹配"（沿用 P0-17 `_core_event_missing` 已验证口径），
  不做 embedding 语义指纹（留待后续）。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

#: 拍文本切分后需剔除的无信息量短词（保留 2 字以上实词片段做子串命中）
_STOP_CHARS = set("了的着他她它在把被是也就都和与而又再才但却向到从对为以上下中后前时这那其之等个只要会能可将，。；、（）()：:「」 \t")
#: 计划拍切分（character_moves 逐拍）
_PLAN_SPLIT_RE = re.compile(r"[→。；;]")
#: 无信息 bigram 整体停用（两个字都在停用字符表里的片段直接丢弃）


def _split_plan_beats(moves: str) -> list[str]:
    """把大纲 character_moves 拆成逐拍动作序列（按 →/。；;）。<2 拍返回 []（不误报）。"""
    if not moves or not str(moves).strip():
        return []
    beats = [b.strip() for b in _PLAN_SPLIT_RE.split(str(moves)) if b and b.strip()]
    return beats if len(beats) >= 2 else []


def _char_bigrams(text: str) -> list[str]:
    """拍文本 → 有信息量的字符二元组（bigram）。

    中文改写识别不能靠整句子串（"主角在墟口遭遇灰卒被咬死" vs 正文
    "陆沉在墟口拾荒…遭遇灰卒，被咬喉致死" 整句不命中但核心词都在）。
    改用滑动 bigram：只保留非停用字组成的相邻二字，对换写/代词替换鲁棒。
    """
    clean = [c for c in str(text) if c not in _STOP_CHARS]
    grams: list[str] = []
    for i in range(len(clean) - 1):
        g = clean[i] + clean[i + 1]
        if g in grams:  # 去连续重复
            continue
        grams.append(g)
    return grams


def _beat_covered(beat: str, content: str) -> tuple[bool, list[str], list[str]]:
    """单拍是否被正文覆盖：拍内有信息量 bigram ≥50% 作为子串出现在正文。

    返回 (covered, hit_grams, miss_grams)。无 bigram → 保守视为未覆盖（False）。
    """
    grams = _char_bigrams(beat)
    if not grams:
        return False, [], [beat]
    hit = [g for g in grams if g in content]
    miss = [g for g in grams if g not in content]
    covered = len(hit) * 2 >= len(grams)  # ≥50% bigram 命中
    return covered, hit, miss


def compute_beat_coverage(content: str, moves: str, core_event: str = "",
                          min_coverage: float = 0.6, warn_coverage: float = 0.4) -> dict:
    """计算正文对章计划 beat 的覆盖率并分级。

    返回：{planned:[{beat,covered,hit,miss}], coverage, verdict, missing}
      - 无计划拍 → verdict="skip"，coverage=None（不误报）；
      - coverage ≥ min → "pass"；[warn, min) → "warn"；< warn → "fail"。
    """
    planned_beats = _split_plan_beats(moves) or _split_plan_beats(core_event)
    if not planned_beats:
        return {"planned": [], "coverage": None, "verdict": "skip", "missing": []}
    body = content or ""
    planned = []
    covered_count = 0
    missing: list[str] = []
    for beat in planned_beats:
        ok, hit, miss = _beat_covered(beat, body)
        planned.append({"beat": beat, "covered": ok, "hit": hit, "miss": miss})
        if ok:
            covered_count += 1
        else:
            missing.append(beat)
    coverage = covered_count / len(planned_beats)
    if coverage >= min_coverage:
        verdict = "pass"
    elif coverage >= warn_coverage:
        verdict = "warn"
    else:
        verdict = "fail"
    return {"planned": planned, "coverage": round(coverage, 3),
            "verdict": verdict, "missing": missing}


def assess_chapter(content: str, moves: str, core_event: str = "",
                   min_coverage: float = 0.6, warn_coverage: float = 0.4) -> dict:
    """对单章正文做 beat 覆盖门禁评估（纯函数，供 stages 接线与 router 体检复用）。"""
    result = compute_beat_coverage(content, moves, core_event, min_coverage, warn_coverage)
    if result["verdict"] == "skip":
        logger.info("[BeatGate] 无计划拍（moves/core_event 均空），跳过覆盖评估")
    else:
        logger.info("[BeatGate] 覆盖率=%.2f verdict=%s 漏写拍=%d",
                    result["coverage"], result["verdict"], len(result["missing"]))
    return result
