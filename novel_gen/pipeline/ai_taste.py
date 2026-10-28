# -*- coding: utf-8 -*-
"""AI 痕迹量化检测（P1-4）。

REQ-词表换LLM 批次二：连接词堆砌 / 情绪词直白宣告 / 填充词口头禅 / 零直白四维度
改为 LLM 语义判定（整章 1 次），词表+统计作为确定性兜底——LLM 失败/非结构化/异常/
开关关闭/未接 llm 时回退，兜底路径行为与旧版逐字节一致。明喻密度、句式排比、段落
雷同、文风漂移量化仍为纯确定性计算（不换 LLM）。
"""
from __future__ import annotations

import logging
import re

from ..llm.json_parse import parse_json_obj
from .templates import ai_taste_check_prompt

logger = logging.getLogger(__name__)

# 网文 AI 腔高频连接词（堆砌即露馅；兜底 + 漂移量化共用，常量保留）
CONNECTIVE_WORDS = [
    "然而", "因此", "不仅", "而且", "值得注意的是", "综上所述",
    "与此同时", "此外", "总之", "显而易见", "事实上", "确实",
    "毕竟", "更何况", "由此可见", "换句话说", "换言之",
]

# 抽象情绪词直白宣告（点破允许：'他怕了'+动作支撑；密度过高=堆砌警示）
EMOTION_WORDS = [
    "愤怒", "震惊", "无奈", "开心", "难过", "恐惧", "兴奋",
    "悲伤", "痛苦", "幸福", "激动", "失望", "害怕", "紧张",
    "悲痛欲绝", "欣喜若狂", "怒火中烧",
]

# 连接词密度阈值（每千字）
CONN_DENSITY_LIMIT = 6.0
# 情绪词密度阈值（每千字）——超限为警示（收敛），非禁词
EMO_DENSITY_LIMIT = 2.0
# '像……'明喻密度阈值（每千字）——白描传神很少用比喻，超限为警示（非禁词）
SIMILE_DENSITY_LIMIT = 2.0
# 直白点破句式：代词 + 情绪词 + 了/得/极（'他怕了''她恨得发颤'）
_DIRECT_PATTERN = re.compile(r"(他|她|我|它)(怕|恨|慌|怒|疼|惊|愣|慌|酸|软|麻)[了得极]")

# ---- REQ-超长篇攻坚 S2.4-L1 漂移量化常量（唯一事实来源，§11.3 不散落硬编码）----
#: 句长波动系数过小 → 句长趋同、模板化
_SENT_CV_TOO_LOW = 0.45
#: 句长波动系数过大 → 节奏失控、可读性差
_SENT_CV_TOO_HIGH = 1.20
#: 词汇多样性 TTR 过低 → 用词单调
_TTR_TOO_LOW = 0.35
#: 无意义填充词（口头禅）占比过高 → 重复用词（兜底 + 漂移量化共用，常量保留）
_FILLER_WORDS = ("然后", "于是", "只见", "当即", "顿时", "但是", "然而",
                 "因此", "不禁", "果然", "终于", "终于还是")
_FILLER_LIMIT = 0.006

#: LLM 判定四维度键（契约，与 ai_taste_check_prompt 输出一致）
_JUDGE_KEYS = ("connective_excess", "emotion_excess", "filler_overuse", "zero_direct")


def _sentences(text: str) -> list[str]:
    """按中文句末标点切句（确定性）。"""
    return [s.strip() for s in re.split(r"[。！？!?；;\n]+", text) if s.strip()]


