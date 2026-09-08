# ============================================================
# 墨造 · AI 小说创作平台 — 多用户 SaaS（FastAPI + SQLite）
# 构建：docker build -t novel-gen .
# 运行：docker compose up -d   （推荐，见 docker-compose.yml）
# ============================================================
FROM python:3.13-slim

# ---- 系统依赖：中文字体（封面绘制必须）+ 时区 + 健康检查工具 ----
# 国内服务器访问 Debian 官方源极慢，先替换为阿里云镜像再安装
RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

WORKDIR /app

# ---- Python 依赖（先拷 requirements 利用构建缓存） ----
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple

# ---- 应用代码 ----
COPY . .

# ---- 运行时环境 ----
# 数据库放在 /data 卷（NOVEL_DB_PATH 覆盖默认的 webapp/ 包内路径）
ENV NOVEL_DB_PATH=/data/novel_web.db
ENV PYTHONUNBUFFERED=1

# 持久化目录：projects(中间产物) / output(成品) / logs(日志) / data(数据库)
VOLUME ["/app/projects", "/app/output", "/app/logs", "/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "run_web.py", "--host", "0.0.0.0", "--port", "8000"]
