# -*- coding: utf-8 -*-
"""存量项目 LLM provider 迁移 CLI（REQ-R31：平台消费切到 DeepSeek）。

只改 ``projects/<id>/config.json`` 里的 ``llm.provider / model / base_url`` 三个字段，
其余配置逐字保留；已切换的项目跳过（幂等），mock 项目与用户自配 Key（BYOK）不受影响
（BYOK 在 ``users.llm_*`` 表里，运行时覆盖项目配置，不在本工具范围内）。

用法（项目根下执行）：
    python tools/switch_llm_provider.py --dry-run        # 预演，不写盘
    python tools/switch_llm_provider.py                  # 默认切到 deepseek / deepseek-flash
    python tools/switch_llm_provider.py --project <ID>   # 只处理单个项目
    # 回滚（切回火山方舟）：
    python tools/switch_llm_provider.py --to doubao --model ep-20261108190349-55bnh --force

退出码：0 全部成功（含无需改动）；1 存在写入/解析失败。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# 让脚本从项目根导入（脚本位于 tools/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from novel_gen.llm.capabilities import (  # noqa: E402
    PROVIDER_PROFILES, normalize_provider, resolve_profile,
)
from novel_gen.utils.paths import get_projects_dir, load_env_once  # noqa: E402

logger = logging.getLogger("tools.switch_llm_provider")

# 会被本工具迁移的 provider（即平台此前默认的火山方舟）；其它 provider 需 --force
_SOURCE_PROVIDERS = {"doubao"}
# Ark 默认端点：切走时必须清空，否则会把火山端点带到别家
_ARK_BASE_URL = (PROVIDER_PROFILES.get("doubao") or {}).get("default_base_url", "")
_BACKUP_SUFFIX = ".bak.pre-r31"


def plan_migration(data: dict, to_provider: str, to_model: str,
                   force: bool = False) -> dict | None:
    """计算单个项目需要改写的 llm 字段；无需改动返回 None。

    - 目标已生效（provider/model 均已相等）→ None（幂等）
    - mock 项目 → None（离线冒烟项目不联网，切了反而会打真实 API）
    - 源 provider 不在 ``_SOURCE_PROVIDERS`` 且未 --force → None（不动用户手选的其他家）
    """
    llm = data.get("llm")
    if not isinstance(llm, dict):
        return None
    provider = normalize_provider(llm.get("provider"))
    model = str(llm.get("model") or "").strip()
    if provider == normalize_provider(to_provider) and model == to_model:
        return None
    if provider == "mock":
        return None
    if provider not in _SOURCE_PROVIDERS and not force:
        return None

    changes: dict = {"provider": to_provider, "model": to_model}
    # Ark 端点显式清空，让它回落目标 provider 的档案默认；用户自填的其他 base_url 原样保留
    base_url = str(llm.get("base_url") or "").strip()
    if base_url and base_url.rstrip("/") == _ARK_BASE_URL.rstrip("/"):
        changes["base_url"] = ""
    return changes


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写：临时文件 + os.replace，Windows 文件锁重试后回退直写。"""
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                # Defender/索引服务偶发占用：回退直写（内容已完整序列化，不会写半截）
                path.write_text(payload, encoding="utf-8")
                tmp.unlink(missing_ok=True)
                return
            time.sleep(0.2 * (attempt + 1))


def migrate_config_file(path: Path, to_provider: str, to_model: str,
                        *, dry_run: bool, force: bool) -> str:
    """处理单个 config.json，返回结果标签：changed / dry / skip / failed。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("读取失败 %s：%s", path, exc)
        print(f"  ✗ 读取失败：{path}（{type(exc).__name__}）")
        return "failed"
    changes = plan_migration(data, to_provider, to_model, force=force)
    if not changes:
        return "skip"

    llm = data["llm"]
    before = {k: llm.get(k) for k in ("provider", "model", "base_url")}
    print(f"  → {path.parent.name}: {before['provider']}/{before['model']} → "
          f"{changes['provider']}/{changes['model']}"
          + (f"（base_url {before['base_url']!r} → ''）" if "base_url" in changes else ""))
    if dry_run:
        return "dry"

    backup = path.with_name(path.name + _BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(path, backup)
    llm.update(changes)
    try:
        _atomic_write_json(path, data)
    except OSError as exc:
        logger.error("写入失败 %s：%s", path, exc)
        print(f"  ✗ 写入失败：{path}（{type(exc).__name__}）")
        return "failed"
    return "changed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把存量项目的 LLM provider 批量切到 DeepSeek（幂等）")
    ap.add_argument("--to", default="deepseek", help="目标 provider（默认 deepseek）")
    ap.add_argument("--model", default="deepseek-flash", help="目标模型（默认 deepseek-flash）")
    ap.add_argument("--project", default="", help="只处理指定项目 ID")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要改动的项目，不写盘")
    ap.add_argument("--force", action="store_true", help="连非火山方舟的 provider 一起改（回滚/强制）")
    args = ap.parse_args(argv)

    load_env_once()  # 与 run_web 同口径读 <代码根>/.env，保证 Key 检查结果准确
    to_provider = normalize_provider(args.to)
    if resolve_profile(to_provider) is None:
        print(f"未知目标 provider：{args.to}，可选：{', '.join(PROVIDER_PROFILES)}")
        return 1
    profile = resolve_profile(to_provider) or {}
    env_key = profile.get("env_key", "")
    if to_provider != "mock" and env_key and not os.environ.get(env_key):
        print(f"⚠ 环境变量 {env_key} 未设置：迁移后生成会因缺少 Key 报配置错误")

    root = get_projects_dir()
    if not root.exists():
        print(f"项目目录不存在：{root}")
        return 0
    if args.project:
        targets = [root / args.project / "config.json"]
    else:
        targets = [d / "config.json" for d in sorted(root.iterdir()) if d.is_dir()]

    counts = {"changed": 0, "dry": 0, "skip": 0, "failed": 0}
    print(f"目标：provider={to_provider} model={args.model}"
          f"{'（dry-run）' if args.dry_run else ''} | 扫描 {len(targets)} 个项目")
    for path in targets:
        if not path.exists():
            continue
        counts[migrate_config_file(path, to_provider, args.model,
                                   dry_run=args.dry_run, force=args.force)] += 1

    print(f"完成：变更 {counts['changed']} / 预演 {counts['dry']} / 跳过 {counts['skip']} "
          f"/ 失败 {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
