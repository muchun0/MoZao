# -*- coding: utf-8 -*-
"""AI 痕迹量化检测（P1-4）。

确定性规则检测成文中的 AI 味（连接词堆砌 / 抽象情绪词直白宣告 / 句式排比 / 段落雷同），
纯函数、不调 LLM，供 QualityChecker 作为计分项（只扣分、不单独阻断生成）。
"""
from __future__ import annotations

import re

# 网文 AI 腔高频连接词（堆砌即露馅）
CONNECTIVE_WORDS = [
    "然而", "因此", "不仅", "而且", "值得注意的是", "综上所述",
    "与此同时", "此外", "总之", "显而易见", "事实上", "确实",
    "毕竟", "更何况", "由此可见", "换句话说", "换言之",
]

# 抽象情绪词直白宣告（应改为动作+微表情，见 _character_depth_rules）
EMOTION_WORDS = [
    "愤怒", "震惊", "无奈", "开心", "难过", "恐惧", "兴奋",
    "悲伤", "痛苦", "幸福", "激动", "失望", "害怕", "紧张",
    "悲痛欲绝", "欣喜若狂", "怒火中烧",
]

# 连接词密度阈值（每千字）
CONN_DENSITY_LIMIT = 6.0
# 情绪词密度阈值（每千字）
EMO_DENSITY_LIMIT = 2.0


def _count(text: str, words: list[str]) -> int:
    return sum(text.count(w) for w in words)


def _parallel_issue(text: str) -> bool:
    """句式排比：连续 3 句以相同 2 字词开头。"""
    sents = [s.strip() for s in re.split(r"[。！？；\n]", text) if s.strip()]
    same_run = 1
    for i in range(1, len(sents)):
        cur, prev = sents[i][:2], sents[i - 1][:2]
        if cur and cur == prev:
            same_run += 1
            if same_run >= 3:
                return True
        else:
            same_run = 1
    return False


def _para_similar(text: str) -> bool:
    """段落结构雷同：连续 3 段（≥30 字）长度差 ≤15%。"""
    paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) >= 30]
    if len(paras) < 3:
        return False
    same_run = 1
    for i in range(1, len(paras)):
        a, b = len(paras[i - 1]), len(paras[i])
        if min(a, b) > 0 and abs(a - b) / min(a, b) <= 0.15:
            same_run += 1
            if same_run >= 3:
                return True
        else:
            same_run = 1
    return False


def score_ai_taste(text: str) -> int:
    """AI 痕迹分 0-100（越高越自然），各维度扣分叠加。"""
    if not text:
        return 100
    total = len(text)
    score = 100
    if _count(text, CONNECTIVE_WORDS) * 1000 / total >= CONN_DENSITY_LIMIT:
        score -= 25
    if _count(text, EMOTION_WORDS) * 1000 / total >= EMO_DENSITY_LIMIT:
        score -= 20
    if _parallel_issue(text):
        score -= 15
    if _para_similar(text):
        score -= 10
    return max(0, min(100, score))


def ai_taste_issues(text: str, threshold: int = 60) -> list[dict]:
    """综合分低于 threshold 时，返回命中的 AI 痕迹维度 issue（level=中改，不阻断）。"""
    if not text or score_ai_taste(text) >= threshold:
        return []
    issues: list[dict] = []
    total = len(text)
    conn = _count(text, CONNECTIVE_WORDS)
    emo = _count(text, EMOTION_WORDS)
    if conn * 1000 / total >= CONN_DENSITY_LIMIT:
        issues.append({
            "type": "AI痕迹-连接词堆砌",
            "detail": f"连接词密度 {conn * 1000 / total:.1f}/千字，高于阈值（然而/因此/不仅…而且/值得注意的是等）",
            "level": "中改",
        })
    if emo * 1000 / total >= EMO_DENSITY_LIMIT:
        issues.append({
            "type": "AI痕迹-情绪词直白",
            "detail": f"抽象情绪词密度 {emo * 1000 / total:.1f}/千字（'他很愤怒/她很震惊'式直白宣告，应用动作+微表情替代）",
            "level": "中改",
        })
    if _parallel_issue(text):
        issues.append({
            "type": "AI痕迹-句式排比",
            "detail": "连续多句以相同词开头，句式对称明显，读感模板化",
            "level": "中改",
        })
    if _para_similar(text):
        issues.append({
            "type": "AI痕迹-段落雷同",
            "detail": "连续段落字数接近，段落结构模板化",
            "level": "中改",
        })
    return issues
