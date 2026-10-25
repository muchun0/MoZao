# -*- coding: utf-8 -*-
"""`VersionedProduct` 产物版本化门面（TECH-Agent用户化与版本化 §3.1，阶段 1A）。

统一章节 / 大纲 / 世界观三类产物的版本库实现：

| 产物 | HEAD | 版本存档 | 索引 | 默认上限 |
|---|---|---|---|---|
| chapter | `chapters/<no>.json` | `chapters/<no>/v<k>.json` | `chapter_versions.json` | 15（`version_limit_chapter`） |
| outline | `outline.json` | `outline/v<k>.json` | `outline/versions.json` | 5（`version_limit_outline`） |
| world | `world.json` | `world/v<k>.json` | `world/versions.json` | 5（`version_limit_world`） |

设计要点：
1. **两种提交语义**：
   - `commit(content)`：与 HEAD 签名比较，相同 → None（幂等）；不同 → 追加版本
     （存量无索引有 HEAD → 旧 HEAD 存 v1(legacy)、新内容 v2）。章节保存 / 用户编辑走这条。
   - `commit_head()`：把当前 HEAD 存档为新版本，与最新存档版本签名比较（幂等）。
     管线中间落盘 `commit=False` 只写 HEAD，run 收尾统一调一次（§3.10「一次 run = 一次 commit」）。
2. **提交顺序（§3.2）**：版本存档 → 索引（原子写）→ 最后写 HEAD（HEAD 落地即视为提交完成）。
3. **并发**：per-project `RLock`（第二道防线；第一道是入口 `_acquire_slot`）。
4. **门面只负责存档，不负责扣费**：`commit` 成功后由调用方调 `settle_ledger.settle_version`；
   `list()` 返回的 `words` 供结算与展示复用（§3.1）。
5. **provenance**：`run_id` 缺省从 `store.current_run()`（§3.9，1B 落地）读取，无 run 上下文时
   降级为空串不报错；`upstream=None` 自动读取当前 outline/world selected 版本号（评审 I-2）。
6. `source` 枚举收敛为 `ai|rewrite|user|legacy`（§4.7）；`source="user"` 置 `user_managed`，
   此后 `auto_select_best` 不再覆盖用户选择。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .wordcount import count_words

__all__ = ["VersionedProduct", "fingerprint"]

logger = logging.getLogger("novel_gen.store.versioned")

# per-project 写锁（§3.2 第二道防线）；_LOCKS_GUARD 保护字典本身
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _project_lock(store_dir: Path) -> threading.RLock:
    key = str(store_dir)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def fingerprint(store, files: list[str]) -> str:
    """记忆快照指纹（§3.1）：对给定项目相对路径做 sha256 拼接（路径+内容，缺失文件记空）。"""
    h = hashlib.sha256()
    for rel in files:
        h.update(str(rel).encode("utf-8"))
        h.update(b"\0")
        p = store.dir / rel
        try:
            if p.exists():
                h.update(p.read_bytes())
        except OSError:
            pass
        h.update(b"\0")
    return h.hexdigest()


def _canonical_sig(data: Any) -> str:
    """整产物签名：canonical JSON 的 sha256（字段序漂移免疫）。"""
    text = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chapter_sig(data: Any) -> str:
    """章节签名：只比正文 content 字段（与现网 save_chapter 幂等口径一致）。"""
    if isinstance(data, dict):
        return str(data.get("content") or "")
    return ""


class _KindSpec:
    """产物类型适配器：路径布局 / 签名口径 / 字数统计 / 版本上限开关名。"""

    def __init__(self, kind: str):
        self.kind = kind

    def head_path(self, store, key: str) -> Path:
        if self.kind == "chapter":
            return store.chapters_dir / f"{int(key):03d}.json"
        return store.dir / f"{self.kind}.json"

    def archive_dir(self, store, key: str) -> Path:
        if self.kind == "chapter":
            return store.chapters_dir / f"{int(key):03d}"
        return store.dir / self.kind

    def version_path(self, store, key: str, v: int) -> Path:
        return self.archive_dir(store, key) / f"v{int(v)}.json"

    def index_path(self, store) -> Path:
        if self.kind == "chapter":
            return store.dir / "chapter_versions.json"
        return store.dir / self.kind / "versions.json"

    def norm_key(self, key: Any) -> str:
        if self.kind == "chapter":
            return str(int(key))
        return "all"  # outline/world 为整产物版本库，key 恒为 all

    def sig(self, data: Any) -> str:
        if self.kind == "chapter":
            return _chapter_sig(data)
        return _canonical_sig(data)

    def words(self, data: Any) -> int:
        return count_words(self.kind, data or {})

    def limit(self, store, override: Optional[int]) -> int:
        if override and override > 0:
            return int(override)
        try:
            return int(store._feature_value(f"version_limit_{self.kind}"))
        except Exception:
            return 15 if self.kind == "chapter" else 5


# commit() 的 upstream 自动读取方向（§3.1 / §11.3 回滚影响范围数据源）：
# chapter 依赖 outline+world；world 依赖 outline；outline 无上游。
_UPSTREAM_DEPS = {"chapter": ("outline", "world"), "world": ("outline",), "outline": ()}


class VersionedProduct:
    """产物版本化门面。kind: chapter | outline | world。"""

    def __init__(self, store, kind: str):
        kind = (kind or "").strip().lower()
        if kind not in _UPSTREAM_DEPS:
            raise ValueError(f"不支持的产物类型: {kind}（支持 chapter/outline/world）")
        self.store = store
        self.kind = kind
        self.spec = _KindSpec(kind)

    # ---------- 提交 ----------

    def commit(self, key, content: dict, *, source: str = "ai",
               quality: Optional[dict] = None, promote: bool = True,
               run_id: str = "", upstream: Optional[dict] = None,
               auto_select_best: bool = True, limit: Optional[int] = None) -> Optional[int]:
        """内容与 HEAD 相同 → None（幂等）；否则追加 v{next} 并返回版本号。

        - 存量无索引但有 HEAD：旧 HEAD 存档为 v1(legacy)，新内容为 v2（§3.5）。
        - `promote=False`：只存档+索引，不写 HEAD、不改 selected（候选版本）。
        - `quality`：写入 history（版本弹窗数据源）。
        - `source="user"`：置 `user_managed=True`，此后 auto_select_best 不覆盖。
        - `run_id`：缺省取 `store.current_run()`（无 run 上下文降级为空串）。
        - `upstream`：默认 None = 自动读取当前 outline/world selected 版本号。
        """
        key = self.spec.norm_key(key)
        with _project_lock(self.store.dir):
            head = self.store._read_json(self.spec.head_path(self.store, key), None)
            if head is not None and self.spec.sig(head) == self.spec.sig(content):
                return None  # 幂等：内容未变不追加
            return self._commit_new(key, content, head=head, source=source, quality=quality,
                                    promote=promote, run_id=run_id, upstream=upstream,
                                    auto_select_best=auto_select_best, limit=limit)

    def commit_head(self, key, *, source: str = "ai", run_id: str = "",
                    upstream: Optional[dict] = None, limit: Optional[int] = None) -> Optional[int]:
        """run 收尾唯一提交点（§3.10）：把当前 HEAD 存档为新版本。

        HEAD 与最新存档版本签名一致 → None（幂等，多点落盘不产生中间态版本）；
        无版本库且 HEAD 存在 → HEAD 直接存为 v1（source 为调用方传入——
        此时 HEAD 即本次 run 的新产物，不是 legacy）。
        """
        key = self.spec.norm_key(key)
        with _project_lock(self.store.dir):
            head = self.store._read_json(self.spec.head_path(self.store, key), None)
            if head is None:
                return None
            index = self._load_index()
            rec = self._rec(index, key)
            versions = self._versions(rec)
            if versions:
                latest = self.store._read_json(
                    self.spec.version_path(self.store, key, max(versions)), None)
                if latest is not None and self.spec.sig(latest) == self.spec.sig(head):
                    return None
            return self._commit_new(key, head, head=None, source=source, quality=None,
                                    promote=True, run_id=run_id, upstream=upstream,
                                    auto_select_best=False, limit=limit,
                                    head_already_on_disk=True)

    def _commit_new(self, key: str, content: dict, *, head: Optional[dict],
                    source: str, quality: Optional[dict], promote: bool,
                    run_id: str, upstream: Optional[dict], auto_select_best: bool,
                    limit: Optional[int], head_already_on_disk: bool = False) -> int:
        """追加一个新版本（调用方已持锁、已判幂等）。`head` 为提交前的旧 HEAD（None=无或 HEAD 即新内容）。"""
        index = self._load_index()
        rec = self._rec(index, key)
        versions = self._versions(rec)
        history = list(rec.get("history", [])) if isinstance(rec.get("history"), list) else []

        if not versions and head is not None:
            # 存量初始化：旧 HEAD 存档为 v1(legacy)，新内容为 v2（§3.5）
            self.store._write_json(self.spec.version_path(self.store, key, 1), head)
            versions.append(1)
            history.append({"v": 1, "ts": 0, "source": "legacy"})
            next_v = 2
        elif not versions:
            next_v = 1
        else:
            next_v = max(versions) + 1

        # 1. 版本存档（临时文件 + os.replace，见 store._write_json）
        self.store._write_json(self.spec.version_path(self.store, key, next_v), content)
        versions.append(next_v)
        entry: dict[str, Any] = {
            "v": next_v, "ts": int(time.time()), "source": source,
            "quality": quality if isinstance(quality, dict) else None,
        }
        run_id = self._resolve_run_id(run_id)
        if run_id:
            entry["run_id"] = run_id
        up = self._resolve_upstream(upstream)
        if up:
            entry["upstream"] = up
        history.append(entry)

        user_managed = bool(rec.get("user_managed")) or source == "user"
        selected = next_v if promote else rec.get("selected")

        # 版本上限淘汰最旧（跳过 selected，HEAD 永不淘汰）
        versions, history = self._prune(key, versions, history, selected, limit)

        # 自动选分（仅 promote 且未被用户管理；只比较带 quality.score 的版本）
        if promote and auto_select_best and not user_managed:
            best = self._best_scored(versions, history)
            if best is not None:
                selected = best

        # 2. 索引（原子写）
        index[key] = {"versions": versions, "selected": selected,
                      "history": history, "user_managed": user_managed}
        self.store._write_json(self.spec.index_path(self.store), index)

        # 3. 最后写 HEAD（HEAD = selected 版本内容；commit_head 场景 HEAD 已在盘上）
        if promote and not head_already_on_disk:
            if selected == next_v:
                head_data = content
            else:
                head_data = self.store._read_json(
                    self.spec.version_path(self.store, key, selected), None)
            if head_data is not None:
                self.store._write_json(self.spec.head_path(self.store, key), head_data)
        return next_v

    # ---------- 回滚 / 删除 / 查询 ----------

    def select(self, key, v: int) -> Optional[dict]:
        """回滚：v{k} 复制回 HEAD，置 user_managed（此后自动选分不再覆盖）。无该版本 → None。"""
        key = self.spec.norm_key(key)
        with _project_lock(self.store.dir):
            data = self.store._read_json(self.spec.version_path(self.store, key, v), None)
            if data is None:
                return None
            index = self._load_index()
            rec = index.get(key)
            if isinstance(rec, dict) and v in (rec.get("versions") or []):
                rec["selected"] = v
                rec["user_managed"] = True
                index[key] = rec
                self.store._write_json(self.spec.index_path(self.store), index)
            self.store._write_json(self.spec.head_path(self.store, key), data)
            return data

    def delete(self, key, v: int) -> bool:
        """删除指定历史版本（selected 不可删）。返回是否删除成功。"""
        key = self.spec.norm_key(key)
        with _project_lock(self.store.dir):
            index = self._load_index()
            rec = index.get(key)
            if not isinstance(rec, dict):
                return False
            versions = self._versions(rec)
            if v not in versions or v == rec.get("selected"):
                return False
            p = self.spec.version_path(self.store, key, v)
            if p.exists():
                p.unlink()
            versions.remove(v)
            rec["versions"] = versions
            rec["history"] = [h for h in (rec.get("history") or [])
                              if not (isinstance(h, dict) and h.get("v") == v)]
            index[key] = rec
            self.store._write_json(self.spec.index_path(self.store), index)
            return True

    def load_version(self, key, v: int) -> Optional[dict]:
        key = self.spec.norm_key(key)
        return self.store._read_json(self.spec.version_path(self.store, key, v), None)

    def selected_version(self, key) -> Optional[int]:
        """当前选中版本号（upstream 自动读取用）；无版本库 → None。"""
        key = self.spec.norm_key(key)
        rec = self._load_index().get(key)
        if isinstance(rec, dict) and isinstance(rec.get("selected"), int):
            return rec["selected"]
        return None

    def list(self, key=None) -> list[dict]:
        """版本列表（升序）：{v, selected, source, ts, words, quality, run_id?, upstream?}。

        words 供结算与展示复用（§3.1：避免两处各算一次）。
        每次 list 对单个 key 做轻量对账（§3.2），发现差异只记日志不改数据。
        """
        keys = [self.spec.norm_key(key)] if key is not None else list(self._load_index().keys())
        out: list[dict] = []
        for k in keys:
            report = self.reconcile(k)
            if any(report.values()):
                logger.warning("版本库对账差异: project=%s kind=%s key=%s %s",
                               self.store.dir.name, self.kind, k, report)
            index = self._load_index()
            rec = index.get(k) or {}
            versions = self._versions(rec)
            selected = rec.get("selected")
            history = {h.get("v"): h for h in (rec.get("history") or [])
                       if isinstance(h, dict)}
            for v in sorted(versions):
                data = self.load_version(k, v)
                h = history.get(v) or {}
                item = {
                    "v": v,
                    "selected": (v == selected),
                    "source": h.get("source", ""),
                    "ts": h.get("ts"),
                    "words": self.spec.words(data) if data else 0,
                    "quality": h.get("quality"),
                }
                if h.get("run_id"):
                    item["run_id"] = h["run_id"]
                if isinstance(h.get("upstream"), dict):
                    item["upstream"] = h["upstream"]
                out.append(item)
        return out

    # ---------- 对账（§3.2） ----------

    def reconcile(self, key=None) -> dict:
        """索引 ↔ 文件对账：{orphan_files, missing_files, selected_mismatch}（全空 = 一致）。

        只读不改数据；全量对账走 `tools/version_check.py`（CLI），本方法供 list() 单 key 轻量触发。
        """
        report = {"orphan_files": [], "missing_files": [], "selected_mismatch": False}
        keys = [self.spec.norm_key(key)] if key is not None else list(self._load_index().keys())
        for k in keys:
            index = self._load_index()
            rec = index.get(k) or {}
            versions = set(self._versions(rec))
            archive = self.spec.archive_dir(self.store, k)
            files = set()
            if archive.is_dir():
                for p in archive.glob("v*.json"):
                    try:
                        files.add(int(p.stem[1:]))
                    except ValueError:
                        continue
            orphan = sorted(files - versions)
            missing = sorted(versions - files)
            if orphan:
                report["orphan_files"].extend(f"{k}:v{v}" for v in orphan)
            if missing:
                report["missing_files"].extend(f"{k}:v{v}" for v in missing)
            sel = rec.get("selected")
            if versions and (sel is None or sel not in versions):
                report["selected_mismatch"] = True
        return report

    # ---------- 内部 ----------

    def _load_index(self) -> dict:
        d = self.store._read_json(self.spec.index_path(self.store), {})
        return d if isinstance(d, dict) else {}

    @staticmethod
    def _rec(index: dict, key: str) -> dict:
        rec = index.get(key)
        if not isinstance(rec, dict):
            rec = {"versions": [], "selected": None, "history": []}
        return rec

    @staticmethod
    def _versions(rec: dict) -> list[int]:
        return [v for v in rec.get("versions", []) if isinstance(v, int)]

    def _prune(self, key: str, versions: list[int], history: list[dict],
               selected: Optional[int], limit: Optional[int]) -> tuple[list[int], list[dict]]:
        """超上限淘汰最旧（跳过 selected，HEAD 永不淘汰；selected 为 None 时淘汰绝对最旧）。"""
        cap = self.spec.limit(self.store, limit)
        while len(versions) > cap:
            candidates = [v for v in versions if v != selected]
            if not candidates:
                break
            oldest = min(candidates)
            p = self.spec.version_path(self.store, key, oldest)
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                logger.warning("版本淘汰删除失败: project=%s kind=%s key=%s v=%d",
                               self.store.dir.name, self.kind, key, oldest)
            versions.remove(oldest)
            history = [h for h in history if h.get("v") != oldest]
        return versions, history

    @staticmethod
    def _best_scored(versions: list[int], history: list[dict]) -> Optional[int]:
        """quality.score 最高的版本号（同分取较新版本；无 score 的版本不参与）。"""
        hmap = {h.get("v"): h for h in history if isinstance(h, dict)}
        best_v, best_score = None, None
        for v in versions:
            score = (hmap.get(v, {}).get("quality") or {}).get("score")
            if score is None:
                continue
            if best_score is None or score > best_score or (score == best_score and v > best_v):
                best_score, best_v = score, v
        return best_v

    def _resolve_run_id(self, run_id: str) -> str:
        """run_id 缺省从 store.current_run() 读取（§3.9）；无 run 上下文降级为空串（不报错）。"""
        if run_id:
            return run_id
        current = getattr(self.store, "current_run", None)
        if not callable(current):
            return ""
        try:
            ctx = current()
        except Exception:
            return ""
        return str(getattr(ctx, "run_id", "") or "") if ctx is not None else ""

    def _resolve_upstream(self, upstream: Optional[dict]) -> dict:
        """upstream=None → 自动读取依赖产物的 selected 版本号（评审 I-2）；显式传入则透传。"""
        if upstream is not None:
            return dict(upstream)
        out: dict[str, int] = {}
        for dep in _UPSTREAM_DEPS[self.kind]:
            try:
                sel = VersionedProduct(self.store, dep).selected_version("all")
            except Exception:
                sel = None
            if sel is not None:
                out[f"{dep}_v"] = sel
        return out
