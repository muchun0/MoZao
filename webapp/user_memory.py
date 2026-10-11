# -*- coding: utf-8 -*-
"""用户级记忆库服务（跨书创作偏好继承，REQ-用户级记忆库.md / TECH-用户级记忆库.md）。

三层记忆架构：
    章级沉淀(已有) → 书级 BookMemory(projects/<id>/book_memory.json，已有)
    → 用户级 UserMemory(users/<uid>/user_memory.json，本模块)
    → 新书继承(项目快照 user_memory_snapshot.json，webapp 写 / novel_gen 读)

写入全部零额外 LLM：
    ① 显式项目输入（题材/避雷/节奏）→ preferences（新书创建时，按项目幂等）；
    ② 人工编辑信号（user_edited 章个人锚）→ style_anchors（edit_chapter 时）；
    ③ 书级 → 用户级蒸馏（book_memory 的 pitfalls/taste）→ disciplines/stats
       （生成里程碑触发，全量重算式幂等：同书同贡献不重复累计）。

继承（build_snapshot）：按题材匹配过滤 + 上限裁剪，webapp 写项目快照文件，
novel_gen 读侧注入 prompt（软约束；数字方向/节拍循环/拼接检测等确定性校验
永远留在代码硬校验层 chapter_state/beats/quality，本模块不承载任何硬校验）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from novel_gen.utils.paths import get_projects_dir, user_memory_path

logger = logging.getLogger(__name__)

# ---------- 上限保护（防膨胀） ----------
_GENRES_MAX = 20
_AVOID_MAX = 50
_PACING_MAX = 10
_DISCIPLINES_MAX = 50
_ANCHORS_MAX = 30
_ANCHORS_TOTAL_CHARS = 600       # 注入时个人文风锚总长上限
_SNAPSHOT_PREF_CHARS = 300       # 注入时偏好摘要上限
_ANCHORS_INJECT = 3              # 注入时文风锚条数

_PACING_NAMES = {"low": "慢热", "medium": "适中", "high": "密集"}


def _empty() -> dict:
    """空用户级账本（无文件/损坏时兜底，降级安全）。"""
    return {
        "version": 1,
        "updated_at": "",
        "preferences": {"genres": [], "pacing": [], "avoid": []},
        "disciplines": [],
        "style_anchors": [],
        "stats": {"books": 0, "avg_score": 0.0, "top_patterns": [],
                  "project_signals": [], "book_contribs": {}},
    }


def _read_json(path, default):
    """读 JSON；不存在/损坏返回 default（与 store 惯例一致）。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path, data) -> None:
    """原子写：先写临时文件再 os.replace。

    Windows 上高频覆盖同一文件时，Defender/索引服务可能短暂锁定目标导致
    replace 抛 PermissionError（WinError 5）→ 短退避重试；仍失败回退直接写
    （放弃原子性换取不丢数据），再失败由调用方捕获降级。
    """
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            return


def _now() -> str:
    """UTC+8 时间戳（Asia/Shanghai 固定偏移，不依赖系统时区）。"""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _split_genres(genre: str) -> list[str]:
    """题材字符串拆词：『玄幻, 都市』→ ['玄幻', '都市']；去空白去重。"""
    out: list[str] = []
    for g in str(genre or "").replace("，", ",").split(","):
        g = g.strip()
        if g and g not in out:
            out.append(g)
    return out or ["玄幻"]


def _genre_match(pref_name: str, cfg_genres: list[str]) -> bool:
    """题材匹配：偏好题材与项目题材任一包含/被包含即命中（弱匹配防误杀）。"""
    pref = str(pref_name or "").strip()
    if not pref:
        return False
    for g in cfg_genres:
        g = g.strip()
        if pref in g or g in pref:
            return True
    return False


