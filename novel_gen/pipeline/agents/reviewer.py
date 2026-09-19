# -*- coding: utf-8 -*-
"""ReviewerAgent：审校 + 修订。

提示词文件：prompts/reviewer.md
"""
from __future__ import annotations

from .base import BaseAgent


class ReviewerAgent(BaseAgent):
    role_name = "reviewer"

