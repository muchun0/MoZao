# -*- coding: utf-8 -*-
"""
墨造核心层·生成线程：预生成注入与后台生成线程编排。

从 core.py 拆出（业务域拆分，行为零变化）。本模块只承载「生成任务编排」一个内聚概念，
包含：拆书库回流/题材锚/用户记忆注入，以及 research+outline+world（_run_outline_only）、
整条流水线（_run_generation）、批量章节（_run_batch）三个后台线程。
不包含鉴权、结算计算主体（结算经 core_settle）与运行槽位管理（经 core_runtime）。

monkeypatch 接缝说明：测试通过 `monkeypatch.setattr(webapp.core, "<helper>", ...)`
补丁门面属性。因此本模块内被测试补丁的 helper（_open_pipeline/_mark_task/_clear_task/
_settle_price/_GEN_SEMAPHORE/_inject_*）一律经延迟 `from . import core` + `core.X()`
解析（与 generate_service 既有模式一致），保证补丁在调用时命中门面；其余 helper
（_LOCK/_RUNS/_settle_incremental/_commit_and_settle/_settle_billing_mode/_is_locked
/_distill_user_memory）不在测试补丁面内，保持模块局部导入。
"""
from __future__ import annotations

import json
import uuid

from novel_gen.pipeline.guard import AgentGuardError
from novel_gen.pipeline.style_anchor import build_teardown_anchor
from novel_gen.store.project import ProjectStore

from . import db
from . import settle_ledger
from . import teardown_lib
from .logger import get_logger
from .user_memory import UserMemoryService
from .core_project import _is_locked
from .core_runtime import _LOCK, _RUNS
from .core_settle import (_commit_and_settle, _settle_billing_mode,
                          _settle_incremental)

logger = get_logger("server")


def _inject_teardown_material(store: ProjectStore, cfg) -> None:
    """P0-3 拆书库回流：调研前把同题材爆款拆书条目格式化为纯文本写入 store，
    供 ResearchEngine 读取并入调研素材（保持 novel_gen 不依赖 webapp 的分层）。
    失败静默降级；空库/无同题材则写空（不注入任何素材）。"""
    try:
        genre = (getattr(cfg, "genre", "") or "").strip()
        items = db.list_teardowns(genre=genre, page=1, page_size=5).get("items", [])
        blocks: list[str] = []
        for it in items:
            detail = db.get_teardown(it.get("id"))
            if not detail:
                continue
            content = detail.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    content = {}
            if not isinstance(content, dict):
                content = {}
            parts = [p for p in [(detail.get("title") or "").strip(),
                                 (detail.get("summary") or "").strip()] if p]
            for k in ("opening_strategy", "opening", "pacing"):
                v = content.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(f"{k}：{v.strip()}")
            for k in ("trends", "plot_patterns", "styles", "avoid",
                      "hooks", "takeaways", "dialogue"):
                v = content.get(k)
                if isinstance(v, list) and v:
                    parts.append(f"{k}：" + "；".join(str(x) for x in v if str(x).strip()))
            if parts:
                blocks.append("\n".join(parts))
        store.save_teardown_material("\n\n---\n\n".join(blocks))
        if blocks:
            logger.info("拆书库回流素材注入完成: project=%s genre=%s %d 条", store.dir.name, genre, len(blocks))
    except Exception:
        logger.exception("拆书库回流素材注入失败（降级跳过）: project=%s", store.dir.name)


