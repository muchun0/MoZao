# -*- coding: utf-8 -*-
"""正文输出纪律（多稿拼接防护）提示词注入测试（P2）。

背景：曾出现一次 LLM 返回把同一章多个起笔草稿首尾拼接（入墟→突袭→睁眼→营地），
并在句子中途截断，被原样落盘。此处验证所有「整章正文输出」提示词都注入了
输出纪律块，从提示词源头规避同类问题。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from novel_gen.config import NovelConfig
from novel_gen.pipeline import templates

KEY = "【输出纪律·硬约束】"
FORBIDDEN_OR = "严禁并列输出多个起笔、多个改写方案或多稿拼接"


def _cfg():
    return NovelConfig(title="测试书", chapter_words=2500, total_words=10000)


def _system(fn, *args, **kw):
    """prompt 函数统一返回 (system, user)，取第一项。"""
    out = fn(*args, **kw)
    return out[0] if isinstance(out, tuple) else out


@pytest.mark.p2
@pytest.mark.parametrize("builder,args", [
    ("scene_prompt",
     (1, "第一章", 1, {"location": "墟", "event": "入墟挖晶", "emotion": "压抑",
                        "anchors": ["蚀晶"], "target_words": 1200}, "", "", None)),
    ("chapter_prompt",
     (1, "第一章", "写陆沉入墟", "", "", "")),
    ("polish_chapter_prompt",
     (1, "第一章", "正文…")),
    ("revise_apply_prompt",
     (1, "第一章", "正文…", [])),
    ("expand_chapter_prompt",
     (1, "第一章", "正文…", 3000)),
])
def test_discipline_injected(builder, args):
    """各正文生成提示词的系统提示词均包含输出纪律（单版本/禁拼接/完整收尾）。"""
    cfg = _cfg()
    fn = getattr(templates, builder)
    system = _system(fn, cfg, *args)
    assert KEY in system
    assert FORBIDDEN_OR in system
    # 完整收尾要求（禁止中途截断）
    assert "句末标点" in system or "中途截断" in system


def test_discipline_block_self_consistent():
    """纪律块本身包含四类硬约束：单版本/禁元文字开头/完整收尾/禁止重述。"""
    text = templates._output_discipline()
    assert "一个" in text and "完整连贯的正文版本" in text
    assert "元文字开头" in text
    assert "自然收尾" in text
    assert "返回开头重述" in text
