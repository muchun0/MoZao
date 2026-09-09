# 墨造 · AI 小说创作平台 — 项目 Wiki

> 本文档是项目长期知识库（Wiki），记录产品、架构、业务、部署、流程与已知问题。
> 代码更新时同步维护本文档；密钥类信息一律不入库（见 .env，已被 .gitignore 排除）。

---

## 1. 产品概览

**墨造 · AI 小说创作平台**：一个多用户 SaaS 化的 AI 自动写小说工具。

- **核心能力**：用户给定题材/方向（或上传大纲），AI 自动生成完整小说，输出 TXT/EPUB/DOCX，并附带封面。
- **商业模式**：按单本字数档位收费（余额制），注册送 10 元体验金；人工收款（站长手动充值）。
- **形态**：Web 应用（FastAPI 后端 + 静态前端），单机可跑，Docker 可部署。
- **当前线上**：`https://mozao.click`（2026-09-09 HTTPS 上线；http 自动跳转）
- **域名**：`mozao.click`（备案已通过；Let's Encrypt 证书由 Caddy 自动签发/续签）
- **旧地址**：`http://47.114.41.136`（纯 IP 直连已关闭，Docker 端口仅绑定回环 127.0.0.1:8000）

### 关键功能清单

| 模块 | 能力 |
|---|---|
| 账号 | 邮箱注册（验证码）、登录、忘记密码（邮件重置）、会话管理 |
| 建书 | 题材可选、目标字数、自定义提示词、**两种模式**：AI 扩写（用户给大纲/摘要，AI 扩写）与自主生成工作流 |
| 大纲 | **上传大纲文件 → 智能解析 → 自动填表**（webapp/outline_parser.py） |
| 生成 | 生成前**联网搜索热门小说**参考文风情节（博查 API）；短篇硬约束 >7000 字 |
| 质量 | 去 AI 味、防同质化（拒绝套路、人物内在矛盾、代价制、私有细节）、结局优化（人物弧光闭环、升维收尾） |
| 封面 | **硅基流动 ERNIE-Image-Turbo AI 生图**（背景 + 程序精确叠加文字），失败回退程序化 Pillow 封面 |
| 作品管理 | 章节阅读/编辑、章节重生成、世界观/调研查看、下载 TXT、退款删除 |
| 代理分销 | 一级代理：本人消费折扣 + 下级消费返佣 + 邀请码绑定 |
| 退款 | 用户申请余额退款 → 邮件通知站长 → 人工处理 |
| 站长后台 | 用户/充值/代理/退款/日志/统计/设置（仅 is_admin 可登录） |

---

## 2. 技术架构

### 技术栈

- 后端：Python 3.13 · FastAPI · Uvicorn · SQLite（标准库 sqlite3）
- 前端：静态 HTML/CSS/JS（`webapp/webui/`，原生 JS，无框架），FastAPI 挂载
- AI：DeepSeek（文本生成，openai 库）、博查 Bocha（联网调研）、硅基流动 ERNIE-Image-Turbo（封面）
- 依赖约束：`httpx<0.28`（openai 1.x 硬依赖）；`python-multipart`（FastAPI 表单上传必需）
- 部署：Docker（python:3.13-slim + Noto CJK 字体），docker-compose 编排

### 目录结构

```
D:\PythonProjects\DouBao\
├── run_web.py                  # Web 服务入口（手写 .env 加载，环境变量优先）
├── Dockerfile / docker-compose.yml / .dockerignore
├── BRANCH_FLOW.md              # 分支与发布流程（规则）
├── WIKI.md                     # 本文档
├── config*.yaml                # 生成引擎配置（题材/文风/约束）
├── novel_gen/                  # 生成引擎（LLM 调用、调研、流水线、导出、封面）
│   └── export/cover.py         # 封面：AI(ERNIE-Image-Turbo) + 程序化兜底
├── webapp/                     # Web 应用（FastAPI）
│   ├── server.py               # 全部 API 路由
│   ├── db.py                   # SQLite 数据层（init_db 自动迁移）
│   ├── pricing.py              # 定价档位
│   ├── referral.py             # 代理/分销参数
│   ├── verify_code.py          # 邮箱验证码（注册/重置）
│   ├── mailer.py               # SMTP 邮件（退款通知等）
│   ├── logger.py               # 日志
│   ├── admin_cli.py            # 命令行管理工具
│   └── webui/                  # 前端页面（index.html / admin.html / app.js / admin.js）
└── tests/                      # pytest（当前 26 passed）
```

### 数据流（创建一本小说）

