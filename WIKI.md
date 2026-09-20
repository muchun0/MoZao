# 墨造 · AI 小说创作平台 — 项目 Wiki

> 项目长期知识库，记录产品、架构、业务、部署、流程与已知问题。
> 密钥类信息一律不入库（见 .env，已被 .gitignore 排除）。

---

## 1. 产品概览

**墨造 · AI 小说创作平台**：多用户 SaaS 化 AI 自动写小说工具。

- **核心能力**：用户给定题材/方向（或上传大纲/已有手写正文），AI 自动生成完整小说，输出 TXT/EPUB/DOCX，附带 AI 封面。
- **商业模式**：按字数计费（普通 2 元/万字，高质量 3 元/万字），余额制；注册送 10 元体验金；人工收款（站长手动充值）。
- **扣费时点**：创建时不扣费，真正生成文字后按 AI 实际生成字数结算；未生成的项目可随时删除不产生费用。
- **BYOK**：用户可在「模型配置」里填自己的 API Key，用自己的 key 生成完全不扣费。
- **线上地址**：`https://mozao.click`（已备案，冀ICP备2026038211号，Let's Encrypt HTTPS 自动续期）

### 关键功能清单

| 模块 | 能力 |
|---|---|
| 账号 | 邮箱注册（验证码）、登录、忘记密码（邮件重置）、会话管理、登录限流 |
| 建书 | 题材可选、目标字数、节奏控制、三种内容类型：① 智能生成（联网调研→大纲→正文）；② AI 扩写（大纲/梗概扩写成文）；③ 续写/扩写已有内容（粘贴或上传手写正文） |
| 大纲 | 上传大纲智能解析自动填表；三级大纲编辑器（总纲→分卷→章节）；章节跨分卷拖拽、分卷增删；大纲快照保存/回滚；单独生成大纲/世界观/分卷 |
| 内容解析 | 上传/粘贴已有正文自动切分章节预览 |
| 生成 | 生成前联网搜索热门小说参考文风（博查 API）；多 agent 流水线（调研员→架构师→写手→审校→读者） |
| 质量 | 去AI味、防同质化、结局优化、番茄写作纪律、Reader 读者视角审校（追更欲评分） |
| 封面 | 硅基流动 ERNIE-Image-Turbo 一体封面；每本限 5 次 |
| 作品管理 | 章节阅读/编辑/重写、大纲编辑、从某章重生成（免费每日限次）、世界观/调研查看、下载、退款删除、归档、克隆 |
| Skills | 全局写作风格（admin）、我的自定义 Skills、Skill 市场（发布/安装） |
| 计费 | 创建不扣费；按 AI 实际字数结算；余额≤0 自动停止；BYOK 不扣费 |
| 代理分销 | 一级代理：本人消费 8 折 + 下级消费 10% 返佣 |
| 退款 | 用户申请余额退款 → 邮件通知站长 → 人工处理 |
| 站长后台 | 用户/充值/代理/退款/日志/统计/设置/Skills 管理 |

---

## 2. 技术架构

### 技术栈

- 后端：Python 3.13 · FastAPI · Uvicorn · MySQL（库 `mozao`；本地 root/yue_426983）
- 前端：Vue 3 + Element Plus + Vite + Pinia + Vue Router（`webapp/frontend/`）
- AI：DeepSeek（文本）、博查 Bocha（联网调研）、硅基流动 ERNIE-Image-Turbo（封面）
- 部署：Docker 多阶段构建（node:20-alpine 编译前端 → python:3.13-slim 运行），docker-compose 编排（mysql-mozao + novel-gen）

### 目录结构

