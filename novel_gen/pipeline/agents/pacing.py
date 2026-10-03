# -*- coding: utf-8 -*-
"""PacingAgent：节奏与伏笔 Agent（表层落地生产者）。

REQ-多Agent协作分层优化：负责「全本关键节点时间线（激励事件/卷末高潮/终极高潮）
+ 分卷节奏梯次（爽点密度/高潮章/过渡区间）+ 伏笔回收闭环」的确定性推导与回填。
只写自身领域（骨架层的节奏/伏笔结构），不越权修改世界观/人物字段。

当前为确定性规则推导（零 LLM、可复现单测），后续可叠加 LLM 精细化设计。

提示词文件：prompts/pacing.md
"""
from __future__ import annotations

from ...store.schemas import (EmotionBeat, ForeshadowItem, KeyNode, Outline, VolumePacing, World)
from .base import BaseAgent


def _map_emotion(emotion: str, tag: str) -> str:
    """把单章 emotion/tag 归一到情绪类型（爽点/虐点/悬念/笑点/平淡）。"""
    s = f"{emotion} {tag}"
    for kw, etype in (("爽", "爽点"), ("打脸", "爽点"), ("升级", "爽点"), ("收获", "爽点"),
                      ("反击", "爽点"), ("反转", "爽点"),
                      ("虐", "虐点"), ("悲", "虐点"), ("牺牲", "虐点"), ("痛", "虐点"),
                      ("悬念", "悬念"), ("钩子", "悬念"), ("揭秘", "悬念"), ("疑", "悬念"),
                      ("笑", "笑点"), ("轻松", "平淡"), ("日常", "平淡")):
        if kw in s:
            return etype
    return "平淡"


class PacingAgent(BaseAgent):
    """节奏与伏笔 Agent：推导关键节点、分卷节奏与伏笔回收闭环。"""

    role_name = "pacing"

    def __init__(self, cfg, llm, store):
        super().__init__(cfg, llm, store)

    def plan_outline(self, outline: Outline) -> tuple[Outline, dict]:
        """推导并回填大纲的关键节点与分卷节奏（只依赖大纲，run_outline 后调用）。

        只补缺失，不覆盖已有设计。返回 (outline, summary)。
        """
        chapters = getattr(outline, "chapters", None) or []
        vols = getattr(outline, "volumes", None) or []
        last_ch = max((getattr(c, "no", 0) or 0) for c in chapters) if chapters else 0

        existing_nodes = list(getattr(outline, "key_nodes", None) or [])
        existing_kinds = {getattr(k, "kind", "") for k in existing_nodes}
        nodes = list(existing_nodes)
        if chapters and "激励事件" not in existing_kinds:
            nodes.insert(0, KeyNode(kind="激励事件", chapter_no=1,
                                    description="首章切入主角困境，触发核心冲突"))
        for v in vols:
            end = int(getattr(v, "end_ch", 0) or 0)
            if end and f"第{v.no}卷高潮" not in existing_kinds:
                nodes.append(KeyNode(kind=f"第{v.no}卷高潮", chapter_no=end,
                                     description=f"第{v.no}卷卷末高潮"))
        if last_ch and "终极高潮" not in existing_kinds:
            nodes.append(KeyNode(kind="终极高潮", chapter_no=last_ch,
                                 description="全书终极高潮，主线核心冲突最终解决"))
        nodes = [n for n in nodes if getattr(n, "chapter_no", 0)]
        nodes.sort(key=lambda n: n.chapter_no)
        outline.key_nodes = nodes

        existing_vp = {getattr(v, "volume_no", 0) for v in (getattr(outline, "volume_pacing", None) or [])}
        vp = list(getattr(outline, "volume_pacing", None) or [])
        for v in vols:
            vno = int(getattr(v, "no", 0) or 0)
            if vno in existing_vp:
                continue
            end = int(getattr(v, "end_ch", 0) or 0)
            start = int(getattr(v, "start_ch", 0) or 0)
            vp.append(VolumePacing(
                volume_no=vno, payoff_density="high",
                climax_ch=end, transition_range=f"{start}-{end}"))
        vp.sort(key=lambda x: x.volume_no)
        outline.volume_pacing = vp

        # 情绪曲线卡（P2）：基于各章 emotion/tag 聚合成跨章 EmotionBeat 区间（零 LLM）。
        # 只补缺失（已有 emotion_curve 不覆盖）；产出全本爽点/虐点/悬念分布，供章节情绪配比。
        if chapters and not (getattr(outline, "emotion_curve", None) or []):
            curve: list[EmotionBeat] = []
            cur: dict | None = None
            for c in sorted(chapters, key=lambda x: int(getattr(x, "no", 0) or 0)):
                no = int(getattr(c, "no", 0) or 0)
                etype = _map_emotion((getattr(c, "emotion", "") or "").strip(),
                                     (getattr(c, "tag", "") or "").strip())
                if cur and cur["type"] == etype and no - cur["to"] <= 1:
                    cur["to"] = no
                    continue
                if cur:
                    curve.append(EmotionBeat(ch_from=cur["from"], ch_to=cur["to"],
                                             emotion_type=cur["type"], intensity=cur["intensity"],
                                             note=cur["note"]))
                tag = (getattr(c, "tag", "") or "").strip()
                cur = {"from": no, "to": no, "type": etype, "intensity": 3,
                       "note": f"第{no}章" + (f"（{tag}）" if tag else "")}
            if cur:
                curve.append(EmotionBeat(ch_from=cur["from"], ch_to=cur["to"],
                                         emotion_type=cur["type"], intensity=cur["intensity"],
                                         note=cur["note"]))
            outline.emotion_curve = curve

        summary = {"key_nodes": len(outline.key_nodes), "volume_pacing": len(outline.volume_pacing),
                   "emotion_curve": len(outline.emotion_curve)}
        return outline, summary

    def plan_foreshadow(self, outline: Outline, world: World) -> tuple[World, int]:
        """伏笔回收闭环：medium 卷内 / large 全篇 补 reveal_ch（run_world 后调用）。

        返回 (world, fixed)。只补缺失回收章的伏笔，不覆盖已设置回收章。
        """
        vols = getattr(outline, "volumes", None) or []
        chapters = getattr(outline, "chapters", None) or []
        last_ch = max((getattr(c, "no", 0) or 0) for c in chapters) if chapters else 0
        fixed = 0
        fs = list(getattr(world, "foreshadowing", None) or [])
        for i, item in enumerate(fs):
            item = ForeshadowItem.from_dict(item) if not isinstance(item, ForeshadowItem) else item
            if getattr(item, "reveal_ch", None):
                fs[i] = item
                continue
            buried = int(getattr(item, "buried_ch", 0) or 0)
            lvl = (getattr(item, "level", "medium") or "medium")
            vol_end = _volume_end_for(vols, buried)
            if lvl == "large" or not vol_end:
                item.reveal_ch = last_ch or (vol_end or 0)
            else:
                item.reveal_ch = vol_end
            if item.reveal_ch:
                fixed += 1
            fs[i] = item
        world.foreshadowing = fs
        return world, fixed


def _volume_end_for(vols: list, chapter_no: int) -> int:
    """返回 chapter_no 所在卷的卷末章号；不在任何卷区间内返回 0。"""
    end = 0
    for v in vols:
        start = int(getattr(v, "start_ch", 0) or 0)
        vend = int(getattr(v, "end_ch", 0) or 0)
        if start and chapter_no >= start:
            end = max(end, vend)
    return end
