# -*- coding: utf-8 -*-
"""状态层（REQ-R21 / TECH-R21，M1）。

四个子模块各管一件事，互不写对方文件：

- `events`      事件流：append-only 的 `events.jsonl` + 消费水位侧车 `consumption.json`
- `foreshadow`  伏笔唯一写入源 `foreshadowing.json`：五态校验 + 回收证据强绑定
- `conflict_rules` 五类确定性冲突规则（纯函数，不依赖 LLM）
- `migrate` / `reconcile` 历史归并与双写对账（幂等）

约定：本包只产出**源数据**，展示与注入仍由 `novel_gen/pipeline/book_facts.py`
统一读取；避免出现第二份「坑列表」或第二套冲突结论。
"""
from __future__ import annotations

__all__ = ["events", "foreshadow", "conflict_rules", "migrate", "reconcile"]