def style_drift_metrics(text: str) -> dict:
    """REQ-超长篇攻坚 S2.4-L1：确定性文风漂移量化指标（≈0 token，每章可跑）。

    返回：句长均值/波动系数 cv、词汇多样性 TTR、最高频 2 字词占比（口头禅）、明喻密度。
    这些指标跨章对比（如与前期章均值比），偏离即文风漂移信号（Foreverse 实证句长波动
    系数是金指标）。
    """
    if not text:
        return {}
    sents = _sentences(text)
    total = len(text)
    lens = [len(s) for s in sents]
    mean_len = sum(lens) / len(lens) if lens else 0
    std = (sum((l - mean_len) ** 2 for l in lens) / len(lens)) ** 0.5 if lens else 0
    cv = std / mean_len if mean_len else 0
    # 词汇多样性 TTR（字级：去重字符 / 总有效字符——中文 2 字窗口对正常文本过严）
    chars = [c for c in text if c.strip() and c not in "，。！？；：、…—\"“”‘’"]
    ttr = len(set(chars)) / len(chars) if chars else 0
    # 无意义填充词占比（口头禅：然后/于是/只见等堆砌 → 重复用词、语感单调）
    filler_count = sum(text.count(w) for w in _FILLER_WORDS)
    filler_ratio = filler_count / total if total else 0
    simile = text.count("像") * 1000 / total if total else 0
    return {
        "sentence_count": len(lens),
        "mean_sentence_len": round(mean_len, 2),
        "sentence_cv": round(cv, 4),
        "ttr": round(ttr, 4),
        "filler_count": filler_count,
        "filler_ratio": round(filler_ratio, 4),
        "simile_per_1k": round(simile, 2),
    }


def drift_score(text: str) -> int:
    """REQ-超长篇攻坚 S2.4-L1：漂移分 0-100（越高越健康）。基于句长波动/多样性/口头禅/比喻。

    低句长波动（模板化）+ 低 TTR + 高口头禅占比 → 明显漂移，分数低。
    """
    m = style_drift_metrics(text)
    if not m or m.get("sentence_count", 0) < 3:
        return 100
    score = 100
    cv = m["sentence_cv"]
    if cv < _SENT_CV_TOO_LOW:
        score -= 20
    elif cv > _SENT_CV_TOO_HIGH:
        score -= 10
    if m["ttr"] < _TTR_TOO_LOW:
        score -= 25
    if m["filler_ratio"] > _FILLER_LIMIT:
        score -= 15
    if m["simile_per_1k"] >= SIMILE_DENSITY_LIMIT:
        score -= 10
    return max(0, min(100, score))


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


def _zero_direct(text: str) -> bool:
    """零直白：全文无一处直白点破句式（'他怕了'式）——情绪演过头、缺说三分。"""
    return not _DIRECT_PATTERN.search(text)


# ---------- REQ-词表换LLM 批次二：四维度 LLM 语义判定 ----------

def _dict_judge(text: str, total: int) -> dict:
    """确定性词表+统计兜底判定（= 旧版 score/issue 逻辑，逐字节一致）。

    filler 旧版不计入 AI 痕迹扣分（其确定性归属在 drift_score），兜底恒 False。
    """
    return {
        "connective_excess": _count(text, CONNECTIVE_WORDS) * 1000 / total >= CONN_DENSITY_LIMIT,
        "emotion_excess": _count(text, EMOTION_WORDS) * 1000 / total >= EMO_DENSITY_LIMIT,
        "filler_overuse": False,
        "zero_direct": _zero_direct(text),
    }


def _llm_ai_taste(text: str, llm) -> dict | None:
    """LLM 语义判定四维度（整章 1 次）。

    返回 dict(4 bool)=成功；dict 为空 / 任一键缺失 / 值非 bool → None（调用方回退词表）。
    空正文直接返回 None（不调 LLM）。
    """
    if not text:
        return None
    system, user = ai_taste_check_prompt(text)
    raw = llm.chat(system, user, temperature=0.2, max_tokens=300, json_mode=True)
    data = parse_json_obj(raw)
    if not isinstance(data, dict) or not data:
        return None
    out: dict = {}
    for k in _JUDGE_KEYS:
        v = data.get(k)
        if not isinstance(v, bool):
            return None
        out[k] = v
    return out


def _ai_taste_judge(text: str, llm, use_llm: bool) -> dict:
    """四维度判定门面：LLM 语义优先；失败/非结构化/异常/未接 llm/开关关闭回退词表。

    score 与 issues 共用本函数一次判定，保证整章仅 1 次 LLM 调用。
    """
    if not text:
        return {k: False for k in _JUDGE_KEYS}
    if llm is None or not use_llm:
        return _dict_judge(text, len(text))
    try:
        res = _llm_ai_taste(text, llm)
    except Exception as exc:
        logger.warning("llm ai_taste judge failed, fallback dict: %s: %s",
                       type(exc).__name__, exc)
        return _dict_judge(text, len(text))
    if res is None:
        logger.warning("llm ai_taste unstructured, fallback dict")
        return _dict_judge(text, len(text))
    return res


