# -*- coding: utf-8 -*-
"""情节节拍序列（正文级重演检测，2026-10-09）

REQ-scene-plan-align §7.3 措施3/5：把正文按关键行为词归一化为「节拍序列」，
同一完整情节循环（盘问→戳穿→失控→挡死→死亡→复活→面板→留牌→离场）换措辞重演时，
序列级指纹重合——文本 n-gram、相邻场景全文相似度、救援动词防线均漏检（详见
REQ-scene-plan-align §7.2 根因 C/D/E）。

- _extract_beat_sequence：正文 → 节拍序列（长词优先、去连续重复、<2 拍返回空）
- _lcs_len：最长公共子序列长度（序列很短，O(n*m) DP 足够）
- _beats_rewrite_detected：新场景节拍序列 vs 已完成场景节拍序列 → 是否重演
  （LCS ≥ 4 且 LCS/较短序列 ≥ 0.7：完整循环重演判据；正常单事件 2-3 拍不触发）
- _beat_cycle_issues：整章正文 → 同一长度 ≥4 节拍子序列出现 ≥2 次且共享 ≥2 种强情节拍（质检情节级重演检测）
"""
from __future__ import annotations

import re

#: 情节节拍词典：关键行为词 → 归一化节拍（长词优先匹配）。
#: 通用化（2026-10-27）：仅保留通用行为词拍（盘问/戳穿/失控/死亡/复活/对峙/包扎/
#: 装晕/离场/约定）；书级实体与书形触发词（言出法随/可死次数/面板/蚀痕/蚀化/挡死/
#: 留牌/铜牌/结拜/诊脉/药包/暗格/翻尸/石阶/白光/炸开/爆裂/被撕/贯穿/命大等）已移除，
#: 换书后不再依赖本书词表。书级情节拍待后续从 World 人物/事件卡动态派生。
_BEAT_DICT = {
    "盘问": "盘问", "逼问": "盘问", "质问": "盘问", "试探": "盘问",
    "戳穿": "戳穿", "识破": "戳穿", "看穿": "戳穿",
    "失控": "失控",
    "咽气": "死亡", "毙命": "死亡", "断气": "死亡", "死亡": "死亡",
    # 注意：'倒下' 不映射死亡——2026-10-15 ch3 实证影子/障碍物'倒下'被误抽为
    # 死亡拍，污染节拍序列凑出假循环（死亡有咽气/毙命/断气 3 词兜底）
    "复活": "复活", "醒来": "复活", "睁开眼": "复活", "睁眼": "复活", "爬起": "复活", "回神": "复活",
    "对峙": "对峙", "别动": "对峙", "盯着": "对峙", "冷笑": "对峙",
    "包扎": "包扎", "按住": "包扎", "撕布": "包扎", "止血": "包扎", "换药": "包扎",
    "装晕": "装晕", "装死": "装晕",
    "离开": "离场", "跟上": "离场", "消失": "离场", "转身": "离场", "离去": "离场",
    "约在": "约定",
    "离场": "离场",
}
_KEYS = sorted(_BEAT_DICT, key=len, reverse=True)
_PATTERN = re.compile("|".join(re.escape(k) for k in _KEYS))

#: 重演判定阈值：LCS ≥ 4 且 LCS/较短序列 ≥ 0.7
#: （完整循环重演判据；正常单事件场景 2-3 拍不触发，机制允许的多次死亡-复活
#: 因序列短不误判；宁多触发重试，不漏放重演——重试由后续 2 次累计再回退整章兜底）
_BEAT_LCS_MIN = 4
_BEAT_LCS_RATIO = 0.7
#: 质检情节级检测：整章内同一长度 ≥4 节拍子序列出现 ≥2 次（非重叠）判重演
_CYCLE_MIN = 4
#: 章内循环判定：两个循环窗口「共享」的节拍中，强情节拍种数 ≥ 2 才判重演。
#: 治 2026-10-15 全书复测误报：高频骨架拍（对峙/离场/死亡/复活）在正常章节
#: 多次出现（反复对峙/离场/同型死亡），凑成 ≥4 拍子序列重复被误判为
#: "同事件循环重演"（ch2/ch3/ch9/ch11 全误报，正文通读无复读）；而 v7/v8 实证的
#: 章内重演（盘问→戳穿→失控→死亡→复活完整循环）两个版本共享多种强情节拍。
#: 强情节拍 = 剧情转折/特殊事件拍（非高频骨架拍），循环必须两次都含 ≥2 种才算
#: "同一事件换措辞重演"；仅骨架拍组合重复（不同事件共享同一行为词）不判。
_CYCLE_STRONG_BEATS = {
    "盘问", "戳穿", "失控", "死亡", "复活", "装晕",
}
_CYCLE_STRONG_MIN = 2
#: 循环窗口最大长度差：同一事件链重演的两个窗口长度相近（差 ≤2）
_CYCLE_LEN_DELTA = 2