def _inject_teardown_style_anchor(store: ProjectStore, cfg) -> None:
    """REQ-双锚风格锚定：拆书库题材文风锚——调研前把同题材爆款拆书条目的
    文风特点（styles）/手法清单（takeaways）/原文片段（excerpts）格式化为纯文本
    写入 store 的 teardown_style_anchor.txt，供写手阶段（scene/chapter/expand
    prompt）注入（弱约束，只借鉴语感，严禁抄袭内容）。

    - 开关 cfg.teardown_style_anchor=False → 写空（不注入）
    - 保持 novel_gen 不依赖 webapp 的分层：本函数只查 DB + 组装 + 写文件，
      消费端在 novel_gen/pipeline/templates.py 读文件内容（经 store 传入）
    - 失败静默降级；首次生成时拆书库可能无同题材条目（调研后才沉淀），
      二次生成/续写时生效
    """
    try:
        if not getattr(cfg, "teardown_style_anchor", False):
            store.save_teardown_style_anchor("")
            return
        genre = (getattr(cfg, "genre", "") or "").strip()
        items = db.list_teardowns(genre=genre, page=1, page_size=5).get("items", [])
        blocks: list[str] = []
        for it in items:
            detail = db.get_teardown(it.get("id"))
            if not detail:
                continue
            content = detail.get("content")
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except Exception:
                    content = {}
            if not isinstance(content, dict):
                content = {}
            anchor = build_teardown_anchor(
                content.get("styles"), content.get("takeaways"), content.get("excerpts"))
            if anchor:
                blocks.append(anchor)
        store.save_teardown_style_anchor("\n\n---\n\n".join(blocks))
        if blocks:
            logger.info("拆书库题材文风锚注入完成: project=%s genre=%s %d 条",
                        store.dir.name, genre, len(blocks))
    except Exception:
        logger.exception("拆书库题材文风锚注入失败（降级跳过）: project=%s", store.dir.name)


def _inject_user_memory(store: ProjectStore, cfg, uid: int) -> None:
    """用户级记忆库继承：跨书偏好/纪律/文风锚 → 项目快照文件（webapp 写 / novel_gen 读）。

    - 开关 cfg.user_memory=False → 不写快照（novel_gen 侧 load 返回 None = 零注入）；
    - 幂等：内容未变不落盘（每次生成入口统一调用，含 start_from>0 从第 N 章重生成）；
    - 保持 novel_gen 不依赖 webapp 的分层：本函数只组装 + 写 store 文件，
      消费端在 novel_gen/pipeline/stages.py 懒读（归属校验 uid 后注入 prompt）；
    - 失败静默降级，不阻塞生成。
    """
    try:
        if not getattr(cfg, "user_memory", True):
            return
        svc = UserMemoryService(uid)
        snap = svc.build_snapshot(cfg)
        if not snap:
            return
        if store.load_user_memory() == snap:
            return
        store.save_user_memory(snap)
        logger.info("用户级记忆注入完成: project=%s uid=%s 纪律=%d 锚=%d",
                    store.dir.name, uid, len(snap.get("disciplines", [])),
                    len(snap.get("style_anchors", [])))
    except Exception:
        logger.exception("用户级记忆注入失败（降级跳过）: project=%s", store.dir.name)


def _distill_user_memory(store: ProjectStore, cfg, uid: int) -> None:
    """书级 → 用户级蒸馏（生成完成/批量后触发）。

    distill 全量重算式幂等（同书同贡献再调用跳过），多入口重复触发安全；
    失败静默降级，不阻塞生成主流程。
    """
    try:
        if not getattr(cfg, "user_memory", True):
            return
        n = UserMemoryService(uid).distill(store.dir.name)
        if n:
            logger.info("用户级记忆蒸馏完成: project=%s uid=%s 变更=%d",
                        store.dir.name, uid, n)
    except Exception:
        logger.exception("用户级记忆蒸馏失败（降级跳过）: project=%s", store.dir.name)