def _simile_hit(text: str, total: int) -> bool:
    """明喻过密（永远确定性，不换 LLM）。"""
    return text.count("像") * 1000 / total >= SIMILE_DENSITY_LIMIT


def _score_from_judge(text: str, judge: dict) -> int:
    """由共享 judge 计算 AI 痕迹分。扣分结构保持现状：conn25/emo20/simile10/zero10/排比15/雷同10。"""
    if not text:
        return 100
    total = len(text)
    score = 100
    if judge["connective_excess"]:
        score -= 25
    if judge["emotion_excess"]:
        score -= 20
    if _simile_hit(text, total):
        score -= 10
    if judge["zero_direct"]:
        score -= 10
    if _parallel_issue(text):
        score -= 15
    if _para_similar(text):
        score -= 10
    return max(0, min(100, score))


def score_ai_taste(text: str, llm=None, use_llm: bool = True) -> int:
    """AI 痕迹分 0-100（越高越自然），各维度扣分叠加。

    llm/use_llm 缺省（或失败）回退词表统计，无参调用与旧版行为一致。
    """
    return _score_from_judge(text, _ai_taste_judge(text, llm, use_llm))


#: LLM 命中维度的语义化 issue detail（不引用词表密度数字）
_DETAIL_CONN = ("连接词扎堆（然而/因此/不仅…而且/值得注意的是等书面连接词高频复用），"
                "读感像议论文转述而非网文叙事；收敛到动作/对话驱动叙事")
_DETAIL_EMO = ("抽象情绪词直白宣告连发（愤怒/震惊/无奈等直说），缺动作/微表情承载；"
               "情绪用动作+生理反应承载，点破只留给关键节点")
_DETAIL_ZERO = ("全文无一处直白点破（'他怕了'式）——情绪只演不说，读者要自己猜；"
                "铺垫到位处直接点破并紧跟动作/生理反应支撑")
_DETAIL_FILLER = ("填充词口头禅高频复现（然后/于是/只见/当即等），读感啰嗦机械；"
                  "按动作顺序直写，删掉口头禅")


def ai_taste_issues(text: str, threshold: int = 60, llm=None, use_llm: bool = True) -> list[dict]:
    """综合分低于 threshold 时返回命中维度 issue（level=中改，不阻断）。

    judge 与 score 共享一次判定（整章 1 次 LLM）；simile/排比/雷同仍确定性生成。
    """
    if not text:
        return []
    judge = _ai_taste_judge(text, llm, use_llm)
    if _score_from_judge(text, judge) >= threshold:
        return []
    total = len(text)
    issues: list[dict] = []
    if judge["connective_excess"]:
        issues.append({"type": "AI痕迹-连接词堆砌", "detail": _DETAIL_CONN, "level": "中改"})
    if judge["emotion_excess"]:
        issues.append({"type": "AI痕迹-情绪词堆砌（点破允许）", "detail": _DETAIL_EMO, "level": "中改"})
    if _simile_hit(text, total):
        simile = text.count("像")
        issues.append({
            "type": "AI痕迹-明喻过密（警示）",
            "detail": f"'像……'明喻密度 {simile * 1000 / total:.1f}/千字——白描重在传神、很少用比喻；"
                      f"收敛到 ≤2/千字，其余用动词链直写",
            "level": "中改",
        })
    if judge["zero_direct"]:
        issues.append({"type": "AI痕迹-全程演过头（零直白）", "detail": _DETAIL_ZERO, "level": "中改"})
    if judge["filler_overuse"]:
        issues.append({"type": "AI痕迹-填充词口头禅堆砌", "detail": _DETAIL_FILLER, "level": "中改"})
    if _parallel_issue(text):
        issues.append({"type": "AI痕迹-句式排比", "detail": "连续多句以相同词开头，句式对称明显，读感模板化", "level": "中改"})
    if _para_similar(text):
        issues.append({"type": "AI痕迹-段落雷同", "detail": "连续段落字数接近，段落结构模板化", "level": "中改"})
    return issues
