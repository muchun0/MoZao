# -*- coding: utf-8 -*-
"""L2 维度评委：reader / plot / consistency / prose，独立调用、结构化输出、失败降级。

公共框架（类型、JSON 健壮解析、降级骨架）见 `base.py`；每个评委只评自己的维度，
不跨维度下结论（MTS 防串味）。
"""
from .base import (
    BaseReviewer, ReviewContext, ReviewerResult, DimensionResult,
    ReviewerIssue, normalize_level, VALID_LEVELS,
)
from .reader_reviewer import ReaderReviewer
from .plot_reviewer import PlotReviewer
from .consistency_reviewer import ConsistencyReviewer
from .prose_reviewer import ProseReviewer

__all__ = [
    "BaseReviewer", "ReviewContext", "ReviewerResult", "DimensionResult",
    "ReviewerIssue", "normalize_level", "VALID_LEVELS",
    "ReaderReviewer", "PlotReviewer", "ConsistencyReviewer", "ProseReviewer",
]
