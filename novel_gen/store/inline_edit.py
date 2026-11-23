# -*- coding: utf-8 -*-
"""内联写作的锚点与写通道（R25 基座 §4.3 / §4.7）。

职责边界：
1. `normalize_text` / `content_hash`：全系统唯一的偏移与哈希口径。前端只做
   `\\r\\n→\\n`、`\\r→\\n` 两步；服务端收到后按同一函数重算并比对，杜绝插入错位。
2. `apply_inline_text`：R25 内联采纳与 R28 小墨建议共用的唯一正文写通道。
   只做「按锚点拼接 + 版本化提交」，不写个人风格锚、不写 good 账本、不置
   `user_edited`、不重算摘要（只置 `summary_stale`），避免把 AI 文本学成用户文风。

本模块属存储层：不依赖 Web / FastAPI / SQL，异常由调用方映射为 HTTP 状态码。
"""
from __future__ import annotations

import hashlib

from .schemas import Chapter
from .versioned import _project_lock
from .wordcount import count_words

__all__ = [
    "InlineEditError", "BadAnchorError", "StaleBaseError",
    "normalize_text", "content_hash", "current_version", "apply_inline_text",
]


class InlineEditError(Exception):
    """内联写通道的业务异常基类（调用方负责映射 HTTP）。"""

    status_code = 400
    code = "INLINE_EDIT_ERROR"

    def __init__(self, detail: str, *, code: str = "", status_code: int = 0):
        super().__init__(detail)
        self.detail = detail
        self.code = code or self.code
        self.status_code = status_code or self.status_code


class BadAnchorError(InlineEditError):
    status_code = 400
    code = "BAD_ANCHOR"


class StaleBaseError(InlineEditError):
    status_code = 409
    code = "STALE_BASE_KEEP_CANDIDATES"


def normalize_text(text: str) -> str:
    """统一换行：只做 CRLF/CR → LF，不 trim、不改段落分隔。"""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def content_hash(text: str) -> str:
    """`sha1:` + sha1(normalize(text))，与前端 `base_hash` 同一口径。"""
    return "sha1:" + hashlib.sha1(normalize_text(text).encode("utf-8")).hexdigest()


def current_version(store, ch_no: int) -> int:
    """当前选中版本号；无版本索引（存量单版本章节）按 0 处理。"""
    rec = (store.load_chapter_versions() or {}).get(str(int(ch_no))) or {}
    selected = rec.get("selected")
    return int(selected) if isinstance(selected, int) else 0


def apply_inline_text(store, ch_no: int, start: int, end: int, text: str, *,
                      base_version: int, base_hash: str,
                      source: str = "inline",
                      run_id: str = "",
                      label: str = "inline:adopt") -> dict:
    """按 `[start, end)` 左闭右开把 `text` 写入章节并提交一个新版本。

    Returns:
        {version, words, chapter_words, selected, created}
    Raises:
        BadAnchorError：章节不存在 / 区间越界 / 非法参数。
        StaleBaseError：`base_version` 或 `base_hash` 与当前正文不一致（期间被切版本/
            重生成/另一标签页修改），此时不写盘、不扣费。
    """
    ch_no = int(ch_no)
    # 读-校验-写必须在同一把项目锁内（RLock 与 versioned.commit 重入），
    # 否则两个标签页并发采纳会各自基于旧版本追加，产生两个「v2」结果（P0-12）。
    with _project_lock(store.dir):
        return _apply_locked(
            store, ch_no, start, end, text,
            base_version=base_version, base_hash=base_hash,
            source=source, label=label)


def _apply_locked(store, ch_no: int, start, end, text, *, base_version: int,
                  base_hash: str, source: str, label: str) -> dict:
    chapter = store.load_chapter(ch_no)
    if chapter is None:
        raise BadAnchorError(f"章节 {ch_no} 未生成", code="CHAPTER_NOT_FOUND", status_code=404)

    cur = normalize_text(chapter.content)
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        raise BadAnchorError("锚点不是整数")
    if not (0 <= start <= end <= len(cur)):
        raise BadAnchorError(f"锚点越界：0 ≤ start ≤ end ≤ {len(cur)}")

    # 乐观锁：版本号与内容哈希双校验（任一不符都视为正文已变化）
    cur_v = current_version(store, ch_no)
    if int(base_version) != cur_v:
        raise StaleBaseError(f"正文版本已变化（当前 v{cur_v}），请重新定位")
    if not base_hash or content_hash(cur) != str(base_hash):
        raise StaleBaseError("正文内容已变化，请重新定位")

    new_text = cur[:start] + str(text or "") + cur[end:]
    # 保留标题/摘要/goal/user_edited；摘要不重算，仅标记落后（由后续生成或显式刷新处理）
    updated = Chapter(
        no=chapter.no,
        title=chapter.title,
        content=new_text,
        summary=chapter.summary,
        goal=chapter.goal,
        source=source,
        user_edited=chapter.user_edited,
        summary_stale=True,
    )
    version = store.save_chapter(updated, source=source, promote=True, label=label)
    created = version is not None
    return {
        "version": int(version) if created else current_version(store, ch_no),
        "words": count_words("chapter", {"content": str(text or "")}),
        "chapter_words": count_words("chapter", {"content": new_text}),
        "selected": True,
        "created": created,
    }