```
D:\PythonProjects\DouBao\
├── run_web.py                  # Web 服务入口
├── Dockerfile / docker-compose.yml / .dockerignore
├── WIKI.md / API.md / AGENTS.md / README.md
├── requirements.txt
├── novel_gen/                  # 生成引擎
│   ├── config.py               # NovelConfig
│   ├── pipeline/
│   │   ├── manager.py          # 流水线编排
│   │   ├── templates.py        # 提示词模板
│   │   ├── stages.py           # 大纲/世界观/章节写作
│   │   ├── reviser.py          # 自动修订 + Reader 审校
│   │   ├── memory.py           # 章节记忆
│   │   └── agents/             # 5 个角色
│   │       ├── base.py         # BaseAgent（按 role 挂 skill）
│   │       ├── researcher.py   # 调研员
│   │       ├── architect.py    # 架构师
│   │       ├── writer.py       # 写手
│   │       ├── reviewer.py    # 审校
│   │       ├── reader.py       # 读者（追更体验）
│   │       └── prompts/      # 各角色独立提示词
│   ├── store/project.py        # ProjectStore
│   ├── store/schemas.py        # Chapter/Outline/Volume
│   ├── utils/chapter_splitter.py
│   └── export/cover.py        # 封面生成
└── webapp/
    ├── server.py               # 全部 API 路由（91 个）
    ├── db.py                   # MySQL 数据层 + 自动迁移
    ├── secret_encrypt.py       # 敏感配置加密（BYOK Key，Fernet）
    ├── pricing.py              # 按字数计费
    ├── referral.py             # 代理/分销
    ├── verify_code.py          # 邮箱验证码
    ├── mailer.py               # SMTP 邮件
    ├── logger.py               # 日志
    ├── admin_cli.py            # 命令行管理
    └── frontend/               # Vue3 前端（src/ + dist/）
```

### 多智能体角色

| 角色 | 文件 | 职责 | role 值 |
|---|---|---|---|
| Researcher | researcher.py | 联网调研、反套路改造、元素叠加 | researcher |
| Architect | architect.py | 三级大纲、人物世界观、时间线 | architect |
| Writer | writer.py | 逐章正文生成 | writer |
| Reviewer | reviewer.py | 一致性/逻辑/合规审校 | reviewer |
| Reader | reader.py | 追更读者视角审校（代入感/爽点/钩子） | reader |

**Skill 挂载**：`writing_skills.role` 空=全局；填对应 role 只注入该角色；用户 `user_skills` 同理。

---

## 3. API 接口

详见同目录 **API.md**（91 个接口，按系统/账号/我的/Skills/项目/站长后台分组，含鉴权说明）。

---

## 4. 业务规则

### 计费

- 普通 2 元/万字，高质量 3 元/万字；`price_for(words) = round(words/10000*费率, 2)`
- 创建不扣费，生成完成后按 AI 实际字数结算
- BYOK（用户自配 Key）完全不扣费
- 余额 ≤ 0 自动停止生成
- 注册送 10 元体验金（赠送金额不支持退款）
- 代理价 = 原价 × 0.8，返佣 10%

### 重写限额

- 单章重写 + 从某章重生成，共用 10 次/项目/日
- 用户手写章节（source=user）保护，不可 rewrite

### 封面

- 每本限 5 次 AI 封面生成
- `GET /api/projects/{id}/cover` 是 `<img>` 标签直接引用，鉴权用 query 参数 `?token=xxx`（不是 Authorization header）
- 计数存在项目目录 `_cover_count.txt`

---

## 5. 数据库结构（MySQL）

| 表 | 关键字段 |
|---|---|
| users | id, username, pass_hash(bcrypt), email, balance, is_admin, is_agent, agent_discount, invite_code, referrer_id, **llm_provider, llm_api_key（Fernet 密文）, llm_base_url, llm_model**（BYOK）, created_at |
| sessions | token, user_id, created_at |
| project_owner | project_id, user_id, price, discount, created_at |
| recharges | id, user_id, amount, note, created_at |
| commission_log | id, agent_id, buyer_id, amount, kind, project_id, created_at |
| refund_requests | id, user_id, amount, reason, status, created_at, handled_at |
| writing_skills | id, name, description, prompt_append, is_active, role, sort_order, created_at |
| user_skills | id, user_id, name, description, prompt_append, role, is_active, **market_id**, created_at |
| skill_market | id, author_id, name, description, prompt_append, role, downloads, created_at |

- 章节存项目目录文件（不在 DB），`source=ai|user` 记录在文件头
- `init_db()` 启动时自动建表 + 增量迁移

