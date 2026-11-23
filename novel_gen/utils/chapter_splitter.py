# -*- coding: utf-8 -*-
"""用户手写正文切分器：把长文本按章节标题切分为章节列表。

支持标题形态（行首匹配，标题取整行）：
- 中文：第一章 雨夜 / 第十二回 决战 / 第三节 / 序章 / 楔子 / 尾声 / 终章 / 番外
- 英文：Chapter 3 The Return / CHAPTER IV
- 数字：2. Next / 3、More
识别不到任何章节标题时，整段作为一个元素（title 为空），由上层决定如何处理。
"""
from __future__ import annotations

import re

_CN_NUM = r"[0-9０-９一二三四五六七八九十百千零两]"
# 中文章节标题行（含特殊章节词）
_CN_HEAD = re.compile(
    rf"^\s*((?:第\s*{_CN_NUM}+\s*[章回节卷部篇])|(?:序章|楔子|尾声|终章|番外|后记|开篇|引子))"
)
_EN_HEAD = re.compile(r"^\s*chapter\s+[0-9ivxlcdm]+", re.I)
_DIGIT_HEAD = re.compile(r"^\s*\d{1,3}[\s、.．:：\-—]")
_DIGIT_ONLY = re.compile(r"^\s*\d{1,3}\s*$")
# 防误判：正文行以"第N章"等字样开头但明显是叙述（后面紧跟长句）——v1 不处理，靠预览确认兜底


_MD_PREFIX = re.compile(r"^\s*#{1,6}\s*")
_BOLD_WRAP = re.compile(r"^\s*(\*\*|__)(.+?)\1\s*$")


def _match_head(line: str):
    """命中返回整行（去 Markdown 前缀与加粗标记）作为标题；否则 None。"""
    s = line.strip()
    if not s:
        return None
    # Markdown 标题（## 第1章 雨夜）与整行加粗（**第1章 雨夜**）都算章节标题
    s = _MD_PREFIX.sub("", s).strip()
    m = _BOLD_WRAP.match(s)
    if m:
        s = m.group(2).strip()
    if _CN_HEAD.match(s) or _EN_HEAD.match(s):
        return s
    if not _DIGIT_ONLY.match(s) and _DIGIT_HEAD.match(s):
        return s
    return None


def split_chapters(text: str) -> list[dict]:
    """按章节标题切分。返回 [{"title": str, "content": str}, ...]（按出现顺序）。"""
    if not text or not text.strip():
        return []
    chapters: list[dict] = []
    cur_title = ""
    cur_lines: list[str] = []

    def flush():
        if cur_title or any(l.strip() for l in cur_lines):
            content = "\n".join(cur_lines).strip()
            if content or cur_title:
                chapters.append({"title": cur_title, "content": content})

    for line in text.splitlines():
        head = _match_head(line)
        if head is not None:
            flush()
            cur_title = head
            cur_lines = []  # 标题行不进入正文
        else:
            cur_lines.append(line)
    flush()
    # 只有标题没有正文的章节也要保留：导入大纲时这是「待写章节」，丢弃会导致
    # 「上传了大纲却只识别出一半章节」；仅剔除标题与正文都为空的占位。
    chapters = [c for c in chapters if c["title"].strip() or c["content"].strip()]
    if not chapters:
        return [{"title": "", "content": text.strip()}]
    return chapters


def summarize_preview(chapters: list[dict], preview_len: int = 100) -> list[dict]:
    """为切分结果生成前端预览（标题/字数/前 N 字）。"""
    return [
        {"no": i + 1, "title": c["title"] or f"第{i + 1}节", "chars": len(c["content"]),
         "preview": c["content"][:preview_len]}
        for i, c in enumerate(chapters)
    ]