1. 前端填表（题材/字数/提示词/模式）→ `POST /api/projects` → 按档位扣费、建项目记录
2. 后台线程执行：联网调研（博查）→ 参考文风 → 生成大纲/世界观 → 逐章生成（DeepSeek）
3. 完成后导出 TXT/EPUB/DOCX 到 output/，生成 AI 封面
4. 用户可下载、阅读、重生成章节；未开始生成可申请退费删除

---

## 3. API 路由一览

| 分组 | 路由 |
|---|---|
| 系统 | GET /api/health · GET /api/genres · GET /api/pricing |
| 大纲 | POST /api/parse-outline（上传文件解析） |
| 账号 | POST /api/send-register-code · /api/register · /api/send-reset-code · /api/reset-password · /api/login · /api/logout |
| 站长后台 | POST /api/admin/login · GET /api/admin/stats|users|recharges|projects|settings|logs|refunds · POST /api/admin/users/{id}/recharge|agent|unagent · GET /api/admin/users/{id}/downline|commissions · POST /api/admin/refunds/{id}/handle |
| 我的 | GET /api/me · /api/me/recharges · /api/me/referral · /api/me/refundable · /api/me/refund-requests · POST /api/me/bind-referrer · /api/me/refund-request |
| 项目 | GET/POST /api/projects · GET /api/projects/{id} · /{id}/outline · /{id}/chapters · /{id}/chapters/{no} · PUT /{id}/chapters/{no} · POST /{id}/regenerate-from/{no} · POST /{id}/cover · GET /{id}/cover · /{id}/world · /{id}/research · POST /{id}/start|stop|refund · GET /{id}/download |

---

## 4. 业务规则

### 定价档位（webapp/pricing.py，改后重启生效）

| 档位 | 字数上限 | 现价（元） | 原价（元） |
|---|---|---|---|
| 极短篇 | ≤5,000 | 0.99 | 2.99（优惠标识） |
| 短篇 | ≤25,000 | 9.9 | — |
| 中篇 | ≤100,000 | 19.9 | — |
| 长篇 | ≤300,000 | 39.9 | — |
| 超长篇 | 不限 | 69.9 | — |

- 创建项目时按目标字数档位扣费（余额制，不足拒绝）；**短篇小说硬约束 >7000 字**（用户偏好，代码层强制）
- 注册赠金 `FREE_REGISTER_BONUS = 10.0` 元（可设 0 关闭）
- 代理价 = 原价 × `AGENT_DISCOUNT`(0.8)

### 代理/分销（webapp/referral.py）

| 参数 | 值 | 说明 |
|---|---|---|
| AGENT_DISCOUNT | 0.8 | 代理本人消费 8 折 |
| COMMISSION_RATE | 0.10 | 下级消费金额 10% 返佣 |
| COMMISSION_BASE | "consume" | 按下级消费返佣（防刷，推荐） |
| REFERRAL_BONUS | 0.0 | 拉新奖励默认关闭（防传销红线） |

- 邀请码 `INV{id}`；下级填邀请码注册时绑定上级；仅"上级是代理"且开启拉新奖励才发注册佣金

### 退款

- 用户可申请"未开始生成"的项目退费（原路扣回余额）+ 删除作品
- 也可申请余额提现退款 → 入库 pending → **邮件通知站长**（ADMIN_EMAIL）→ 站长后台人工批准/拒绝

### 站长规则

- **首个注册用户自动成为站长**（`db.get_user(1) is None` 判定）；后台仅 is_admin 可登录
- 充值 = 人工收款：站长收到转账后后台手动加余额

---

## 5. 数据库结构（SQLite，webapp/db.py）

| 表 | 关键字段 |
|---|---|
| users | id, username(UNIQUE), pass_hash(sha256+salt), email(UNIQUE), balance, is_admin, is_agent, agent_discount, invite_code(UNIQUE), referrer_id, created_at |
| sessions | token(PK), user_id, created_at |
| project_owner | project_id(PK), user_id, **price**（实扣金额，退费用）, created_at |
| recharges | id, user_id, amount, note, created_at |
| commission_log | id, agent_id, buyer_id, amount, kind(consume/recharge/register), project_id, note, created_at |
| refund_requests | id, user_id, amount, reason, status(pending/approved/rejected), admin_note, created_at, handled_at |

- `init_db()` 启动时自动建表 + 老库迁移（email/is_agent/agent_discount/invite_code/referrer_id/price 列增量补充）

---

## 6. 配置（.env，**不入库**）

| 键 | 说明 |
|---|---|
| DEEPSEEK_API_KEY | 文本生成模型 Key |
| BOCHA_API_KEY | 联网调研（生成前搜热门小说参考）Key |
| SILICONFLOW_API_KEY | 封面 AI 生图（ERNIE-Image-Turbo）Key |
| AI_COVER_ENABLED | 封面 AI 开关，默认 1；0 = 只用程序化封面 |
| ADMIN_EMAIL | 站长收件邮箱（退款通知） |
| SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS | SMTP 发信（QQ 邮箱：smtp.qq.com:465 + 授权码） |

