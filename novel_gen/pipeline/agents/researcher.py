# -*- coding: utf-8 -*-
"""ResearcherAgent：题材调研 + 反套路改造。

提示词文件：prompts/researcher.md
"""
from __future__ import annotations

from ...research.research import ResearchEngine
from ...research.schemas import ResearchReport
from .base import BaseAgent


class ResearcherAgent(BaseAgent):
    role_name = "researcher"

    def run(self, silent: bool = False):
        if self.cfg.mode == "expand":
            return None
        existing = self.store.load_research()
        if existing is not None:
            return existing
        self.logger.info("题材调研开始")
        engine = ResearchEngine(self.cfg, self.llm, self.store,
                                role_prompt=self.persona_prefix())
        result = engine.run(silent=silent)
        self.logger.info("题材调研完成")
        self._run_anti_cliche(silent=silent)
        return result

    def _run_anti_cliche(self, silent: bool = False):
        from pathlib import Path
        path = Path(self.store.dir) / "anti_cliche.json"
        if path.exists():
            return
        try:
            from ...pipeline.templates import anti_cliche_prompt
            research = self.store.load_research()
            research_block = research.to_prompt_block() if research else ""
            sys_a, user_a = anti_cliche_prompt(
                self.cfg, self.cfg.genre, self.cfg.effective_title(),
                self.cfg.premise, research_block,
            )
            system = self._system_with_role(sys_a)
            raw = self.llm.chat(system, user_a, temperature=0.4, max_tokens=1500)
            import json
            t = raw.strip()
            if t.startswith("```"):
                lines = t.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                t = "\n".join(lines).strip()
            data = json.loads(t)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.logger.info("反套路设计完成")
        except Exception as e:
            self.logger.warning("反套路设计失败（不阻塞）: %s", e)
