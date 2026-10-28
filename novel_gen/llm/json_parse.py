# -*- coding: utf-8 -*-
"""LLM 输出 JSON 容错解析（AGENTS.md §11.7 统一健壮封装）。

对模型返回的 JSON 做容错解析：剥代码块围栏（含 ```json 标记）→ 截断检测
→ 尾逗号修复 → 返回 dict；任何失败返回空 dict，绝不影响调用方。

职责边界：与 `novel_gen/llm/base.py:_parse_json_strict`（失败即抛、用于 LLM
重试路径）不同，本模块是「失败静默返回空 dict」的容错版，供 pipeline 各
解析点统一使用。跨模块解析必须收敛到此处，禁止在别处重复实现。
"""
from __future__ import annotations

import json
import re


def parse_json_obj(text) -> dict:
    """容错解析 LLM 输出的 JSON 对象；任何失败返回空 dict。

    剥 ```/```json 围栏（任意位置）→ 尾部截断时剥离未闭合片段 → 修复尾逗号
    → json.loads → 非 dict 视为失败返回 {}。所有异常统一返回 {}。
    """
    try:
        if not isinstance(text, str):
            text = str(text)
        t = text.strip()
        m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
        if m:
            t = m.group(1).strip()
        # 截断检测：尾部不完整 JSON 时去掉最后一段未闭合
        if not t.startswith("{"):
            i = t.find("{")
            t = t[i:] if i >= 0 else t
        # 尾逗号修复
        t = re.sub(r",\s*([}\]])", r"\1", t)
        data = json.loads(t)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
