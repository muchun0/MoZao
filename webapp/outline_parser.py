# -*- coding: utf-8 -*-
"""上传大纲智能解析：把用户的大纲文档解析成新建表单可填的结构化字段。

支持格式：.txt / .md / .docx（2MB 内）。
解析主通道走服务端 LLM（DeepSeek）结构化输出；LLM 不可用时降级为
启发式解析（标题取首行、章节标题收集为剧情走向），保证功能可用。
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

from novel_gen.config import LLMConfig

ALLOWED_EXTENSIONS = {".txt", ".md", ".docx"}
MAX_SIZE = 2 * 1024 * 1024  # 2MB

_SYSTEM = (
    "你是资深小说编辑助手。用户会给你一份小说大纲/设定文档，请把它提炼成结构化字段，"
    "供小说生成器的『新建项目』表单使用。"
    "只输出 JSON，不要任何解释、前缀或 Markdown 代码块标记。"
)

_USER_TEMPLATE = (
    "请解析以下大纲文档，输出严格 JSON（字段说明如下，无法从原文确定的字段给 null，"
    "不要编造原文没有的内容）：\n"
    "{\n"
    '  "title": "书名（取自标题或正文首部，无则 null）",\n'
    '  "genre": "题材（如：玄幻/都市/悬疑/科幻/言情/历史/奇幻/仙侠…，从原文判断，无则 null）",\n'
    '  "total_words": 目标总字数（整数。原文若写“约30万字/50w”等换算成数字，无明确字数则 null）,\n'
    '  "chapter_words": 单章字数（整数，原文明确提到才有，无则 null）,\n'
    '  "style": "文风要求（冷峻写实/克制冷冽/轻松幽默…，原文有则提取，无则 null）",\n'
    '  "premise": "核心创意/一句话简介（高度概括这本书要写什么）",\n'
    '  "background": "故事背景/世界观（时代、世界格局、力量体系、核心矛盾等，尽量完整保留原文设定）",\n'
    '  "hero": "主角设定（姓名、身份、性格、目标…，尽量完整保留原文）",\n'
    '  "characters": ["配角/反派：姓名：身份与设定（每行一个字符串）"],\n'
    '  "plot_direction": "剧情走向/关键情节（按原文脉络概括主线发展，尽量完整保留关键桥段）"\n'
    "}\n"
    "要求：background/hero/plot_direction 是强约束字段，能提取多少保留多少，宁可长一点；"
    "genre 若有多个题材取最核心的一个；characters 不含主角。\n\n"
    "【大纲文档】\n{text}"
)


def extract_text(filename: str, data: bytes) -> str:
    """按扩展名提取纯文本。txt/md 直接解码（utf-8 失败回退 gbk）；docx 解析段落。"""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"不支持的文件格式 {ext or '(无扩展名)'}，请上传 .txt / .md / .docx")
    if len(data) > MAX_SIZE:
        raise ValueError("文件超过 2MB 限制，请精简后上传")
    if ext == ".docx":
        return _extract_docx(data)
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        raise ValueError("服务端缺少 python-docx，无法解析 .docx，请改用 .txt / .md")
    doc = docx.Document(io.BytesIO(data))
    parts = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


def parse_outline_text(text: str, llm) -> dict:
    """主通道：LLM 结构化解析；失败降级为启发式。"""
    text = text.strip()
    if not text:
        raise ValueError("文件内容为空")
    try:
        data = llm.chat_json(
            _SYSTEM,
            _USER_TEMPLATE.replace("{text}", text[:12000]),  # 用 replace 而非 format：JSON 示例含花括号
            temperature=0.2,
            max_tokens=2000,
        )
        return _normalize(data)
    except Exception:
        # LLM 失败不阻断功能：启发式兜底
        return fallback_parse(text)


def fallback_parse(text: str) -> dict:
    """启发式兜底：标题取首个非空行（去掉 markdown # 与书名号）；章节标题收进剧情走向。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    title = ""
    if lines:
        title = re.sub(r"^#+\s*", "", lines[0]).strip("《》 \t")
    chapters = [
        ln for ln in lines if re.match(r"^(第[0-9一二三四五六七八九十百千]+[章回节卷]|Chapter\s*\d+)", ln)
    ]
    return {
        "title": title or None,
        "genre": None,
        "total_words": None,
        "chapter_words": None,
        "style": None,
        "premise": None,
        "background": None,
        "hero": None,
        "characters": [],
        "plot_direction": "\n".join(chapters) if chapters else None,
    }


def _normalize(data) -> dict:
    """校验/清洗 LLM 输出，保证字段类型与前端表单一致。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            data = {}
    if not isinstance(data, dict):
        data = {}
    allowed = {
        "title", "genre", "total_words", "chapter_words",
        "style", "premise", "background", "hero", "characters", "plot_direction",
    }
    out = {k: data.get(k) for k in allowed}
    # 整数校验
    for k in ("total_words", "chapter_words"):
        try:
            out[k] = int(out[k]) if out[k] is not None else None
        except (TypeError, ValueError):
            out[k] = None
    # 字符串清洗：空串转 None
    for k in ("title", "genre", "style", "premise", "background", "hero", "plot_direction"):
        v = out[k]
        if isinstance(v, str):
            v = v.strip()
        out[k] = v or None
    # characters 必须是字符串列表
    chars = out.get("characters")
    if isinstance(chars, str):
        chars = [c.strip() for c in re.split(r"[\n；;]", chars) if c.strip()]
    if not isinstance(chars, list):
        chars = []
    out["characters"] = [
        str(c).strip() for c in chars if str(c).strip() and str(c).strip() != "null"
    ]
    return out
