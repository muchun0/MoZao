# -*- coding: utf-8 -*-
"""
调研数据源：为「调研阶段」收集原始素材。

- KnowledgeBaseSource：读取内置 knowledge_base.json（零配置，永远可用）
- WebSearchSource：可选联网搜索（博查 bocha / serper），未配置 Key 时自动不可用，
  由上层 fallback 到知识库 + LLM 先验知识，不影响主流程
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from ..config import SEARCH_PROVIDER_DEFAULTS, SearchConfig

_KB_PATH = Path(__file__).parent / "knowledge_base.json"


class SourceResult:
    """一条素材：来源标题 + 正文 + 可选 URL。"""
    __slots__ = ("title", "content", "url")

    def __init__(self, title: str, content: str, url: str = ""):
        self.title = title
        self.content = content
        self.url = url

    def to_text(self) -> str:
        base = f"【{self.title}】\n{self.content}"
        return f"{base}\n来源：{self.url}" if self.url else base


class KnowledgeBaseSource:
    """内置热门小说共性知识库。"""

    name = "knowledge_base"

    def __init__(self):
        self._data: Optional[dict] = None

    def available(self) -> bool:
        return self._load() is not None

    def _load(self) -> Optional[dict]:
        if self._data is None:
            try:
                self._data = json.loads(_KB_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        return self._data

    def collect(self, genre: str) -> list[SourceResult]:
        data = self._load() or {}
        results: list[SourceResult] = []

        common = {
            "当前流行趋势(2026)": data.get("common_trends_2026", []),
            "高频情节套路": data.get("plot_patterns", []),
            "节奏规律": data.get("pacing_rules", []),
            "情绪法则": data.get("emotion_rules", []),
            "开篇技巧": data.get("opening_rules", []),
            "人物塑造": data.get("character_rules", []),
        }
        for title, items in common.items():
            if items:
                results.append(SourceResult(
                    title=f"[通用] {title}",
                    content="\n".join(f"- {x}" for x in items),
                ))

        style_spectrum = data.get("style_spectrum", [])
        if style_spectrum:
            lines = [f"- {s.get('name')}：{s.get('desc')}" for s in style_spectrum]
            results.append(SourceResult("[通用] 文风光谱（可混合）", "\n".join(lines)))

        genre_info = data.get("by_genre", {}).get(genre)
        if genre_info:
            lines = [f"- {k}：{v}" for k, v in genre_info.items() if v]
            results.append(SourceResult(f"[{genre}] 题材要点", "\n".join(lines)))

        return results


class WebSearchSource:
    """可选联网搜索源（bocha / serper），需在 config 配置 api_key。"""

    name = "web_search"

    def __init__(self, cfg: SearchConfig):
        self.cfg = cfg
        self._defaults = SEARCH_PROVIDER_DEFAULTS.get(cfg.provider, {})

    def available(self) -> bool:
        return bool(self.cfg.provider and self.cfg.api_key)

    def _search_once(self, query: str) -> list[SourceResult]:
        url = self.cfg.api_url or self._defaults.get("api_url", "")
        if not url:
            return []
        key = self.cfg.api_key
        header_key = self._defaults.get("header_key", "Authorization")
        header_value = self._defaults.get("header_value", "Bearer {key}").format(key=key)

        body = json.dumps(
            {"query": query, "count": self.cfg.count} if self.cfg.provider == "bocha"
            else {"q": query, "num": self.cfg.count}
        ).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header(header_key, header_value)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return []

        # 统一提取：兼容 bocha(data.webPages.value) 与 serper(organic)
        raw_items: list[dict] = []
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            web_pages = data.get("webPages")
            if isinstance(web_pages, dict) and isinstance(web_pages.get("value"), list):
                raw_items = web_pages["value"]
        if not raw_items and isinstance(payload, dict) and isinstance(payload.get("organic"), list):
            raw_items = payload["organic"]

        results: list[SourceResult] = []
        for it in raw_items[: self.cfg.count]:
            if not isinstance(it, dict):
                continue
            title = it.get("name") or it.get("title") or ""
            url_ = it.get("url") or it.get("link") or ""
            content = it.get("snippet") or it.get("summary") or ""
            if title or content:
                results.append(SourceResult(title=title, content=content, url=url_))
        return results

    def collect(self, genre: str, premise: str) -> list[SourceResult]:
        queries = [
            f"{genre}小说 热门 爆款 套路",
            f"{genre}小说 文风 节奏 2026 趋势",
        ]
        if premise and len(premise) > 4:
            queries.append(f"{premise} 小说 类似 作品")
        results: list[SourceResult] = []
        for q in queries:
            results.extend(self._search_once(q))
        return results
