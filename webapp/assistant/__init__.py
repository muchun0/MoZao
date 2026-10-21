# -*- coding: utf-8 -*-
"""小墨AI 包（REQ-小墨AI 一期）：只读项目产物，提供写作对话能力。

包本身不导入子模块（避免 import webapp.assistant 时连带拉起 core/db）；
调用方按需 `from webapp.assistant import service` / `context` / `cards`。
"""
