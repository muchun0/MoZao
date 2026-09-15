# -*- coding: utf-8 -*-
"""ResearcherAgent：题材调研 + 反套路改造。

职责：
1. 联网搜索同题材热门作品，提炼共性套路
2. 输出反套路改造方案（主动破局/内部抱团/双向救赎）
3. 元素叠加建议（基底题材+2~3个加成元素）
"""
from __future__ import annotations

from ...research.research import ResearchEngine
from ...research.schemas import ResearchReport
from .base import BaseAgent


class ResearcherAgent(BaseAgent):
    role_name = "researcher"
    role_description = (
        "你是一位资深网文题材调研员。你的工作是联网搜索同类题材的热门作品，"
        "提炼共性套路和读者偏好，然后给出反套路改造建议——"
        "把被动冲突改成主角主动布局，把内部撕咬改成抱团对外，把单向拯救改成双向救赎。"
        "你只输出调研结论和改造方向，不写正文。"
    )

    def run(self, silent: bool = False):
        # 扩写模式跳过调研
        if self.cfg.mode == "expand":
            return None
        existing = self.store.load_research()
        if existing is not None:
            return existing
        self.logger.info("题材调研开始")
        result = ResearchEngine(self.cfg, self.llm, self.store).run(silent=silent)
        self.logger.info("题材调研完成")
        self._run_anti_cliche(silent=silent)
        return result

    def _run_anti_cliche(self, silent: bool = False):
        """反套路改造 + 元素叠加。"""
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
                t = t.split("```")[1] if "```" in t[3:] else t
                t = t.strip("json").strip()
            data = json.loads(t)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.logger.info("反套路设计完成")
        except Exception as e:
            self.logger.warning("反套路设计失败（不阻塞）: %s", e)