def _run_outline_only(store: ProjectStore, handle) -> None:
    """只跑 research → outline → editor_review → world，不写正文。
    完成后 phase 标记为 outline_done，等用户编辑大纲后再 /start。"""
    from . import core  # 延迟门面引用：经 core.<helper> 解析，保留 monkeypatch 接缝
    cfg = store.load_config()
    trace_id = uuid.uuid4().hex
    core._mark_task(store, "outline")
    logger.info("大纲生成线程启动: project=%s title=%s trace=%s",
                store.dir.name, cfg.effective_title(), trace_id)
    try:
        pipeline = core._open_pipeline(store, trace_id=trace_id)
        project_id = store.dir.name
        mode = _settle_billing_mode(store)
        # P0-3 拆书库回流：调研前注入同题材爆款拆书素材（失败降级）
        core._inject_teardown_material(store, cfg)
        # REQ-双锚风格锚定：拆书库题材文风锚（写手阶段消费，失败降级）
        core._inject_teardown_style_anchor(store, cfg)
        # 用户级记忆库：跨书偏好/纪律/文风锚注入项目快照（失败降级）
        core._inject_user_memory(store, cfg, db.project_owner(store.dir.name))
        pipeline.run_research()
        # 爆款拆书库：调研成果自动沉淀（失败不影响生成，内部已捕获异常）
        teardown_lib.sink_research(pipeline.store.load_research(), store.dir.name)
        if handle.stop_event.is_set():
            return
        pipeline.run_outline()
        if handle.stop_event.is_set():
            return
        pipeline.run_editor_review()
        if handle.stop_event.is_set():
            return
        # v2 计费：大纲在编辑评审定稿后统一提交版本并结算（一次 run 一次提交，§3.10）
        _commit_and_settle(store, "outline", pipeline.store.load_outline(),
                           mode=mode, project_id=project_id)
        pipeline.run_world()
        # v2 计费：世界观同样按产物版本提交并结算（与大纲是两个不同产物）
        _commit_and_settle(store, "world", pipeline.store.load_world(),
                           mode=mode, project_id=project_id)
        if handle.stop_event.is_set():
            return
        # 标记阶段完成
        pipeline._mark_state("outline_done")
        logger.info("大纲生成完成: project=%s chapters=%d", store.dir.name, len(pipeline.store.load_outline().chapters))
    except AgentGuardError as exc:
        # 模块2：成本防护超限——返回已生成的有效结果 + 超限提示，不抛 500
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("大纲生成被成本防护终止: project=%s %s", store.dir.name, exc)
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    except Exception as exc:
        handle.error = f"{type(exc).__name__}: {exc}"
        logger.exception("大纲生成失败: project=%s error=%s", store.dir.name, exc)
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    finally:
        core._GEN_SEMAPHORE.release()
        with _LOCK:
            _RUNS.pop(store.dir.name, None)
        core._clear_task(store)


def _research_needed(cfg, has_outline: bool) -> bool:
    """是否需要执行题材调研：无大纲必跑（auto 全自动）；expand 模式勾选「附带调研」也跑。

    expand 勾选调研时只补调研（research.json + 拆书库沉淀），用户已导入的大纲不被重写——
    是否生成大纲仍由 has_outline 单独决定。
    """
    if not has_outline:
        return True
    return cfg.mode == "expand" and bool(getattr(cfg, "with_research", False))


