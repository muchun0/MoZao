# -*- coding: utf-8 -*-
"""双锚风格锚定（REQ-双锚风格锚定）：个人旧稿锚 + 拆书库题材锚的纯函数提取/组装。

设计约束：
- novel_gen 不依赖 webapp：本模块只处理纯数据结构（list/dict/str），webapp 侧负责
  查拆书库（MySQL）并调用 build_teardown_anchor 格式化后写入 store 文件。
- 全部为纯函数，可离线单测（tests/test_style_anchor.py）。
- 锚是弱约束（只借鉴语感与手法，严禁抄袭内容）：包装块明示"严禁抄袭具体内容与情节"。
"""
from __future__ import annotations

__all__ = ["extract_personal_anchor", "build_teardown_anchor", "wrap_style_anchor",
           "PERSONAL_ANCHOR_HEADER", "TEARDOWN_ANCHOR_HEADER"]

#: 注入块头（templates 与 webapp router 共用；格式与 _style_reference 一致）
PERSONAL_ANCHOR_HEADER = "【本人旧稿风格参考（只模仿选词、句式、节奏与语感，严禁抄袭具体内容与情节）】"
TEARDOWN_ANCHOR_HEADER = "【同题材爆款文风锚（只借鉴手法与语感，严禁抄袭内容与情节）】"

#: 个人锚单段最大字符数
_PER_SEG = 250
#: 个人锚总长上限
_PERSONAL_MAX = 600
#: 题材锚总长上限
_TEARDOWN_MAX = 800
#: 题材锚中原文片段引用：最多段数 × 单段字数（拆解时已限 100 字/段）
_EXCERPT_MAX_SEGS = 2
_EXCERPT_SEG_LEN = 100


def _split_segments(text: str, per_seg: int) -> list[str]:
    """按段落切分正文，保留 30~per_seg 字的有内容段；超长段在句号边界截断。"""
    segs: list[str] = []
    for raw in (text or "").split("\n"):
        part = raw.strip()
        if len(part) < 30:
            continue  # 跳过过短段（标题/面板行/零碎行）
        if len(part) > per_seg:
            cut = part[:per_seg]
            # 尽量在句号/叹号/问号后切断，避免截在半句
            idx = -1
            for c in "。！？":
                i = cut.rfind(c)
                if i > idx:
                    idx = i
            if idx >= per_seg * 0.6:
                part = cut[: idx + 1]
            else:
                part = cut
        segs.append(part)
    return segs


def extract_personal_anchor(chapters, max_total: int = _PERSONAL_MAX,
                            per_seg: int = _PER_SEG) -> str:
    """从用户人工编辑过的章节抽取个人风格锚文本。

    chapters: 可迭代对象，元素需带 user_edited(bool) 与 content(str)
    （实际为 novel_gen.store.schemas.Chapter；用鸭子类型便于纯函数测试）。
    过滤 user_edited=True 的章节，每章取 1~2 段，累计不超过 max_total 字。
    无人工编辑章返回空串（调用方不注入）。
    """
    edited = [c for c in (chapters or []) if getattr(c, "user_edited", False)]
    if not edited:
        return ""
    picked: list[str] = []
    total = 0
    for ch in edited:
        segs = _split_segments(getattr(ch, "content", "") or "", per_seg)
        for seg in segs[:2]:  # 每章最多 2 段
            if total + len(seg) > max_total:
                return "\n\n".join(picked)
            picked.append(seg)
            total += len(seg)
    return "\n\n".join(picked)


def build_teardown_anchor(styles, takeaways, excerpts,
                          limit: int = _TEARDOWN_MAX,
                          excerpt_max_segs: int = _EXCERPT_MAX_SEGS,
                          excerpt_seg_len: int = _EXCERPT_SEG_LEN) -> str:
    """把拆书条目 content 的文风相关字段组装为题材锚纯文本（webapp 侧查库后调用）。

    styles/takeaways/excerpts 均为可迭代的字符串；缺失传 None/[] 即跳过。
    - styles：文风语言特点，每项一句（如『短句快节奏+口语化吐槽』）
    - takeaways：可直接借用的写作手法清单
    - excerpts：原文片段引用（拆解时已限 ≤100 字/段），此处再限段数与长度，
      只作语感样本、不得整段照抄
    """
    blocks: list[str] = []
    for label, items in (("文风特点", styles), ("可借鉴手法", takeaways)):
        clean = [str(x).strip() for x in (items or []) if x and str(x).strip()]
        if clean:
            blocks.append(f"{label}：\n" + "\n".join(f"· {x}" for x in clean))
    exc = [str(x).strip() for x in (excerpts or []) if x and str(x).strip()][:excerpt_max_segs]
    if exc:
        blocks.append("原文片段（仅作语感样本，严禁整段照抄）：\n"
                      + "\n".join(f"· {x[:excerpt_seg_len]}" for x in exc))
    if not blocks:
        return ""
    text = "\n\n".join(blocks)
    return text[:limit]


def wrap_style_anchor(header: str, text: str, max_chars: int = _TEARDOWN_MAX) -> str:
    """包装风格锚块：统一头部 + 防抄袭声明，限制长度；空文本返回空串。"""
    text = (text or "").strip()
    if not text:
        return ""
    return f"{header}\n{text[:max_chars]}"
