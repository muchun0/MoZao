# -*- coding: utf-8 -*-
"""R27 分步流程的共享常量（阶段键 / agent 映射 / 展示名）。

单独成文件的原因：编排状态机与确认卡渲染都要用同一份阶段定义，集中在此避免两处维护。
"""
from __future__ import annotations

FLOW_FILE = "generation_stage.json"
FLOW_VERSION = 1

STAGE_KEYS = ("research", "outline", "world", "volume", "chapters")
STAGE_AGENT = {
    "research": "researcher",
    "outline": "architect.outline",
    "world": "architect.world",
    "volume": "architect.volume",
    "chapters": "writer.chapter",
}
AGENT_STAGE = {v: k for k, v in STAGE_AGENT.items()}
STAGE_LABELS = {
    "research": "题材调研",
    "outline": "大纲",
    "world": "人物世界观",
    "volume": "卷纲",
    "chapters": "正文",
}
#: 中间阶段在 run 成功后进入 done（等确认）；chapters 成功后直接 confirmed（终态）
MIDDLE_STAGES = ("research", "outline", "world", "volume")