class UserMemoryService:
    """用户级记忆库：跨书沉淀 + 新书继承。全部写路径零额外 LLM。"""

    def __init__(self, uid: int):
        self.uid = int(uid)
        self.path = str(user_memory_path(self.uid))

    # ---------- 持久化 ----------
    def load(self) -> dict:
        """懒读用户级账本；无文件/损坏返回空账本（不抛异常，降级安全）。"""
        d = _read_json(self.path, None)
        return d if isinstance(d, dict) else _empty()

    def save(self, data: dict | None = None) -> None:
        """原子写覆盖（幂等；失败静默，不阻塞生成主流程）。

        调用方在 load() 后修改账本再 save(data) 落盘（防 save() 重载空账本丢改动）。
        """
        try:
            data = data if data is not None else self.load()
            data["updated_at"] = _now()
            _write_json(self.path, data)
        except Exception:
            logger.warning("user_memory 落盘失败 uid=%s（跳过，不阻塞）", self.uid)

    # ---------- 写入①：显式项目输入 → 偏好（新书创建时） ----------
    def record_project_signals(self, project_id: str, cfg) -> None:
        """新书创建：题材/避雷(forbidden)/节奏 → 更新偏好权重（零 LLM）。

        幂等：同一项目只计一次（stats.project_signals 去重），防重复创建刷权重。
        weight 不落盘，注入时按 count/books 现算（单一事实来源 = count）。
        """
        data = self.load()
        stats = data.setdefault("stats", _empty()["stats"])
        seen = stats.setdefault("project_signals", [])
        if str(project_id) in seen:
            return                      # 同一项目已计入，跳过
        pref = data.setdefault("preferences", {"genres": [], "pacing": [], "avoid": []})

        # 题材：genre 拆词逐项 +1
        for g in _split_genres(getattr(cfg, "genre", "")):
            item = next((x for x in pref["genres"] if x.get("name") == g), None)
            if item is None:
                pref["genres"].append({"name": g, "count": 1, "last_book": str(project_id)})
            else:
                item["count"] = int(item.get("count", 0)) + 1
                item["last_book"] = str(project_id)
        # 节奏：pacing_density → 中文名 +1
        pd = str(getattr(cfg, "pacing_density", "") or "").strip().lower()
        if pd in _PACING_NAMES:
            name = _PACING_NAMES[pd]
            item = next((x for x in pref["pacing"] if x.get("name") == name), None)
            if item is None:
                pref["pacing"].append({"name": name, "count": 1, "last_book": str(project_id)})
            else:
                item["count"] = int(item.get("count", 0)) + 1
                item["last_book"] = str(project_id)
        # 避雷：forbidden 逐项 +1（source=forbidden，用户显式排除）
        for fb in (getattr(cfg, "forbidden", None) or []):
            fb = str(fb).strip()
            if not fb:
                continue
            item = next((x for x in pref["avoid"] if x.get("desc") == fb), None)
            if item is None:
                pref["avoid"].append({"desc": fb, "source": "forbidden",
                                      "count": 1, "last_book": str(project_id)})
            else:
                item["count"] = int(item.get("count", 0)) + 1
                item["last_book"] = str(project_id)

        # 上限裁剪（按 count 降序保留）
        pref["genres"] = pref["genres"][:_GENRES_MAX]
        pref["pacing"] = pref["pacing"][:_PACING_MAX]
        pref["avoid"] = pref["avoid"][:_AVOID_MAX]
        seen.append(str(project_id))
        self.save(data)

    # ---------- 写入②：人工编辑信号 → 文风锚（edit_chapter 时） ----------
    def record_edits(self, project_id: str, chapter_no: int,
                     anchor_texts: list[str]) -> None:
        """跨书个人文风锚增量登记（零 LLM）。

        anchor_texts 由调用方复用 style_anchor 提取口径给出（每章≤2 段、总长≤600）；
        同书同章同文本去重；上限 _ANCHORS_MAX 条，超限按插入顺序淘汰最旧。
        """
        if not anchor_texts:
            return
        data = self.load()
        anchors = data.setdefault("style_anchors", [])
        changed = False
        for t in anchor_texts:
            t = str(t).strip()
            if not t:
                continue
            key = (str(project_id), int(chapter_no), t)
            if any((a.get("book") == key[0] and int(a.get("ch", 0)) == key[1]
                    and a.get("text") == key[2]) for a in anchors):
                continue
            anchors.append({"book": key[0], "ch": key[1],
                            "text": t, "length": len(t)})
            changed = True
        if changed:
            anchors[:] = anchors[-_ANCHORS_MAX:]      # 保留最近 N 条
            self.save(data)

    # ---------- 写入③：书级 → 用户级蒸馏（生成完成/里程碑） ----------
    def distill(self, project_id: str) -> int:
        """把一本书 book_memory.json 的踩坑/评分蒸馏进用户级账本（零 LLM）。

        幂等：stats.book_contribs 保存每本书最新贡献快照；同书同贡献再调用
        直接跳过（生成完成可能多次触发）；书有新踩坑（贡献变化）时全量重算：
        移除旧贡献 → 写入新贡献 → 重建 disciplines/stats（不重复累计）。
        返回本次变更条数（0=无变化）。
        """
        book = _read_json(os.path.join(get_projects_dir(), str(project_id),
                                       "book_memory.json"), None)
        if not isinstance(book, dict):
            return 0
        data = self.load()
        stats = data.setdefault("stats", _empty()["stats"])
        contribs = stats.setdefault("book_contribs", {})

        # 本书最新贡献：score（taste 均分，无则 None）+ patterns{pattern: count}
        taste = book.get("taste") or {}
        chapters = taste.get("chapters") or []
        score = None
        if chapters:
            score = round(sum(int(c.get("score", 0)) for c in chapters) / len(chapters), 1)
        patterns: dict[str, int] = {}
        fixes: dict[str, str] = {}
        for pit in book.get("pitfalls") or []:
            pat = str(pit.get("pattern") or "").strip()
            if not pat:
                continue
            patterns[pat] = int(pit.get("count", 1))
            fixes[pat] = str(pit.get("fix") or "")
        new_contrib = {"score": score, "patterns": patterns}

        old = contribs.get(str(project_id))
        if old == new_contrib:
            return 0                                    # 幂等：贡献未变
        if old is not None:
            self._remove_contrib(data, str(project_id), old)
        contribs[str(project_id)] = new_contrib
        n = self._add_contrib(data, str(project_id), new_contrib, fixes)
        self._recompute_stats(data)
        self.save(data)
        return n

    @staticmethod
    def _remove_contrib(data: dict, project_id: str, contrib: dict) -> None:
        """从 disciplines 移除某书旧贡献（book_ids 去该书、count 扣减、空则删条目）。"""
        dis = data.setdefault("disciplines", [])
        for pat, cnt in (contrib.get("patterns") or {}).items():
            d = next((x for x in dis if x.get("pattern") == pat), None)
            if d is None:
                continue
            bids = [b for b in (d.get("book_ids") or []) if b != project_id]
            d["book_ids"] = bids
            d["count"] = max(0, int(d.get("count", 0)) - int(cnt))
            d["books"] = len(bids)
            if not bids or int(d.get("count", 0)) <= 0:
                dis.remove(d)

    @staticmethod
    def _add_contrib(data: dict, project_id: str, contrib: dict,
                     fixes: dict[str, str]) -> int:
        """把某书新贡献写入 disciplines（pattern 归并；rule/last_book 取最新）。"""
        dis = data.setdefault("disciplines", [])
        n = 0
        for pat, cnt in (contrib.get("patterns") or {}).items():
            d = next((x for x in dis if x.get("pattern") == pat), None)
            if d is None:
                if len(dis) >= _DISCIPLINES_MAX:
                    continue
                dis.append({
                    "pattern": pat,
                    "rule": fixes.get(pat, ""),
                    "book_ids": [str(project_id)],
                    "books": 1,
                    "count": int(cnt),
                    "escalated_from": str(project_id),
                    "last_book": str(project_id),
                })
                n += 1
            else:
                if str(project_id) not in (d.get("book_ids") or []):
                    d.setdefault("book_ids", []).append(str(project_id))
                d["book_ids"] = list(dict.fromkeys(d["book_ids"]))
                d["books"] = len(d["book_ids"])
                d["count"] = int(d.get("count", 0)) + int(cnt)
                if fixes.get(pat):
                    d["rule"] = fixes[pat]
                d["last_book"] = str(project_id)
                n += 1
        return n

    @staticmethod
    def _recompute_stats(data: dict) -> None:
        """重算 stats：books/avg_score/top_patterns（由 book_contribs 现算，防漂移）。"""
        stats = data.setdefault("stats", _empty()["stats"])
        contribs = stats.get("book_contribs") or {}
        scores = [c.get("score") for c in contribs.values()
                  if isinstance(c.get("score"), (int, float))]
        stats["books"] = len(contribs)
        stats["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0.0
        agg: dict[str, int] = {}
        for c in contribs.values():
            for pat, cnt in (c.get("patterns") or {}).items():
                agg[pat] = agg.get(pat, 0) + int(cnt)
        stats["top_patterns"] = [p for p, _ in sorted(
            agg.items(), key=lambda kv: -kv[1])[:_DISCIPLINES_MAX]]

    # ---------- 继承：用户级 → 单书快照 ----------
    def build_snapshot(self, cfg) -> dict | None:
        """组装项目注入快照（题材匹配过滤 + 上限裁剪）。

        - preferences：genres 仅注入与本书题材匹配的；pacing/avoid 跨题材通用全注入；
        - disciplines：跨 ≥2 本书（book_ids 去重）才注入，Top-N=cfg.user_memory_inject_top；
        - style_anchors：跨书 Top _ANCHORS_INJECT 条，总长 ≤600 字。
        空账本/无可注入内容 → None（调用方不落盘）。
        """
        data = self.load()
        cfg_genres = _split_genres(getattr(cfg, "genre", ""))
        books = int(data.get("stats", {}).get("books", 0)) or 1
        pref = data.get("preferences") or {}

        prefs_lines: list[str] = []
        for g in pref.get("genres") or []:
            if _genre_match(g.get("name", ""), cfg_genres):
                w = min(1.0, int(g.get("count", 0)) / books)
                prefs_lines.append(f"题材偏好：{g.get('name','')}（置信度 {w:.0%}）")
        for p in pref.get("pacing") or []:
            w = min(1.0, int(p.get("count", 0)) / books)
            prefs_lines.append(f"节奏偏好：{p.get('name','')}（置信度 {w:.0%}）")
        for a in pref.get("avoid") or []:
            w = min(1.0, int(a.get("count", 0)) / books)
            prefs_lines.append(f"避雷（用户历史排除）：{a.get('desc','')}（置信度 {w:.0%}）")

        dis = [d for d in data.get("disciplines") or []
               if len(d.get("book_ids") or []) >= 2]          # 跨书确认才注入
        dis.sort(key=lambda d: (-int(d.get("count", 0)), -int(d.get("books", 1))))
        dis = dis[:max(1, int(getattr(cfg, "user_memory_inject_top", 5) or 1))]

        anchors = (data.get("style_anchors") or [])[: _ANCHORS_INJECT]
        total = 0
        anchors_out = []
        for a in anchors:
            t = str(a.get("text") or "")[: _ANCHORS_TOTAL_CHARS - total]
            if not t:
                break
            anchors_out.append({"book": a.get("book", ""), "ch": int(a.get("ch", 0)),
                                "text": t, "length": len(t)})
            total += len(t)

        if not prefs_lines and not dis and not anchors_out:
            return None
        return {
            "version": 1,
            "created_at": _now(),
            "uid": self.uid,
            "preferences": "\n".join(prefs_lines)[:_SNAPSHOT_PREF_CHARS],
            "disciplines": dis,
            "style_anchors": anchors_out,
            "stats": {"books": data.get("stats", {}).get("books", 0),
                      "avg_score": data.get("stats", {}).get("avg_score", 0.0)},
        }

    # ---------- 只读 / 清空（隐私兜底，未来管理 UI 的数据底座） ----------
    def summary(self) -> dict:
        """记忆摘要（只读；返回可序列化结构，供 /api/me/user-memory）。"""
        data = self.load()
        pref = data.get("preferences") or {}
        return {
            "books": data.get("stats", {}).get("books", 0),
            "avg_score": data.get("stats", {}).get("avg_score", 0.0),
            "genres": [g.get("name") for g in pref.get("genres") or []],
            "pacing": [p.get("name") for p in pref.get("pacing") or []],
            "avoid": [a.get("desc") for a in pref.get("avoid") or []],
            "disciplines": [{"pattern": d.get("pattern"), "books": d.get("books"),
                             "count": d.get("count")} for d in data.get("disciplines") or []],
            "style_anchors": len(data.get("style_anchors") or []),
            "top_patterns": data.get("stats", {}).get("top_patterns", []),
        }

    def clear(self) -> None:
        """清空用户级记忆（隐私兜底：删除账本文件，不可恢复）。"""
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            logger.warning("user_memory 清空失败 uid=%s（跳过）", self.uid)