def _run_generation(store: ProjectStore, handle, start_from: int = 0) -> None:
    """后台线程：从断点续跑整条流水线，结束后自动导出。

    手动编排阶段并在每章之间检查停止标记，使「停止」真实生效。
    start_from > 0 时：跳过 research/outline/world，直接从 start_from 章开始生成
    （用于「从某章重新生成」，此时这些前置产物已存在）。
    """
    from . import core  # 延迟门面引用：经 core.<helper> 解析，保留 monkeypatch 接缝
    cfg = store.load_config()
    trace_id = uuid.uuid4().hex
    core._mark_task(store, "generation", start_from=start_from)
    logger.info("生成线程启动: project=%s title=%s start_from=%d trace=%s",
                store.dir.name, cfg.effective_title(), start_from, trace_id)
    try:
        pipeline = core._open_pipeline(store, trace_id=trace_id)
        project_id = store.dir.name
        mode = _settle_billing_mode(store)
        # REQ-双锚风格锚定：所有生成入口统一注入题材锚（start_from>0 从第 N 章重生成、
        # 复用大纲继续生成也生效；重复注入幂等，失败静默降级）
        core._inject_teardown_style_anchor(store, cfg)
        # 用户级记忆库：跨书偏好/纪律/文风锚注入项目快照（所有生成入口，失败降级）
        core._inject_user_memory(store, cfg, db.project_owner(store.dir.name))

        if start_from > 0:
            done = start_from - 1
        else:
            # 已有大纲则复用，不重新生成（用户上传大纲/之前已生成过的情况）
            existing_outline = store.load_outline()
            has_outline = bool(existing_outline and existing_outline.chapters)
            if _research_needed(cfg, has_outline):
                # P0-3 拆书库回流：调研前注入同题材爆款拆书素材（失败降级）
                core._inject_teardown_material(store, cfg)
                pipeline.run_research()
                # 爆款拆书库：调研成果自动沉淀（失败不影响生成，内部已捕获异常）
                teardown_lib.sink_research(pipeline.store.load_research(), store.dir.name)
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=research", store.dir.name)
                    return
            if not has_outline:
                pipeline.run_outline()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=outline", store.dir.name)
                    return
                pipeline.run_editor_review()
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=editor_review", store.dir.name)
                    return
                # v2 计费：大纲在编辑评审定稿后统一提交版本并结算（一次 run 一次提交，§3.10）
                _commit_and_settle(store, "outline", pipeline.store.load_outline(),
                                   mode=mode, project_id=project_id)
            # 世界观：有则复用，没有则生成
            existing_world = store.load_world()
            if not (existing_world and (existing_world.world_view or existing_world.characters)):
                pipeline.run_world()
                # v2 计费：新建世界观是一个独立产物版本，提交并结算一次
                _commit_and_settle(store, "world", pipeline.store.load_world(),
                                   mode=mode, project_id=project_id)
                if handle.stop_event.is_set():
                    logger.info("生成停止: project=%s 阶段=world", store.dir.name)
                    return
            done, _total = pipeline.progress()

        done, total = pipeline.progress()
        try:
            st = store.load_state()
            st.phase = "writing"
            st.last_chapter = done
            st.total_chapters = total
            st.error = ""
            store.save_state(st)
        except Exception:
            pass
        for no in range(done + 1, total + 1):
            if handle.stop_event.is_set():
                print(f"[web] 收到停止请求，第 {no} 章暂停")
                logger.info("生成停止: project=%s 阶段=writing 第 %d 章暂停", store.dir.name, no)
                break
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("余额耗尽停止: project=%s 第 %d 章前 %s", store.dir.name, no, why)
                break
            # v2 计费：写前快照版本集合，写后只结算本次新增版本（幂等不重扣）
            prev_versions = settle_ledger.snapshot_chapter_versions(store, no)
            pipeline.write_chapter(no, silent=False)
            settle_ledger.settle_chapter_versions(store, no, prev_versions, mode=mode,
                                                  project_id=project_id)
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("余额耗尽停止: project=%s 第 %d 章后 %s", store.dir.name, no, why)
                break

        # 未停止则导出成品，并把状态统一回写为 done，避免 phase 滞后
        if not handle.stop_event.is_set():
            pipeline.export()
            try:
                st = store.load_state()
                st.phase = "done"
                st.last_chapter = len(store.list_chapters())
                outline = store.load_outline()
                st.total_chapters = len(outline.chapters) if outline else st.total_chapters
                store.save_state(st)
            except Exception:
                pass
        logger.info("生成完成: project=%s title=%s", store.dir.name, cfg.effective_title())
    except AgentGuardError as exc:
        # 模块2：成本防护超限——不抛 500，保留已生成产物 + 超限提示（可从断点继续）
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("生成被成本防护终止: project=%s %s", store.dir.name, exc)
        try:
            store.dir.joinpath("failed.json").write_text(
                json.dumps({"error": handle.error, "title": cfg.effective_title(),
                            "guard": True}, ensure_ascii=False),
                encoding="utf-8")
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        handle.error = f"{type(exc).__name__}: {exc}"
        # P1 修复：失败落盘标记，前端可展示 failed 状态与原因
        try:
            store.dir.joinpath("failed.json").write_text(
                json.dumps({"error": handle.error, "title": cfg.effective_title()}, ensure_ascii=False),
                encoding="utf-8")
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
        logger.exception("生成失败: project=%s title=%s error=%s",
                         store.dir.name, cfg.effective_title(), exc)
    finally:
        core._GEN_SEMAPHORE.release()
        core._settle_price(store)
        # 用户级记忆库：书级 → 用户级蒸馏（幂等 + 失败降级；完成/停止/失败均尝试）
        _distill_user_memory(store, cfg, db.project_owner(store.dir.name))
        with _LOCK:
            _RUNS.pop(store.dir.name, None)
        core._clear_task(store)
        logger.info("生成线程结束: project=%s", store.dir.name)


