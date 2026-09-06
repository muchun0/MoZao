# -*- coding: utf-8 -*-
"""结局提示词验证：大纲层/正文末章/非末章/扩写模式/末章判定。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from novel_gen.config import NovelConfig, LLMConfig
from novel_gen.pipeline.templates import (
    outline_prompt, chapter_prompt, expand_outline_prompt,
)


def _cfg(chapters=2):
    return NovelConfig(
        title="验证本", genre="都市",
        total_words=2500 * chapters, chapter_words=2500,  # num_chapters = chapters
        de_ai=True, anti_similarity=True,
        llm=LLMConfig(provider="mock"),
    )


def test_outline_requires_ending():
    sys_, user = outline_prompt(_cfg(), "")
    assert ("结局" in user) and ("升维" in user), "大纲应包含结局+升维要求"


def test_outline_batch_only_final_requires_ending():
    cfg = _cfg(chapters=3)
    _, user_mid = outline_prompt(cfg, "", batch=(2, 2, ""))
    assert "结局" not in user_mid, "非末批不应要求结局"
    _, user_last = outline_prompt(cfg, "", batch=(3, 3, ""))
    assert "结局" in user_last, "末批应要求结局"


def test_chapter_final_injects_ending():
    cfg = _cfg()
    _, u = chapter_prompt(cfg, chapter_no=2, chapter_title="终章", chapter_goal="收束",
                          research_block="", memory_context="", prev_tail="", is_final=True)
    assert "升维" in u and "收束" in u, "末章应包含升维+收束"
    assert "未完待续" in u, "末章应禁止烂尾"


def test_chapter_non_final_no_ending():
    cfg = _cfg()
    _, u = chapter_prompt(cfg, chapter_no=1, chapter_title="开局", chapter_goal="推进",
                          research_block="", memory_context="", prev_tail="", is_final=False)
    assert "升维" not in u and "结局硬要求" not in u, "非末章不应有结局要求"
    assert "留钩子引向下章" in u, "非末章应保留钩子"


def test_expand_outline_requires_ending():
    _, u = expand_outline_prompt(_cfg(), "主角欠债百万，最终还清。")
    assert "结局" in u and "升维" in u, "扩写模式大纲应包含结局+升维"