def _extract_beat_sequence(text: str) -> list[str]:
    """正文 → 情节节拍序列：按关键行为词首次出现顺序归一化，去连续重复，<2 拍返回空。"""
    if not text:
        return []
    seq: list[str] = []
    for m in _PATTERN.finditer(text or ""):
        b = _BEAT_DICT[m.group(0)]
        if not seq or seq[-1] != b:
            seq.append(b)
    return seq if len(seq) >= 2 else []


def _lcs_len(a: list[str], b: list[str]) -> int:
    """最长公共子序列长度（O(n*m) DP，序列很短足够）。"""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0
    dp = [0] * (n + 1)
    for i in range(1, m + 1):
        prev = 0
        for j in range(1, n + 1):
            tmp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev + 1
            else:
                dp[j] = max(dp[j], dp[j - 1])
            prev = tmp
    return dp[n]


def _beats_rewrite_detected(seq: list[str], done_seqs: list[list[str]]) -> bool:
    """新场景节拍序列 vs 已完成场景节拍序列：任一 LCS ≥ 阈值且占比达标 → 完整循环重演。"""
    if not seq or len(seq) < _BEAT_LCS_MIN:
        return False
    for d in done_seqs:
        if not d or len(d) < _BEAT_LCS_MIN:
            continue
        l = _lcs_len(seq, d)
        if l >= _BEAT_LCS_MIN and l / min(len(seq), len(d)) >= _BEAT_LCS_RATIO:
            return True
    return False


#: 局部子序列重合阈值：已完成序列中任一 4-7 拍子序列被新场景复现 ≥0.8 即判局部重演
#: （重试稿可能压缩重演段/插入新节拍使整序列 LCS 不达标而逃逸——子序列级兜底）
_BEAT_LOCAL_MIN = 4
_BEAT_LOCAL_RATIO = 0.8


def _beats_local_rewrite_detected(seq: list[str], done_seqs: list[list[str]]) -> bool:
    """局部子序列重合检测：已完成场景节拍序列中任一长度 4-7 的子序列，
    在新场景节拍序列中基本复现（LCS ≥ max(4, 0.8×子序列长)）→ 局部重演。

    与 _beats_rewrite_detected 互补：整序列 LCS 要求占比 ≥0.7，重试稿若把
    重演段压缩（子序列仍完整但整体占比下降）或插入新节拍（稀释 LCS 占比）
    即逃逸；本函数按子序列粒度复核，命中即重演。
    """
    if not seq or len(seq) < _BEAT_LOCAL_MIN:
        return False
    for d in done_seqs:
        if not d or len(d) < _BEAT_LOCAL_MIN:
            continue
        nd = len(d)
        for start in range(nd - _BEAT_LOCAL_MIN + 1):
            for L in range(_BEAT_LOCAL_MIN, min(_BEAT_LOCAL_MIN + 4, nd - start) + 1):
                sub = d[start:start + L]
                if _lcs_len(sub, seq) >= max(_BEAT_LOCAL_MIN, int(L * _BEAT_LOCAL_RATIO)):
                    return True
    return False


# ---------- P0-10 跨场景同构场景检测（2026-10-10） ----------
# v7 第 5 章「瑶光界药铺」三重拼接实证：同一「诊脉→药包→离场」核心节拍写了 3 遍，
# 但单遍节拍仅 3 拍 < _BEAT_LCS_MIN(4)，整序列/局部子序列检测全部漏检；且三遍
# desc（查药粉/诊脉/探脉枕）相似度 <0.55 也逃过拆场景预筛。本函数按「核心节拍 +
# 非通用拍数量」判跨场景同构——放宽到 3 拍，但公共节拍中必须有 ≥2 拍是
# 场景特异性词（非通用拍，如盘问/戳穿/失控/包扎/约定/装晕），防「对峙→离场」式通用拍误判。

#: 通用节拍：几乎所有场景都会出现的骨架拍（仅凭它们不得判同构）
_SCENE_GENERIC_BEATS = {"离场", "对峙", "复活", "死亡", "盘问", "失控"}
#: 跨场景同构判据：公共子序列 LCS ≥ 3 且占比 ≥0.6 且非通用拍 ≥2
#: 2026-10-10 P0-14：占比 0.8→0.6——v8 第 1 章实证「断墙遇灰卒」三重拼接中，
#: 场景 2/3 只重复场景 1 的核心 3~4 拍（面板→留牌→小九→离场），ratio≈0.6~0.75，
#: 旧 0.8 阈值漏检（v7 第 5 章是完整 3 拍全同 ratio=1.0 才命中）。放宽后靠
#: 「非通用拍 ≥2」防误判（对峙→离场式通用拍组合仍不命中）。
_SCENE_ISO_MIN = 3
_SCENE_ISO_RATIO = 0.6
_SCENE_ISO_SPECIFIC_MIN = 2
#: 参与比对的历史场景节拍序列最短长度（=MIN：3 拍药铺同构仍可命中；防过短序列误判）
_SCENE_ISO_LEDGER_MIN = 3


