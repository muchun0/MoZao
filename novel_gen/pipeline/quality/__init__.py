# -*- coding: utf-8 -*-
"""章节质检三层体系门面包（REQ-质检三层重构，2026-10-29）。

结构（TECH §3）：
- `checker.py`      —— `QualityChecker`：`check()` / `ensure_quality()` 编排、版本快照、落盘；
- `reviewers/`      —— L2 维度评委（reader / plot / consistency / prose）；
- `aggregate.py`    —— L3 聚合器（合并去重 / 维度归类 / 排序 / 门禁 / 建议清单）；
- `constants.py`    —— 阈值、面板正则、维度权重与元数据（唯一事实来源）；
- `text_tools.py`   —— 无状态纯文本工具。

后续专项债务（见 decisions.md 2026-10-29）：checker.py 内旧确定性方法将迁入
`deterministic/`、三类重写方法迁入 `rewrite_support.py`，本期保持行为零变化未剥离。

兼容契约：`from novel_gen.pipeline.quality import QualityChecker, _MAX_REWRITE, ...`
与重构前逐字一致（I-1）；旧路径符号在本模块命名空间全部可用。
"""
from __future__ import annotations

# ---- 门面类 ----
from .checker import QualityChecker

# ---- L3 聚合器与 L2 评委（新链路公开类型） ----
from . import aggregate
from .reviewers import (
    ReviewContext, ReviewerResult, DimensionResult, ReviewerIssue,
    ReaderReviewer, PlotReviewer, ConsistencyReviewer, ProseReviewer,
)

# ---- 兼容导出：旧 quality.py 模块级常量 ----
from .constants import (
    _MIN_WORDS_RATIO, _NGRAM, _DUP_RATIO, _MAX_RECENT,
    _SELF_NGRAM, _SELF_DUP_RATIO, _SALVAGE_MIN_LINE, _SALVAGE_MAX_LINE, _SALVAGE_SPREAD,
    _TERMINAL,
    _REPEAT_MIN_LEN, _REPEAT_LONG_LEN, _REPEAT_HARD_COUNT, _DIALOG_ALLOWED, _PANEL_KEYWORDS,
    _PANEL_REPEAT_SPAN, _PANEL_SPAN_LABEL, _PANEL_EVENT_ANCHORS, _PANEL_EVENT_RECALL,
    _MAX_REWRITE, _SALVAGE_MIN_KEEP, _CONSISTENCY_CHARS,
    REVIEW_DIMENSION_WEIGHTS, DIMENSION_LABELS, LEVEL_RANK, RECOMMENDATION_LIMIT,
)

# ---- 兼容导出：旧 quality.py 模块级纯函数 ----
from .text_tools import (
    _seg_has_settle_event_regex, _llm_panel_seg_settle, _load_persona, _has_type,
    _split_summary, _core_event_missing,
    _iter_sentences, _norm_sentence, _is_panel_line, _iter_repeat_groups,
    _locate_problem_spans, _merge_spans, _align_to_paras,
)

__all__ = [
    "QualityChecker", "aggregate",
    # L2 评委与类型
    "ReviewContext", "ReviewerResult", "DimensionResult", "ReviewerIssue",
    "ReaderReviewer", "PlotReviewer", "ConsistencyReviewer", "ProseReviewer",
    # 常量
    "_MIN_WORDS_RATIO", "_NGRAM", "_DUP_RATIO", "_MAX_RECENT",
    "_SELF_NGRAM", "_SELF_DUP_RATIO", "_SALVAGE_MIN_LINE", "_SALVAGE_MAX_LINE",
    "_SALVAGE_SPREAD", "_TERMINAL",
    "_REPEAT_MIN_LEN", "_REPEAT_LONG_LEN", "_REPEAT_HARD_COUNT", "_DIALOG_ALLOWED",
    "_PANEL_KEYWORDS", "_PANEL_REPEAT_SPAN", "_PANEL_SPAN_LABEL",
    "_PANEL_EVENT_ANCHORS", "_PANEL_EVENT_RECALL",
    "_MAX_REWRITE", "_SALVAGE_MIN_KEEP", "_CONSISTENCY_CHARS",
    "REVIEW_DIMENSION_WEIGHTS", "DIMENSION_LABELS", "LEVEL_RANK", "RECOMMENDATION_LIMIT",
    # 纯函数
    "_seg_has_settle_event_regex", "_llm_panel_seg_settle", "_load_persona", "_has_type",
    "_split_summary", "_core_event_missing",
    "_iter_sentences", "_norm_sentence", "_is_panel_line", "_iter_repeat_groups",
    "_locate_problem_spans", "_merge_spans", "_align_to_paras",
]
