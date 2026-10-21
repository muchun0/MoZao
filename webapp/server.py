# -*- coding: utf-8 -*-
"""
小说生成 Web 后端（FastAPI）：多用户 SaaS 版。

第三批优化：原 2853 行单文件已按职责拆分——
- webapp/core.py：共享状态、鉴权、项目工具、结算、生成线程、任务持久化
- webapp/routers/：按域拆分路由（system/auth/me/skills/admin/projects*）
- 第四批：projects 域再拆为 5 个功能子模块（base/outline/chapters/world/generation）
- 本文件仅保留组装（导入路由注册 + 静态前端托管 + 启动入口）

业务规则摘要：
1. 用户系统：注册/登录/登出，token 鉴权（MySQL 持久化，库 mozao）
2. 项目隔离：每个项目归属一个用户，只能看/操作自己的项目（管理员可见全部）
3. Key 集中管理：平台 LLM API Key 只放服务端环境变量，前端不可见；
   用户自配 Key（BYOK）加密落库，DB 泄露不暴露明文（见 webapp/secret_encrypt.py）
4. 按 AI 实际生成字数计费：创建项目不扣费，生成完成后按实际字数结算
   （先落盘结算基线再扣费，宁可漏扣不重扣）
5. 人工收款：充值由管理员后台/命令行手动加余额（见 webapp/admin_cli.py）
6. 任务持久化：生成任务落盘 task.json，服务重启自动重拾中断任务
"""
from .core import *  # noqa: F401,F403  触发 app 创建与核心状态初始化
from .routers import (system, auth, me, skills, admin, style_roles, teardowns,
                      assistant, projects_base, projects_outline, projects_chapters,
                      projects_world, projects_generation)  # noqa: F401  触发路由注册

# ---------- 静态前端（同源部署时） ----------
class _NoCacheStaticFiles(StaticFiles):
    """前端静态资源禁用浏览器缓存：html/js/css 返回 no-cache，避免旧 JS 长期缓存导致前端改动不生效。"""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # 304 NotModifiedResponse 无 path 属性（条件请求命中时返回），需跳过；
        # 只有普通文件响应才按扩展名加 no-cache，避免 AttributeError 导致前端资源 500
        path = getattr(resp, "path", None)
        if path and path.endswith((".html", ".js", ".css")):
            resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp


if WEB_DIR.exists():
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        file_path = WEB_DIR / full_path
        if file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(WEB_DIR / "index.html"))

    app.mount("/", _NoCacheStaticFiles(directory=str(WEB_DIR), html=True), name="webui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