---

## 6. 配置（.env，不入库）

| 键 | 说明 |
|---|---|
| DEEPSEEK_API_KEY | 文本生成模型 Key |
| BOCHA_API_KEY | 联网调研 Key |
| SILICONFLOW_API_KEY | 封面 AI 生图 Key |
| AI_COVER_ENABLED | 封面 AI 开关（1/0） |
| LLM_KEY_ENCRYPT_SECRET | BYOK Key 加密密钥（可选；未配置时自动生成根目录 `.llm_encrypt.key`，已 gitignore，服务器 zip 解压不覆盖） |
| ADMIN_EMAIL | 站长收件邮箱（退款通知） |
| SMTP_HOST/PORT/USER/PASS | SMTP 发信 |
| ALLOWED_ORIGINS | CORS 白名单 |
| DATABASE_URL | MySQL 连接串 |

---

## 7. 服务器与部署

### 阿里云 ECS

| 项 | 值 |
|---|---|
| 实例 ID | i-bp14oxtecrth3wbq6thc |
| 地域 | cn-hangzhou |
| 规格 | 2核2G · 40G 磁盘 |
| 系统 | Alibaba Cloud Linux 3 |
| 公网 IP | 47.114.41.136 |
| 域名 | mozao.click（已备案，冀ICP备2026038211号） |
| HTTPS | Let's Encrypt（到期 2026-12-17，自动续期） |
| 服务目录 | /opt/novel-gen |
| 容器 | mysql-mozao + novel-gen（docker compose） |

### 发版流程

服务器直连 GitHub 常超时，走 workbench 上传：

1. 本地提交并推送 master
2. `git archive --format=zip -o novel_master.zip master`
3. workbench upload 到服务器 `/tmp/`
4. 服务器解压 + `docker compose up -d --build`（build 阶段自动编译前端）

```powershell
$wb = "C:\Users\pc\AppData\Local\Programs\workbench\workbench.exe"
& $wb upload -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou -f novel_master.zip /tmp/
& $wb exec -i i-bp14oxtecrth3wbq6thc -r cn-hangzhou --timeout 600 -c "cd /opt/novel-gen && python3 -m zipfile -e /tmp/novel_master.zip . && docker compose up -d --build"
```

### 本地启动

```powershell
py -3.13 run_web.py --host 0.0.0.0 --port 8000
```

---

## 8. 分支流程

- 新功能从 devtest 切分支开发
- 验证通过合并回 devtest
- 确认无误合并 master
- 服务器只跑 master
- 合并 master 后同步更新 WIKI.md / API.md / README.md / 飞书文档

---

## 9. 已知问题与避坑

| 问题 | 解决 |
|---|---|
| 白屏（主 JS 几百字节） | dist 未打进镜像，确认前端源码已提交后 rebuild |
| 服务器无 unzip | 用 `python3 -m zipfile -e` |
| workbench upload 显示 exit 1 | 看 `Upload complete` 即成功 |
| 硅基流动 401 | 用 `api.siliconflow.cn` |
| 前端改动不生效 | 必须 commit 进 git，容器内 build 才会用新代码 |
| 改了 requirements.txt | 必须 `--build` rebuild |
| BYOK 密文解不开（报错/回退平台 key） | 密钥丢失（清空服务目录或换机）。备份根目录 `.llm_encrypt.key`，或在 .env 固定 `LLM_KEY_ENCRYPT_SECRET`；无法恢复时用户需重新填写 key |

---

## 10. 安全加固记录

- 密码 bcrypt(cost=12)，旧 sha256 自动平滑重哈希
- CORS 白名单（ALLOWED_ORIGINS）
- 登录 IP 限流（5 分钟失败 8 次封 10 分钟）
- 上传文件 10MB 上限
- 余额扣改用 SELECT ... FOR UPDATE 行锁
- 结算幂等（先落 settle 标记再扣费）
- BYOK Key 加密落库（Fernet，`enc:v1:` 前缀；存量明文启动时自动迁移）

---

*最后更新：2026-09-20*
