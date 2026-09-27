# -*- coding: utf-8 -*-
"""
记忆系统：解决长篇小说「前后矛盾」的核心组件。

两层记忆：
1. 设定库（长期）：世界观 + 人物卡 + 伏笔清单，全书记忆，永不遗忘
2. 章节摘要（短期）：已写章节的一句话摘要，只回填最近 N 章，控制上下文成本

生成当前章时，把「设定库 + 最近 N 章摘要」拼进提示词，让模型基于既定事实续写。
"""
from __future__ import annotations

from ..store.project import ProjectStore
from ..store.schemas import World


class Memory:
    def __init__(self, store: ProjectStore, memory_window: int = 5):
        self.store = store
        self.memory_window = max(0, memory_window)

    def build_context(self, world: World | None, up_to_chapter: int) -> str:
        """为第 up_to_chapter 章构建四层记忆上下文。

        L1 全局设定库：世界观+人物卡+伏笔（全书，永不遗忘）
        L2 卷级摘要：当前所在卷的卷目标+高潮+人物成长
        L3 近章大纲：当前章前后各2章的剧情节点
        L4 前章尾文：最近N章一句话摘要（短期）
        """
        parts: list[str] = []

        # L1 全局设定库
        if world is not None:
            w_lines = []
            if world.world_view:
                w_lines.append(f"世界观：{world.world_view}")
            if world.characters:
                chars = "；".join(
                    f"{c.name}（{c.role}）——{c.desc}" for c in world.characters if c.name
                )
                if chars:
                    w_lines.append(f"人物卡：{chars}")
            if world.foreshadowing:
                def _fs(x):
                    if isinstance(x, dict):
                        return " ".join(str(v) for v in x.values())
                    return str(x)
                w_lines.append("已埋伏笔：" + "；".join(_fs(x) for x in world.foreshadowing))
            if getattr(world, "locked_settings", None):
                w_lines.append("【核心设定锁定·绝对不可违背】：" + "；".join(world.locked_settings))
            if w_lines:
                parts.append("[L1·全局设定·全书记忆]\n" + "\n".join(w_lines))

        # L2 卷级摘要：当前所在分卷
        try:
            outline = self.store.load_outline()
            if outline and outline.volumes:
                cur_vol = None
                for v in outline.volumes:
                    if v.start_ch <= up_to_chapter <= v.end_ch:
                        cur_vol = v
                        break
                if cur_vol:
                    v_lines = [f"当前卷：第{cur_vol.no}卷《{cur_vol.name}》"]
                    if cur_vol.intro:
                        v_lines.append(f"卷主题：{cur_vol.intro}")
                    if cur_vol.main_conflict:
                        v_lines.append(f"本卷矛盾：{cur_vol.main_conflict}")
                    if cur_vol.character_growth:
                        v_lines.append(f"人物成长：{cur_vol.character_growth}")
                    if cur_vol.climax:
                        v_lines.append(f"卷高潮：{cur_vol.climax}")
                    parts.append("[L2·当前分卷]\n" + "\n".join(v_lines))
                # L3 近章大纲：前后各2章节点
                nearby = []
                for c in outline.chapters:
                    if abs(c.no - up_to_chapter) <= 2 and c.no < up_to_chapter:
                        nearby.append(c)
                if nearby:
                    n_lines = [f"第{c.no}章《{c.title}》：{c.goal}" for c in nearby]
                    parts.append("[L3·近期章节大纲]\n" + "\n".join(n_lines))
        except Exception:
            pass

        # L4 前章尾文（短期摘要）
        summaries = self.store.load_summaries()
        recent = [s for s in summaries if int(s.get("no", 0)) < up_to_chapter]
        recent = recent[-self.memory_window:] if self.memory_window else recent
        if recent:
            lines = [f"第{s['no']}章：{s['summary']}" for s in recent]
            parts.append(
                f"[L4·前章剧情摘要·最近 {len(recent)} 章]\n" + "\n".join(lines)
            )

        # L4.5 近章正文直读（P0-1）：一句话摘要丢细节（对话/动作/物品/位置），
        # 直读最近 N 章正文尾部作为细节锚点；长度受 memory_direct_read_* 配置约束，
        # 0=关闭（行为与旧版一致）。
        try:
            cfg_d = self.store.load_config()
            n_direct = int(getattr(cfg_d, "memory_direct_read_chapters", 0) or 0)
            chars_d = int(getattr(cfg_d, "memory_direct_read_chars", 2000) or 0)
            if n_direct > 0 and chars_d > 0:
                direct_lines = []
                for dno in range(up_to_chapter - 1, max(0, up_to_chapter - 1 - n_direct), -1):
                    ch = self.store.load_chapter(dno)
                    if ch and ch.content:
                        tail = ch.content.strip()[-chars_d:]
                        if tail:
                            direct_lines.append(f"第{dno}章《{ch.title}》：{tail}")
                if direct_lines:
                    parts.append(
                        "[L4.5·近章正文直读·细节锚点（以直读正文为准，摘要仅供快速回顾）]\n"
                        + "\n\n".join(direct_lines)
                    )
        except Exception:
            pass

        # 一致性校验 issues（高质量模式）：提醒后续章节注意修正
        try:
            v_issues = self.store.load_verify_issues()
            # 只保留最近一次校验（按 chapter 取最新）
            if v_issues:
                last_ch = max(int(x.get("chapter", 0)) for x in v_issues)
                recent_issues = [x for x in v_issues if int(x.get("chapter", 0)) == last_ch]
                if recent_issues:
                    v_lines = [
                        f"- {it.get('problem', '')}（建议：{it.get('suggestion', '注意保持一致')}）"
                        for it in recent_issues
                    ]
                    parts.append(
                        "[前次一致性校验·需在后续章节注意]\n" + "\n".join(v_lines)
                    )
        except Exception:
            pass

        # 反套路约束（常驻）
        try:
            ac_path = self.store.dir / "anti_cliche.json"
            if ac_path.exists():
                import json
                ac_data = json.loads(ac_path.read_text(encoding="utf-8"))
                from .templates import anti_cliche_hint
                ac_hint = anti_cliche_hint(ac_data)
                if ac_hint:
                    parts.append(ac_hint)
        except Exception:
            pass

        # 重复度检测：最近章节高频词/句式
        try:
            from .repetition import RepetitionDetector
            cfg = self.store.load_config()
            detector = RepetitionDetector(cfg, self.store)
            banned_hint = detector.banned_hint(up_to_chapter)
            if banned_hint:
                parts.append(banned_hint)
        except Exception:
            pass

        if not parts:
            return ""
        return "\n\n".join(parts)