def _run_batch(store: ProjectStore, handle, start: int, end: int) -> None:
    """批量生成章节线程：指定起始/结束章逐章生成。

    v2 口径下每写完一章即按「新增版本」结算一次（批量 N 章 = N 次结算），
    收尾的 `_settle_price` 仅对从未结算过的历史项目做一次兜底（有账本则 no-op）。
    独立成模块级函数，便于启动重拾复用（task.json 记录 start/end）。
    """
    from . import core  # 延迟门面引用：经 core.<helper> 解析，保留 monkeypatch 接缝
    project_id = store.dir.name
    trace_id = uuid.uuid4().hex
    core._mark_task(store, "batch", start=start, end=end)
    try:
        cfg = store.load_config()
        pipeline = core._open_pipeline(store, trace_id=trace_id)
        mode = _settle_billing_mode(store)
        outline = store.load_outline()
        research = store.load_research()
        # 用户级记忆库：跨书偏好/纪律/文风锚注入项目快照（批量入口，失败降级）
        core._inject_user_memory(store, cfg, db.project_owner(project_id))
        for no in range(start, end + 1):
            if handle.stop_event.is_set() or no > len(outline.chapters):
                break
            if _is_locked(store, "chapter", no):
                logger.info("批量生成跳过锁定章节: project=%s no=%d", project_id, no)
                continue
            plan = outline.chapters[no - 1]
            # v2 计费：写前快照版本集合，写后只结算本次新增版本（批量 N 章 = N 次结算）
            prev_versions = settle_ledger.snapshot_chapter_versions(store, no)
            pipeline.chapter_writer.write(no, plan.title, plan.goal, research,
                                          is_final=(no == len(outline.chapters)))
            settle_ledger.settle_chapter_versions(store, no, prev_versions, mode=mode,
                                                  project_id=project_id)
            stop, why = _settle_incremental(store)
            if stop:
                handle.error = why
                logger.warning("批量生成余额耗尽: project=%s 第 %d 章后 %s", project_id, no, why)
                break
        core._settle_price(store)
    except AgentGuardError as exc:
        # 模块2：成本防护超限——保留已生成产物 + 超限提示
        handle.error = f"{exc}（已保留当前已生成内容，可从断点继续）"
        logger.warning("批量生成被成本防护终止: project=%s %s", project_id, exc)
        # 落盘 state.error：线程结束 _RUNS.pop 后 handle 不可见，前端 status 只能读到 state.error，
        # 不落盘则成本防护终止对前端完全无提示（与 _run_outline_only 保持一致）
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    except Exception as e:
        handle.error = f"{type(e).__name__}: {e}"
        logger.exception("批量生成失败: %s", project_id)
        try:
            st = store.load_state()
            st.error = handle.error
            store.save_state(st)
        except Exception:
            pass
    finally:
        core._GEN_SEMAPHORE.release()
        # 用户级记忆库：书级 → 用户级蒸馏（幂等 + 失败降级）
        _distill_user_memory(store, cfg, db.project_owner(project_id))
        with _LOCK:
            _RUNS.pop(project_id, None)
        core._clear_task(store)


__all__ = [
    "logger",
    "_inject_teardown_material", "_inject_teardown_style_anchor",
    "_inject_user_memory", "_distill_user_memory",
    "_run_outline_only", "_research_needed", "_run_generation", "_run_batch",
]
