# -*- coding: utf-8 -*-
"""
命令行入口：AI 自动生成小说。

常用命令：
    python -m novel_gen init   --config config.yaml             # 初始化项目
    python -m novel_gen research --config config.yaml           # 只跑「热门小说共性调研」
    python -m novel_gen plan   --config config.yaml             # 生成大纲（自动含调研）
    python -m novel_gen world  --config config.yaml             # 生成人物/世界观
    python -m novel_gen write  --config config.yaml --from 1 --to 5   # 写指定章节
    python -m novel_gen resume --config config.yaml             # 从断点续跑
    python -m novel_gen all    --config config.yaml             # 一键全流程+导出
    python -m novel_gen export --project <dir>                  # 导出 txt
    python -m novel_gen status --project <dir>                  # 查看进度
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import PROVIDER_DEFAULTS, check_short_novel_hard_constraint, load_config, validate_config
from .llm.factory import create_llm
from .pipeline.manager import NovelPipeline
from .store.project import ProjectStore


def _fail_if_hard_constraint_violated(cfg) -> None:
    """短篇小说字数硬约束：违反时立即报错退出，阻止生成。"""
    msg = check_short_novel_hard_constraint(cfg)
    if msg:
        print(f"[硬约束错误] {msg}")
        print("已阻止生成。请修改 config.yaml 的 total_words（短篇必须 > 7000 字）后重试。")
        sys.exit(1)


def _setup_utf8() -> None:
    """Windows 控制台默认 GBK，统一切 UTF-8 避免中文/特殊字符报错。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _build_parser() -> argparse.ArgumentParser:
    # 共享父解析器：--config / --project 同时挂到顶层与每个子命令，
    # 因此 `all --config X` 和 `--config X all` 两种写法都可用。
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", default="config.yaml", help="配置文件路径（默认 config.yaml）")
    parent.add_argument("--project", default=None, help="已有项目目录；不填则复用最近项目")
    parent.add_argument("--new", action="store_true",
                        help="强制新建项目（默认复用 projects/ 下最近创建的项目）")

    parser = argparse.ArgumentParser(
        prog="novel_gen",
        description="AI 自动生成小说（调研 → 大纲 → 人物世界观 → 逐章正文 → 导出）",
        parents=[parent],
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", parents=[parent], help="初始化项目目录")
    sub.add_parser("research", parents=[parent], help="热门小说共性调研")
    sub.add_parser("plan", parents=[parent], help="生成大纲（自动含调研）")
    sub.add_parser("world", parents=[parent], help="生成人物/世界观")
    p_write = sub.add_parser("write", parents=[parent], help="写章节")
    p_write.add_argument("--from", dest="from_no", type=int, default=1, help="起始章节（默认1）")
    p_write.add_argument("--to", dest="to_no", type=int, default=0, help="结束章节（默认全书）")
    sub.add_parser("resume", parents=[parent], help="从断点续跑")
    sub.add_parser("all", parents=[parent], help="一键全流程并导出")
    sub.add_parser("export", parents=[parent], help="导出 txt")
    sub.add_parser("status", parents=[parent], help="查看项目进度")
    return parser


def _latest_project(root: Path):
    """返回 projects/ 下最近创建的项目目录；不存在则返回 None。"""
    root = Path(root)
    if not root.exists():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir()]
    if not dirs:
        return None
    return max(dirs, key=lambda d: d.stat().st_mtime)


def _attach_api_key(cfg, config_path: str) -> None:
    """打开已有项目时补充 api_key：优先命令行配置文件，其次环境变量。

    项目 config.json 出于安全不落盘 api_key，故打开项目时需在此补回。
    """
    if Path(config_path).exists():
        try:
            cli_cfg = load_config(config_path)
            if cli_cfg.llm.api_key:
                cfg.llm.api_key = cli_cfg.llm.api_key
        except Exception:
            pass
    if not cfg.llm.api_key:
        env_key = PROVIDER_DEFAULTS.get(cfg.llm.provider, {}).get("env_key", "")
        if env_key:
            cfg.llm.api_key = os.environ.get(env_key, "") or ""


def _load_or_create(args) -> tuple[ProjectStore, NovelPipeline]:
    """打开指定项目 / 复用最近项目 / 新建项目，按此优先级。

    打开项目时以项目内配置快照为准（provider/model/字数等），
    api_key 通过 _attach_api_key 从命令行 config 或环境变量补回。
    """
    if args.project:
        store = ProjectStore.open(args.project)
        cfg = store.load_config()
        _attach_api_key(cfg, args.config)
    else:
        root = Path("projects")
        latest = None if args.new else _latest_project(root)
        if latest is not None:
            store = ProjectStore.open(latest)
            cfg = store.load_config()
            _attach_api_key(cfg, args.config)
            print(f"[复用项目] {store.dir}")
        else:
            cfg = load_config(args.config)
            _fail_if_hard_constraint_violated(cfg)
            store = ProjectStore.create(root, cfg)
            print(f"[新建项目] {store.dir}")
    llm = create_llm(cfg.llm)
    pipeline = NovelPipeline(cfg, store, llm)
    return store, pipeline


