# -*- coding: utf-8 -*-
"""
单章改稿器：四步流程（通读定位→分类判定→分层修改→回读校验）。

只改小改问题；中改以上只记录不自动改。
改稿失败或引入新问题时保留原版。
"""
from __future__ import annotations

import json
import logging

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.project import ProjectStore
from .templates import (
    revise_locate_prompt,
    revise_apply_prompt,
    revise_verify_prompt,
)

logger = logging.getLogger(__name__)


class ChapterReviser:
    """单章自动改稿。"""

    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 role_prompt: str = ""):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        return f"{self.role_prompt}\n\n{system}" if self.role_prompt else system

    def revise(self, chapter_no: int, chapter_title: str, chapter_goal: str,
               content: str, silent: bool = False) -> str:
        """对本章正文跑四步改稿，返回改后正文（失败时返回原文）。"""
        try:
            return self._revise_impl(chapter_no, chapter_title, chapter_goal, content, silent)
        except Exception as e:
            logger.warning("改稿失败（保留原版）: 第%d章 %s", chapter_no, e)
            return content

    def _revise_impl(self, chapter_no, chapter_title, chapter_goal, content, silent):
        # 步骤1：通读定位
        sys_l, user_l = revise_locate_prompt(
            self.cfg, chapter_no, chapter_title, chapter_goal, content
        )
        sys_l = self._sys(sys_l)
        raw = self.llm.chat(sys_l, user_l, temperature=0.3, max_tokens=1500)
        locate = self._parse_json(raw)
        issues = locate.get("issues", [])
        if not issues:
            if not silent:
                print(f"  [改稿] 第{chapter_no}章未发现需要修改的问题")
            self._save_review(chapter_no, issues, content, content)
            return content

        # 步骤2：分类（代码层）
        small = [it for it in issues if it.get("level", "小改") == "小改"]
        medium_plus = [it for it in issues if it.get("level", "小改") != "小改"]

        # 步骤3：只改小改
        if not small:
            if not silent:
                print(f"  [改稿] 第{chapter_no}章 {len(medium_plus)} 个中改以上问题，记录但不自动改")
            self._save_review(chapter_no, issues, content, content)
            return content

        sys_a, user_a = revise_apply_prompt(
            self.cfg, chapter_no, chapter_title, content, small
        )
        sys_a = self._sys(sys_a)
        revised = self.llm.chat(
            sys_a, user_a,
            temperature=0.7, max_tokens=8000,
            frequency_penalty=0.2, presence_penalty=0.1,
        )
        revised = revised.strip()

        # 步骤4：回读校验
        sys_v, user_v = revise_verify_prompt(
            self.cfg, chapter_no, revised, small
        )
        sys_v = self._sys(sys_v)
        raw_v = self.llm.chat(sys_v, user_v, temperature=0.3, max_tokens=500)
        verify = self._parse_json(raw_v)

        if verify.get("ok") and not verify.get("new_issues"):
            if not silent:
                print(f"  [改稿] 第{chapter_no}章已自动修改 {len(small)} 个小问题")
            self._save_review(chapter_no, issues, content, revised)
            return revised
        else:
            if not silent:
                print(f"  [改稿] 第{chapter_no}章改后引入新问题，保留原版")
            self._save_review(chapter_no, issues, content, content)
            return content

    def _parse_json(self, text: str) -> dict:
        t = text.strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        try:
            return json.loads(t)
        except Exception:
            return {"issues": []}

    def _save_review(self, chapter_no, issues, original, revised):
        """保存改稿记录到 review.json。"""
        path = self.store.dir / "review.json"
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing = [r for r in existing if int(r.get("chapter", 0)) != chapter_no]
        existing.append({
            "chapter": chapter_no,
            "issues": issues,
            "auto_revised": original != revised,
            "original_words": len(original),
            "revised_words": len(revised),
        })
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
