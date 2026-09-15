# -*- coding: utf-8 -*-
"""
语句重复度检测：统计最近 N 章的高频词和高频 3-gram，
超过阈值的加入"禁用词表"，注入下一章 prompt。

轻量实现：collections.Counter，不调 LLM。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ..config import NovelConfig
from ..store.project import ProjectStore

# 阈值：某词/3-gram 在最近10章出现 >5次/万字 才禁用
THRESHOLD_PER_10K = 5


class RepetitionDetector:
    def __init__(self, cfg: NovelConfig, store: ProjectStore):
        self.cfg = cfg
        self.store = store

    def _words(self, text: str) -> list[str]:
        """提取中文词（简单按2-4字滑窗，不做分词）。"""
        # 去掉标点空白
        cleaned = re.sub(r"[\s\W]+", " ", text)
        words = cleaned.split()
        return [w for w in words if len(w) >= 2]

    def _trigrams(self, text: str) -> list[str]:
        """提取中文 3-gram。"""
        chars = re.sub(r"[^\u4e00-\u9fa5]", "", text)
        return [chars[i:i+3] for i in range(len(chars) - 2)]

    def detect(self, up_to_chapter: int, window: int = 10) -> list[str]:
        """统计最近 window 章的高频重复项，返回禁用词表。"""
        chapters = self.store.list_chapters()
        recent = [c for c in chapters if c.no < up_to_chapter][-window:]
        if not recent:
            return []

        all_words = []
        all_trigrams = []
        total_chars = 0
        for ch in recent:
            text = ch.content or ""
            total_chars += len(text)
            all_words.extend(self._words(text))
            all_trigrams.extend(self._trigrams(text))

        if total_chars < 1000:
            return []

        per_10k = total_chars / 10000
        threshold = max(3, int(THRESHOLD_PER_10K * per_10k))

        word_counts = Counter(all_words)
        tri_counts = Counter(all_trigrams)

        banned = []
        for word, cnt in word_counts.most_common(30):
            if cnt >= threshold and len(word) >= 2:
                banned.append(word)
        for tri, cnt in tri_counts.most_common(30):
            if cnt >= threshold * 1.5:  # 3-gram 阈值更高
                banned.append(tri)

        # 保存到 repetition.json
        path = self.store.dir / "repetition.json"
        path.write_text(json.dumps({
            "up_to_chapter": up_to_chapter,
            "total_chars": total_chars,
            "threshold": threshold,
            "banned": banned,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

        return banned[:20]  # 只取前20个，避免 prompt 过长

    def banned_hint(self, up_to_chapter: int) -> str:
        """返回注入 prompt 的禁用提示（空则返回空串）。"""
        banned = self.detect(up_to_chapter)
        if not banned:
            return ""
        return (
            "【避免重复】以下词汇/句式在最近章节中出现过于频繁，本章请换用其他表达：\n"
            + "、".join(banned)
        )