def _require_outline(pipeline: NovelPipeline):
    if pipeline.store.load_outline() is None:
        print("项目还没有大纲，请先运行：python -m novel_gen plan --config config.yaml")
        sys.exit(1)


def cmd_init(args) -> None:
    cfg = load_config(args.config)
    _fail_if_hard_constraint_violated(cfg)
    issues = validate_config(cfg)
    for i in issues:
        if "硬约束" in i:
            print(f"[硬约束] {i}")
        else:
            print(f"[警告] {i}")
    store = ProjectStore.create(Path("projects"), cfg)
    print(f"[完成] 项目已创建：{store.dir}")
    print(f"下一步：python -m novel_gen research --project {store.dir}")


def cmd_research(args) -> None:
    store, pipeline = _load_or_create(args)
    pipeline.run_research()


def cmd_plan(args) -> None:
    store, pipeline = _load_or_create(args)
    pipeline.run_outline()


def cmd_world(args) -> None:
    store, pipeline = _load_or_create(args)
    pipeline.run_world()


def cmd_write(args) -> None:
    store, pipeline = _load_or_create(args)
    outline = pipeline.store.load_outline()
    if outline is None:
        print("项目还没有大纲，请先运行：python -m novel_gen plan")
        sys.exit(1)
    to_no = args.to_no or len(outline.chapters)
    for no in range(max(1, args.from_no), min(to_no, len(outline.chapters)) + 1):
        pipeline.write_chapter(no)


def cmd_resume(args) -> None:
    store, pipeline = _load_or_create(args)
    _fail_if_hard_constraint_violated(pipeline.cfg)
    pipeline.resume()


def cmd_all(args) -> None:
    store, pipeline = _load_or_create(args)
    cfg = pipeline.cfg
    # 短篇小说字数硬约束（覆盖复用已有项目场景）
    _fail_if_hard_constraint_violated(cfg)
    issues = validate_config(cfg)
    for i in issues:
        if "硬约束" in i:
            print(f"[硬约束] {i}")
        else:
            print(f"[警告] {i}")
    if cfg.llm.provider != "mock":
        est = round(cfg.total_words * 1.6 / 1000, 1)  # 中文 1 字≈1.6 token（粗略）
        print(f"[预估] 全书输出约 {cfg.total_words} 字 ≈ {est}k tokens 输出（不含上下文），请留意成本。")

    # confirm=true 时阶段间暂停；否则自动推进
    def _maybe_pause(stage_name: str):
        if cfg.confirm:
            r = input(f"  —— {stage_name} 完成，按回车继续（输入 q 退出）…… ")
            if r.strip().lower() == "q":
                sys.exit(0)

    pipeline.run_research()
    _maybe_pause("调研")
    pipeline.run_outline()
    _maybe_pause("大纲")
    pipeline.run_editor_review()
    _maybe_pause("编辑质检")
    pipeline.run_world()
    _maybe_pause("人物/世界观")
    done, total = pipeline.progress()
    for no in range(done + 1, total + 1):
        pipeline.write_chapter(no)
    pipeline.export()


def cmd_export(args) -> None:
    store, pipeline = _load_or_create(args)
    out = pipeline.export()
    print(f"[完成] 导出文件：{out}")


def cmd_status(args) -> None:
    store, pipeline = _load_or_create(args)
    cfg = pipeline.cfg
    done, total = pipeline.progress()
    state = pipeline.store.load_state()
    print(f"项目：{store.dir}")
    print(f"《{cfg.effective_title()}》{cfg.genre}｜{done}/{total} 章｜阶段：{state.phase}")
    print(f"产物：{store.dir}")
    for f in sorted(p.name for p in store.dir.glob("*.json")):
        print(f"  - {f}")
    if store.list_chapters():
        print(f"  - chapters/（{done} 章）")


def main() -> None:
    _setup_utf8()
    args = _build_parser().parse_args()
    handlers = {
        "init": cmd_init,
        "research": cmd_research,
        "plan": cmd_plan,
        "world": cmd_world,
        "write": cmd_write,
        "resume": cmd_resume,
        "all": cmd_all,
        "export": cmd_export,
        "status": cmd_status,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
