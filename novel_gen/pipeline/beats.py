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
- _beat_cycle_issues：整章正文 → 同一长度 ≥4 节拍子序列出现 ≥2 次（质检情节级重演检测）
"""
from __future__ import annotations

import re

#: 情节节拍词典：关键行为词 → 归一化节拍（长词优先匹配，覆盖已实证的重演循环节拍）
_BEAT_DICT = {
    "言出法随": "失控", "重新站起来": "复活", "可死次数": "面板", "别装了": "戳穿",
    "扑上去": "挡死", "冲上去": "挡死", "挡在": "挡死", "护在": "挡死",
    "盘问": "盘问", "逼问": "盘问", "质问": "盘问", "试探": "盘问",
    "戳穿": "戳穿", "识破": "戳穿", "看穿": "戳穿",
    "失控": "失控", "白光": "失控", "炸开": "失控", "爆裂": "失控",
    "咽气": "死亡", "毙命": "死亡", "断气": "死亡", "被撕": "死亡", "贯穿": "死亡",
    "死亡": "死亡", "倒下": "死亡",
    "复活": "复活", "醒来": "复活", "睁眼": "复活", "爬起": "复活", "回神": "复活",
    "面板": "面板", "蚀化": "面板", "蚀痕": "面板",
    "对峙": "对峙", "别动": "对峙", "盯着": "对峙", "冷笑": "对峙", "命大": "对峙",
    "包扎": "包扎", "按住": "包扎", "撕布": "包扎", "止血": "包扎", "换药": "包扎",
    "装晕": "装晕", "装死": "装晕",
    "令牌": "留牌", "铜牌": "留牌", "腰牌": "留牌", "收下": "留牌", "留牌": "留牌",
    "离开": "离场", "跟上": "离场", "消失": "离场", "转身": "离场", "离去": "离场",
    "结拜": "结拜", "红绳": "结拜",
    "三天后": "约定", "碰碗": "约定", "约在": "约定",
    "诊脉": "诊脉", "把脉": "诊脉", "搭脉": "诊脉",
    "药包": "药包", "递药": "药包",
    "暗格": "暗格", "夹层": "暗格", "地图": "地图", "翻尸": "翻尸",
    "石阶": "石阶", "石门": "石阶",
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


def _beat_cycle_issues(content: str) -> list[dict]:
    """质检 2.12 情节级重演检测（确定性）：整章正文内同一长度 ≥4 节拍序列
    （非重叠）出现 ≥2 次 = 同事件多版本重演（换措辞循环，文本 n-gram 漏检）。

    判定用「子序列」而非「连续子串」：换措辞版本可能插入少量节拍
    （如 CYCLE_B 在盘问与戳穿间插入'对峙'），连续匹配会漏检——
    只要后续窗口的 LCS ≥ 0.7×窗口长度即判同一循环再现。
    """
    seq = _extract_beat_sequence(content)
    if len(seq) < _CYCLE_MIN * 2:
        return []
    n = len(seq)
    max_win = min(_CYCLE_MIN + 4, n)
    for i in range(n - _CYCLE_MIN + 1):
        for L in range(_CYCLE_MIN, max_win - i + 1):
            sub = seq[i:i + L]
            for j in range(i + L, n - 1):
                win = seq[j:min(j + L + 4, n)]
                if len(win) < _CYCLE_MIN:
                    continue
                if _lcs_len(sub, win) >= max(_CYCLE_MIN, int(L * 0.7)):
                    return [{
                        "type": "情节-同事件循环重演",
                        "detail": f"同一情节节拍序列「{'→'.join(sub)}」在章内出现 ≥2 次，"
                                  "疑似同事件多版本重演（换措辞循环，文本相似度漏检）",
                        "level": "大改",
                    }]
    return []
