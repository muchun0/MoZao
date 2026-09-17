# -*- coding: utf-8 -*-
"""
调研引擎：自动生成小说的第一阶段——先吸收热门小说共性，再进入大纲/正文。

流程：
    收集原始素材（内置知识库 [+ 可选联网搜索]）
    → 交给 LLM 按题材提炼出《爆款共性调研报告》(JSON)
    → 落盘 research.json，并在后续大纲/人物/正文阶段注入该报告

为什么要做这一步：长篇小说最怕「闭门造车」，直接生成容易产出过时套路、
节奏失衡、开篇劝退的文本；先对齐市场共性再创作，能显著提升成品的
「追读感」与题材贴合度。
"""
from __future__ import annotations

from ..config import NovelConfig
from ..llm.base import LLMClient
from ..store.project import ProjectStore
from .schemas import ResearchReport
from .sources import KnowledgeBaseSource, SourceResult, WebSearchSource


class ResearchEngine:
    def __init__(self, cfg: NovelConfig, llm: LLMClient, store: ProjectStore,
                 role_prompt: str = ""):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.role_prompt = role_prompt

    def _sys(self, system: str) -> str:
        return f"{self.role_prompt}\n\n{system}" if self.role_prompt else system

    # ---------- 素材收集 ----------
    def _collect_material(self) -> list[SourceResult]:
        results: list[SourceResult] = []
        kb = KnowledgeBaseSource()
        if kb.available():
            results.extend(kb.collect(self.cfg.genre))

        web = WebSearchSource(self.cfg.search)
        if web.available():
            results.extend(web.collect(self.cfg.genre, self.cfg.premise))
        return results

    def _material_text(self, results: list[SourceResult]) -> str:
        if not results:
            return "（暂无外部素材，请基于你对网络小说市场的既有认知作答。）"
        return "\n\n".join(r.to_text() for r in results)

    # ---------- 主流程 ----------
    def run(self, silent: bool = False) -> ResearchReport:
        material = self._collect_material()
        material_text = self._material_text(material)
        used_web = any(isinstance(r, SourceResult) and r.url for r in material)

        system = (
            "你是一位深耕网文市场多年的编辑兼写作研究专家，熟悉各大平台"
            "（起点/番茄/晋江等）的爆款规律与读者心理。你的任务是从给定素材中"
            "提炼出可执行的『题材共性调研报告』，供 AI 写手直接套用于新书创作。"
            "你的判断要具体、可操作，避免空话套话。"
        )
        user = (
            f"【待创作题材】{self.cfg.genre}\n"
            f"【核心创意/简介】{self.cfg.premise or '（未提供，按题材常规创作）'}\n"
            f"【目标字数】全书约 {self.cfg.total_words} 字，单章约 {self.cfg.chapter_words} 字\n\n"
            f"【可参考的原始素材】\n{material_text}\n\n"
            f"请基于以上素材，输出《{self.cfg.genre}题材爆款共性调研报告》，JSON 字段如下：\n"
            f"- genre：题材名\n"
            f"- summary：本题材共性综述（2-4 句）\n"
            f"- trends：当前 2-4 个流行方向（含 2026 趋势）\n"
            f"- plot_patterns：3-6 个高频情节套路（每个一句，可混合使用）\n"
            f"- styles：2-5 个常见文风元素（说明如何混合，如『热血王道+轻快日常』）\n"
            f"- pacing：节奏规律（一句，含开篇/中段/高潮密度）\n"
            f"- opening_strategy：开篇策略（主角如何快速立住+悬念怎么抛）\n"
            f"- avoid：2-4 个应避免的雷区/过时套路\n"
            f"- references：3-6 部参考作品及可借鉴点\n"
            f"报告要求：紧扣素材、给出混合方案而非单一复制某部作品。"
        )

        data = self.llm.chat_json(self._sys(system), user)
        report = ResearchReport.from_dict(data)
        if not report.genre:
            report.genre = self.cfg.genre

        self.store.save_research(report)
        if not silent:
            self._print_summary(report, used_web)
        return report

    @staticmethod
    def _print_summary(report: ResearchReport, used_web: bool) -> None:
        print(f"\n[调研完成] 题材：{report.genre}"
              f" ｜ 数据源：{'内置知识库 + 联网搜索' if used_web else '内置知识库 + 模型先验'}")
        print(f"共性综述：{report.summary}")
        print("流行方向：", "；".join(report.trends[:3]))
        print("情节套路：", "；".join(report.plot_patterns[:4]))
        print("文风混合：", "；".join(report.styles[:3]))
        print(f"开篇策略：{report.opening_strategy}\n")