def _beats_scene_isomorphic(seq: list[str], scene_ledger: list[list[str]]) -> bool:
    """新场景节拍序列 vs 历史场景序列全集（跨场景）：任一公共子序列达到
    「3 拍 + 占比 0.6 + ≥2 个非通用拍」即判同构场景（同一场景换措辞重复写）。

    治 v7 第 5 章药铺三重拼接：三遍诊脉场景节拍均为「诊脉→药包→离场」，
    LCS=3、占比=1.0、非通用拍=诊脉+药包=2 → 命中；两个普通场景各含「对峙→离场」
    → 非通用拍=0 → 不误判。场景防线与整章回退路径复用。
    P0-14 放宽：v8 第 1 章三重拼接的场景 2/3（只重复核心 3~4 拍、占比 0.6~0.75）
    旧阈值漏检，放宽占比至 0.6 后可拦截。
    """
    if not seq or len(seq) < _SCENE_ISO_MIN:
        return False
    for d in scene_ledger:
        if not d or len(d) < _SCENE_ISO_LEDGER_MIN:
            continue
        if _lcs_len(seq, d) < _SCENE_ISO_MIN:
            continue
        # 公共子序列 = 两序列中同时出现的节拍（保序集合交集即可，无需 DP 回溯）
        common = [b for b in seq if b in set(d)]
        if len(common) < _SCENE_ISO_MIN:
            continue
        # 公共子序列占新场景序列的比例（近似 LCS 占比，够用）
        ratio = len(common) / min(len(seq), len(d))
        specific = sum(1 for b in common if b not in _SCENE_GENERIC_BEATS)
        if ratio >= _SCENE_ISO_RATIO and specific >= _SCENE_ISO_SPECIFIC_MIN:
            return True
    return False


def _beat_cycle_issues(content: str) -> list[dict]:
    """质检 2.12 情节级重演检测（确定性）：整章正文内同一长度 ≥4 节拍序列
    出现 ≥2 次 = 同事件多版本重演（换措辞循环，文本 n-gram 漏检）。

    双判据（2026-10-15 全书复测校准）：
    1. 连续子序列判据：同一长度 ≥4 的连续节拍串出现 ≥2 次且串内含 ≥2 种
       强情节拍（完整循环等长再现；换措辞在序列层即相同序列——CYCLE_A/B、
       test_inspector 面板死亡复活×6 均命中；ch2 对峙留牌×2 无强拍被拦）。
    2. 窗口判据（容忍换措辞插入 1-2 拍）：LCS ≥ 0.8×窗口长 + 两窗口长度差
       ≤2 + 共享强情节拍 ≥2。治 ch11 实证误报：两个不同失控事件被骨架拍
       （面板/对峙）拼进 6 拍窗口 vs 后续 10 拍窗口——长度差 4、LCS 占比
       0.67，双判据均拦。
    """
    seq = _extract_beat_sequence(content)
    if len(seq) < _CYCLE_MIN * 2:
        return []
    n = len(seq)

    # 判据1：连续子序列重复（完整循环等长再现）
    for L in range(_CYCLE_MIN, min(_CYCLE_MIN + 5, n) + 1):
        seen: dict[tuple, int] = {}
        for i in range(n - L + 1):
            t = tuple(seq[i:i + L])
            seen[t] = seen.get(t, 0) + 1
        for t, c in seen.items():
            if c < 2:
                continue
            strong = sum(1 for b in set(t) if b in _CYCLE_STRONG_BEATS)
            if strong < _CYCLE_STRONG_MIN:
                continue
            return [{
                "type": "情节-同事件循环重演",
                "detail": f"同一情节节拍序列「{'→'.join(t)}」在章内出现 ≥2 次，"
                          "疑似同事件多版本重演（换措辞循环，文本相似度漏检）",
                "level": "大改",
            }]

    # 判据2：容忍少量插入的窗口匹配（换措辞版本可能插入 1-2 拍）
    max_win = min(_CYCLE_MIN + 4, n)
    for i in range(n - _CYCLE_MIN + 1):
        for L in range(_CYCLE_MIN, max_win - i + 1):
            sub = seq[i:i + L]
            for j in range(i + L, n - 1):
                win = seq[j:min(j + L + 4, n)]
                if len(win) < _CYCLE_MIN:
                    continue
                if abs(len(sub) - len(win)) > _CYCLE_LEN_DELTA:
                    continue
                if _lcs_len(sub, win) < max(_CYCLE_MIN, int(L * 0.8)):
                    continue
                # 共享节拍 = 两个窗口同时出现的节拍（保序交集，近似 LCS 内容）
                shared = [b for b in sub if b in set(win)]
                strong = sum(1 for b in set(shared) if b in _CYCLE_STRONG_BEATS)
                if strong < _CYCLE_STRONG_MIN:
                    # 仅高频骨架拍重复（不同事件共享行为词），非同一事件换措辞重演
                    continue
                return [{
                    "type": "情节-同事件循环重演",
                    "detail": f"同一情节节拍序列「{'→'.join(sub)}」在章内出现 ≥2 次，"
                              "疑似同事件多版本重演（换措辞循环，文本相似度漏检）",
                    "level": "大改",
                }]
    return []
