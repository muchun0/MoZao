# -*- coding: utf-8 -*-
"""启动 AI 小说生成器 Web 服务（多用户 SaaS 版）。

用法：
    python run_web.py                       # 默认 127.0.0.1:8000
    python run_web.py 8080                  # 指定端口
    python run_web.py --host 0.0.0.0 --port 8000   # 局域网访问（给别人用）

API Key 配置（放在服务端，用户看不到）：
    方式一：在项目根目录放一个 .env 文件：
        DEEPSEEK_API_KEY=sk-xxxxx
        BOCHA_API_KEY=sk-xxxxx      # 可选，联网调研用
    方式二：直接设置系统环境变量（export DEEPSEEK_API_KEY=...）
"""
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from webapp.logger import get_logger

logger = get_logger("run")


def _load_env() -> None:
    """从项目根目录 .env 文件读取环境变量（若存在）。"""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    _load_env()

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = "127.0.0.1"
    port = 8000
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    elif args:
        port = int(args[0])

    has_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    logger.info("服务启动: host=%s port=%d deepseek_key=%s",
                host, port, "已配置" if has_key else "未配置")
    print("=" * 56)
    print("  AI 小说生成器（多用户版）")
    print(f"  服务地址：http://{host}:{port}")
    print(f"  DeepSeek Key：{'已配置' if has_key else '未配置（新建项目需选 Mock 或先在 .env 配置）'}")
    print("  浏览器打开上面的地址即可使用")
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)
    try:
        uvicorn.run("webapp.server:app", host=host, port=port)
    finally:
        logger.info("服务停止: host=%s port=%d", host, port)


if __name__ == "__main__":
    main()
