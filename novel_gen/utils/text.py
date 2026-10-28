# -*- coding: utf-8 -*-
"""纯文本算法工具（无 IO、无 LLM 依赖）。

承载跨模块收敛的纯文本匹配算法，作为唯一权威来源（AGENTS.md §11.2）。
当前只有最长公共子串长度；后续同类纯文本算法收敛到此处。
"""
from __future__ import annotations


def longest_common_substr_len(a: str, b: str) -> int:
    """两文本最长公共子串长度（≥2 字视为覆盖命中；O(n*m) 小文本直算）。

    以短串作窗口、长串作母串，从最长窗口向下探测是否存在连续公共子串；
    找不到（或任一为空）返回 0。与「最长公共子序列（LCS）」不同，这里要求
    字符连续——注意勿与 beats.py 的 `_lcs_len`（列表子序列 DP）混淆。
    """
    if not a or not b:
        return 0
    if len(a) > len(b):
        a, b = b, a
    for w in range(len(a), 1, -1):
        for i in range(len(a) - w + 1):
            if a[i:i + w] in b:
                return w
    return 0
