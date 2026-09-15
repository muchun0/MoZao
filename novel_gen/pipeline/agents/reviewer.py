# -*- coding: utf-8 -*-
"""ReviewerAgent：审计 + 修订。

职责：
1. 一致性校验：人设/伏笔/时间线是否前后矛盾
2. 逻辑审查：剧情是否合理、有没有降智/巧合
3. 合规检测：有没有违规内容
4. 根据审计结果修订正文（高质量模式）
"""
from __future__ import annotations

from .base import BaseAgent


class ReviewerAgent(BaseAgent):
    role_name = "reviewer"
    role_description = (
        "你是一位严苛的编辑审校。你的工作是检查已生成内容的问题："
        "人设是否漂移、伏笔是否丢失、时间线是否矛盾、逻辑是否降智、有没有模板化套话。"
        "你只指出问题并给出修改建议，不直接重写全文。"
        "修订者角色会根据你的反馈做最小必要修改。"
    )

    def verify_consistency(self, project_id: str, chapter_no: int | None = None) -> dict:
        """设定一致性校验。"""
        from ...store.project import ProjectStore
        store = ProjectStore(project_id)
        outline = store.load_outline()
        world = store.load_world()
        chapters = store.load_chapters() if chapter_no is None else [
            store.load_chapter(chapter_no)
        ]
        from ...pipeline.templates import verify_prompt
        system, user = verify_prompt(outline, world, chapters, kind="consistency")
        self.logger.info("一致性校验开始")
        result = self.llm.chat(system, user, temperature=0.3)
        self.logger.info("一致性校验完成")
        return {"kind": "consistency", "result": result}

    def verify_logic(self, project_id: str, chapter_no: int | None = None) -> dict:
        """剧情逻辑审查。"""
        from ...store.project import ProjectStore
        store = ProjectStore(project_id)
        outline = store.load_outline()
        chapters = store.load_chapters() if chapter_no is None else [
            store.load_chapter(chapter_no)
        ]
        from ...pipeline.templates import verify_prompt
        system, user = verify_prompt(outline, None, chapters, kind="logic")
        self.logger.info("逻辑审查开始")
        result = self.llm.chat(system, user, temperature=0.3)
        self.logger.info("逻辑审查完成")
        return {"kind": "logic", "result": result}

    def verify_compliance(self, chapter_no: int, content: str) -> dict:
        """网文合规检测。"""
        from ...pipeline.templates import verify_prompt
        system, user = verify_prompt(None, None, [{"content": content, "no": chapter_no}], kind="compliance")
        self.logger.info("合规检测开始")
        result = self.llm.chat(system, user, temperature=0.2)
        self.logger.info("合规检测完成")
        return {"kind": "compliance", "result": result}

    def revise_chapter(self, chapter_no: int, plan_title: str, plan_goal: str,
                       content: str, silent: bool = False) -> str:
        """根据审校结果修订正文（高质量模式）。"""
        from ...pipeline.reviser import ChapterReviser
        reviser = ChapterReviser(self.cfg, self.llm, self.store)
        return reviser.revise(chapter_no, plan_title, plan_goal, content, silent=silent)