- run_web.py 手写解析 .env，**环境变量优先**（不会覆盖已存在的环境变量）
- 容器部署：compose `env_file: .env` 注入，镜像内不存密钥

---

## 7. 服务器与部署

### 阿里云 ECS

| 项 | 值 |
|---|---|
| 实例 ID | i-bp14oxtecrth3wbq6thc |
| 名称 | launch-advisor-20260908 |
| 地域 | cn-hangzhou |
| 规格 | ecs.e-c1m2.large（2核2G）· 40G 磁盘 |
| 系统 | Alibaba Cloud Linux 3（Docker 26.1.3 + Compose v2.27） |
| 公网 IP | 47.114.41.136（**安全组放行 TCP 80**） |
| 服务目录 | /opt/novel-gen |
| 部署形态 | 本地 master 打包上传 → docker compose up -d --build |

### Docker 卷（持久化，勿 `down -v`）

| 卷 | 挂载 | 内容 |
|---|---|---|
| novel_projects | /app/projects | 生成中间产物 |
| novel_output | /app/output | 成品（TXT/EPUB/DOCX/封面） |
| novel_logs | /app/logs | 日志 |
| novel_data | /data | SQLite 数据库 novel_web.db |

### 健康检查

- 容器 HEALTHCHECK → `GET /api/health`；端口映射 `80:8000`

### 远程运维（本机 Windows）

- Workbench CLI：`C:\Users\pc\AppData\Local\Programs\workbench\workbench.exe`（凭据 `~/.workbench/config.json`，AK 模式）
- 常用：`workbench exec -i <实例ID> --command "..."` · `workbench upload <本地> <远程> -i <实例ID> -f` · `workbench list ecs`
- **注意**：服务器未装 git；发布 = 本地 master 打包（tar 排除规则见 BRANCH_FLOW.md）→ 上传 → 覆盖解压 → rebuild

---

## 8. 分支与发布流程（规则）

见 **BRANCH_FLOW.md**（同目录），核心：

```
devtest（开发验证）→ 确认无误 → 合并 master → 推送 → 从 master 打包部署服务器
```

红线：.env 不入库；未验证不合并；服务器只跑 master；数据库/日志/成品不入库。

---

## 9. 常用命令

```bash
# 本地测试
py -3.13 -m pytest tests/ -q          # 26 passed 基线
# 本地启动服务
py -3.13 run_web.py --host 0.0.0.0 --port 8000
# 命令行管理（充值/代理/查用户）
py -3.13 -m webapp.admin_cli users
# 打包部署（master 分支）
tar.exe -czf %TEMP%\novel_deploy.tar.gz --exclude=... -C D:\PythonProjects\DouBao .
# 服务器（经 workbench exec）
cd /opt/novel-gen && docker compose up -d --build
curl http://127.0.0.1:80/api/health
docker compose logs -f novel-gen
```

---

## 10. 已知问题与解决记录（避坑）

| 问题 | 原因 | 解决 |
|---|---|---|
| 容器启动即崩溃 Restarting | FastAPI 表单上传缺 `python-multipart` | requirements.txt 补依赖 |
| 服务器构建 apt 极慢（16KB/s） | 访问 Debian 官方源 | Dockerfile 先 sed 换阿里云镜像 `mirrors.aliyun.com` |
| 硅基流动 API 401 | key 是**国内站**的，`api.siliconflow.com` 不认 | 用 `api.siliconflow.cn`（代码已双域名兜底） |
| AI 封面出现乱码文字 | prompt 注入书名/副标题/题材字面，AI 渲染成文字 | prompt 只描述氛围并强禁文字；文字一律程序叠加 |
| 前端看不到"管理后台"入口 | 浏览器缓存旧版 app.js | 强刷 Ctrl+F5；服务器端已确认无问题 |
| 短篇缺结局 | 生成流程无结局收束 | 结局优化功能：人物弧光闭环 + 升维收尾，已并入流程 |
| PowerShell 执行多行/中文命令报错 | 引号转义 | 写 .py/.sh 文件上传执行，不在命令行内嵌 |

---

## 11. 用户长期偏好（系统已记忆）

1. **短篇小说字数必须大于 7000 字**
2. **代码更新流程：先推 devtest 验证 → 确认后合并 master → 服务器运行 master**
3. 部署方式：A 方案（本地 master 打包上传，服务器不装 git）
4. 收款：人工收款；定价按单本档位；当前 IP:端口内测

---

*最后更新：2026-09-09 · 由部署与开发记录整理*
