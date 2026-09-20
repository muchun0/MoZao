# -*- coding: utf-8 -*-
"""ReaderAgent：读者视角审校。

从追更体验角度查问题：代入感、爽点闭环、章末钩子、阅读障碍。
与 Reviewer 的区别：Reviewer 查"对不对"（逻辑/一致性/合规），
Reader 查"爽不爽"（追更欲/代入感/情绪曲线）。

提示词文件：prompts/reader.md
"""
from __future__ import annotations

from .base import BaseAgent


class ReaderAgent(BaseAgent):
    role_name = "reader"
