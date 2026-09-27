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
| 建书 | 弹窗按「生成模式→基础信息→故事设定→节奏与文风→质量选项→费用估算」分组：生成模式置顶（auto 全自动 / expand AI 扩写，选扩写直接展开现有字数/上传大纲/大纲内容/手写内容/附带题材调研）；基础信息含书名/题材/目标字数/每章字数/风格/风格角色/写作技能；故事设定含核心设定/故事背景/主角设定/预设人物（回车标签）/剧情走向/避雷元素（回车标签）；节奏与文风含爽点密度/剧情速度/自定义提示词；质量选项把高质量模式（3 元/万字）与编辑质检/去AI味/防同质化/人物立体化/记忆窗口合并；三种内容类型：① 智能生成（联网调研→大纲→正文）；② AI 扩写（大纲/梗概扩写成文）；③ 续写/扩写已有内容（粘贴或上传手写正文） |
| 大纲 | 上传大纲智能解析自动填表；三级大纲编辑器（总纲→分卷→章节）；章节跨分卷拖拽、分卷增删；大纲快照保存/回滚；单独生成大纲/世界观/分卷 |
| 内容解析 | 上传/粘贴已有正文自动切分章节预览 |
| 生成 | 生成前联网搜索热门小说参考文风（博查 API）；多 agent 流水线（调研员→架构师→写手→审校→读者）；**章节生成按钮三态**（无章节=开始生成 / 有章节未完成=继续生成，成本防护/余额耗尽终止后同样可从断点继续 / 全部完成=重新生成整本重写）；**整本重写** `POST /api/projects/{id}/chapters/regenerate`（保留大纲世界观、锁定章节跳过） |
| 质量 | 去AI味、防同质化、结局优化、番茄写作纪律、Reader 读者视角审校（追更欲评分） |
| 封面 | 硅基流动 ERNIE-Image-Turbo 一体封面；每本限 5 次 |
| 作品管理 | 章节阅读/编辑/重写、**选中段落扩写/续写**、大纲编辑、从某章重生成（免费每日限次）、世界观/调研查看、下载、退款删除、归档、克隆 |
| Skills | 全局写作风格（admin）、我的自定义 Skills、Skill 市场（发布/安装）、**风格角色**（写作风格抽象为 agent 角色，可挂载技能，建书一键套用） |
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
│   │   └── agents/             # 6 个角色（含 StylistAgent 风格师）
│   │       ├── base.py         # BaseAgent（按 role 挂 skill）
│   │       ├── researcher.py   # 调研员
│   │       ├── architect.py    # 架构师
│   │       ├── writer.py       # 写手
  │   │       ├── stylist.py     # 风格师（写作风格角色，可挂载技能）
│   │       ├── reviewer.py    # 审校
│   │       ├── reader.py       # 读者（追更体验）
│   │       └── prompts/      # 各角色独立提示词
│   ├── store/project.py        # ProjectStore
│   ├── store/schemas.py        # Chapter/Outline/Volume
│   ├── utils/chapter_splitter.py
│   ├── utils/paths.py          # DATA_DIR 数据目录解析（代码与数据隔离）
│   ├── utils/migrate.py        # 数据迁移工具（复制→校验→删源）
│   └── export/cover.py        # 封面生成
└── webapp/
    ├── server.py               # FastAPI 组装壳（导入路由注册 + 静态托管 + 启动入口）
    ├── core.py                 # 核心层：共享状态/鉴权/项目工具/结算/生成线程/任务持久化
    ├── routers/                # 按域拆分路由（97 个）：system/auth/me/skills/admin/style_roles
    │                           #   + projects 域 5 子模块：base/outline/chapters/world/generation
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
**风格角色**：`style_roles`（user_id=0 平台内置 / >0 用户自建，role_prompt 人设）+ `style_role_skills`（role_id ↔ skill_type(admin/user)/skill_id 挂载关系）；建书选风格角色后组装人设+挂载技能快照进 config（style_role_prompt），由 StylistAgent 注入写手正文与扩写路径。

---

## 3. API 接口

详见同目录 **API.md**（90 个路由，按系统/账号/我的/Skills/项目/站长后台分组，含鉴权说明）。

---

## 4. 业务规则

### 计费

- 普通 2 元/万字，高质量 3 元/万字；`price_for(words) = round(words/10000*费率, 2)`
- 创建不扣费，生成完成后按 AI 实际字数结算
- 结算口径统一走 `_compute_settle`（增量每章扣 + 整批兜底，共用同一计算，避免口径漂移）
- **资金安全**：增量/整批结算均**先落盘 settle.json 结算基线再扣费**——宁可漏扣（站长后台补偿）也不重复扣；崩溃/重启后按基线跳过，不会双扣
- BYOK（用户自配 Key）完全不扣费
- 余额 ≤ 0 自动停止生成（充值后从断点继续）
- 注册送 10 元体验金（赠送金额不支持退款）
- 代理价 = 原价 × 0.8，返佣 10%

### 生成任务持久化（重启自动重拾）

- 生成线程启动时在项目目录写 `task.json`（kind=generation/outline/batch + start_from/start/end），线程正常结束/用户停止/异常退出时清理
- 进程崩溃或重启时残留 `task.json`；服务启动（lifespan 钩子）扫描全部项目目录，自动重拾中断任务并从断点续跑（章节产物文件是权威来源，不重复生成）
- 并发槽（3 路）不足时跳过并记日志，用户可手动重新触发
- 覆盖：全流程生成（`/start`）、大纲生成（`/outline`）、批量生成（`/chapters/batch`）；「从某章重生成」经 task.json 以 generation 记录同样可重拾

### 重写限额

- 单章重写 + 从某章重生成，共用 10 次/项目/日
- 用户手写章节（source=user）保护，不可 rewrite

### 成本防护（agent_guard）

- 单场景调用次数超限自动终止（`max_loops=200`；场景限额：research 30 / outline 40 / world 25 / **chapter 150** / revise 30 / review 25），终止时已生成产物保留，前端显示「继续生成」可从断点续跑
- 限额可用环境变量覆盖：`AGENT_GUARD_SCENE_<SCENE大写>`（如 `AGENT_GUARD_SCENE_CHAPTER=150`）、`AGENT_GUARD_MAX_LOOPS`

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
| style_roles | id, user_id(0=平台/用户自建), name, description, role_prompt, is_active, sort_order, created_at |
| style_role_skills | id, role_id, skill_type(admin/user), skill_id, sort_order, created_at |

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
| DATA_DIR | 数据目录根（projects/output/logs 的父级，默认代码根）。本地 `D:\NovelData`；服务器不设时容器内回退 `/app`，与既有卷挂载一致，零影响 |

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
| workbench upload 中途 i/o timeout（8%-50% 断） | 本地网络到 OSS 链路不稳定，重试多次可成功（2026-09-23 第 5 次成功）；传输后服务器 `md5sum` 与本地比对校验完整性 |
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
- 结算幂等：增量/整批均先落 settle 标记再扣费（宁可漏扣不重扣）
- BYOK Key 加密落库（Fernet，`enc:v1:` 前缀；存量明文启动时自动迁移）
- 测试随仓库维护：tests/ 已入库（加密/meta 缓存/结算/Agent/风格角色/拆书库共 80 个用例，本地全绿）

---

- 部署记录（2026-09-27）：第七次发版（合并 devtest → master，a04124e）。内容（前端体验 + 稳定性一批）：① **新建小说弹窗输入项完善**（`ProjectList.vue` 重写）——新增每章字数、故事背景、主角设定、预设人物/避雷元素动态标签（回车添加）、剧情走向、爽点密度、剧情速度、自定义提示词、写作技能（平台+我的合并）、风格角色、高质量模式、更多质量开关；默认值与后端一致（chapter_words=2500、pacing=medium、plot_speed=medium、memory_window=5、质量开关全开）；② 修复标签回车无法添加（Vue3 模板 ref 自动解包，改独立函数直接读 ref）；高质量模式补功能说明；③ **大纲导入格式整理**（产出《穿成外婆的纸扎系统_导入大纲.md》11 章可直接导入）；④ **成本防护终止后可继续**——错误提示旁加「继续生成」入口；⑤ **agent_guard 场景限额调大**（max_loops 80→200；chapter 60→150、revise 12→30、review 10→25）+ 补 `AGENT_GUARD_SCENE_<SCENE>` 环境变量覆盖；⑥ **章节生成按钮三态 + 整本重写接口**——无章节「开始生成」/未完成「继续生成」/完成「重新生成」；新增 `POST /api/projects/{id}/chapters/regenerate`（保留大纲/世界观、锁定章节跳过）；⑦ **弹窗布局重排**——生成模式优先、高质量模式与高级选项合并为「质量选项」。另修复 `safety.py` jailbreak 正则第二组可选导致正常文本「系统激活」误判 prompt 注入、创建接口 400 拦截（改必选，新增正反用例）。回归：44 pytest 全绿（P0+P1）、三个冒烟脚本（建表单 19 字段落盘/大纲导入/整本重写 11 章）通过。
- 部署记录（2026-09-24）：第六次发版（合并 devtest → master，75725e3）。内容：**爆款拆书库三件套**——① 基础沉淀：新表 `book_teardown`（genre/title/summary/content JSON/source_project/source_type=research|seed/view_count/status）+ `webapp/teardown_lib.py`（`sink_research` 调研成果自动沉淀：同项目更新、同题材合并、异常只记日志不阻塞生成；`seed_from_knowledge_base` 启动导入「通用」种子幂等）+ `routers/teardowns.py` 4 接口（题材聚合/列表搜索/详情自增浏览数/管理员删除）+ 前端拆书库页（/teardowns，顶栏入口）；② 方案 A：expand 模式可选「附带题材调研」——`NovelConfig.with_research` + `_research_needed(cfg, has_outline)`，expand 勾选时先跑调研（产出 research.json + 沉淀拆书库并作扩写参考）且**不重写用户已导入大纲**；修复 `save_config/load_config` 手写白名单漏落盘 with_research 的缺陷；③ P1 手动拆解：拆书库页「+ 拆解爆款作品」粘贴/上传 .txt/.md/.docx（≥50 字、≤20000 字）→ AI 拆解 12 项写法清单（开篇钩子/悬念钩子/情节套路/人设亮点/文风/节奏/对话技巧/雷区/可借用手法/原文片段）→ `source_type=manual` 入库（`book_teardown` 幂等迁移加 `user_id` 记录投稿人）；参数错误 400、未登录 401、失败 500 不写坏数据，拆解与调研一致不计费。背景：用户主力创作是 expand 模式，expand 因 has_outline=True 跳过调研 → 拆书库长期只有 8 月内置种子。回归：全量 80 pytest 全绿、真实 DeepSeek 手动拆解冒烟通过、前端构建 dist 引用一致。部署：git archive master → workbench upload（MD5 校验一致）→ python3 -m zipfile -e 解压 → docker compose up -d --build；验证：容器 mysql-mozao/novel-gen 均 healthy、域名 200、/api/health 200、主 JS 881KB 无白屏、/api/teardowns 未登录 401。
- 部署记录（2026-09-23）：第五次发版（合并 devtest → master，e975b5d）。内容：恢复「选中段落扩写/续写」前端入口——该功能原在旧原生前端 webui（293773a 引入），随 c575fad 删除 webui 而丢失（后端 `/api/projects/{id}/expand-selection` 一直在）；移植回 Vue 版 `ProjectDetail.vue`：阅读器/编辑器选中 ≥5 字弹出「扩写|续写」浮动条（teleport 到 body），结果 ElMessageBox 预览确认后插入选中文本之后（编辑态取光标处），自动进入编辑态；`api/novel.js` 新增 `expandSelection`。后端零改动；注意该接口不参与计费结算（未走 `_settle_incremental`，原实现如此）。构建验证：npm run build 通过、dist 引用一致。
- 部署记录（2026-09-23）：第四次发版（合并 devtest → master，e5b19f7）。内容：① 用户自有内容导入——上传大纲支持 txt/md/docx（docx 解析章节/正文，上限 5MB），支持粘贴文本（上限 10 万字），创建项目默认原样采用用户大纲、不自动重写，世界观不自动生成（用户手动触发"重生成世界观"后才派生），重导入大纲自动备份旧大纲（`snap_<epoch>`，可还原）；② 大纲快照体验收敛——快照列表新增 `display` 可读时间（自动备份/导入前/重生成前 → "MM-DD HH:MM:SS"），重生成大纲后立即刷新快照列表、生成完成也刷新；工具 tab 移除重复的「重新生成」（大纲/世界观）与「大纲快照」入口，统一到详情页顶部（问题 1-4 全修复）。部署：OSS 上传中途连续超时（8%-50% i/o timeout，4 次），重试第 5 次成功（见 Upload complete 即成功）；MD5 校验一致后 `python3 -m zipfile -e` → `docker compose up -d --build`；验证：容器 healthy、`/api/health` 200、主 JS 881KB 无白屏。
- 部署记录（2026-09-21）：第三次发版（合并 devtest → master，08e8078）。内容：① 生成提示词口头禅频次约束——`writer.md` 限同句口头禅每章 ≤2 次、禁止章末复现/连续多章复读；`architect.md` 禁止把口头禅写进章节 `core_event`/`transition` 作"每章复现"指令（如"以×××结束"）。背景：实测《我的地球Online日记》45 万字"先记下来"出现 150 次，口头禅退化为复读机，且 `repetition.py`（>5 次/万字才禁）拦不住刻意设计的短口头禅，故在提示词层约束。② dist 移出仓库（fix/dist-not-in-repo）。部署方式变更：本次 workbench OSS 上传通道连续超时，改走 `workbench exec` 分块传输（base64 45×24KB → 服务器解码），MD5 校验一致后 `python3 -m zipfile -e` 解压 → `docker compose up -d --build`；验证：容器 healthy、`/api/health` 200（本地+域名）、主 JS 881KB 无白屏。
- 部署记录（2026-09-21）：第三次发版（master 3a16801）。内容：项目详情页两个前端 bug 修复——① 大纲生成完成后轮询只刷新章节列表，未重拉 outline/world 数据，导致进度条消失后切到「大纲」「人物/世界观」仍是空白；改为完成时静默重拉 outline/world/lock/chapters，并给 OutlineEditor 加 :key 强制重挂载。② 点「重生成大纲/重生成世界观」后无进度条：原代码启动后要等 3 秒首次轮询才渲染进度条，且已有章节项目 genDone 初始为 true 导致进度条呈绿色完成态、阶段文字不显示；新增 beginGenUI() 立即置进行中状态，前置阶段（research/outline/editor_review/world）用 indeterminate 流动动画条，writing 阶段回到按章数百分比实心条。部署：git archive master → workbench upload → python3 -m zipfile -e 解压 → docker compose up -d --build，主 JS 881KB/200。
- 部署记录（2026-09-21）：第二次发版（合并 devtest → master，e89d563）。内容：① 数据与代码隔离（DATA_DIR 可配置，新增 `novel_gen/utils/paths.py` + `migrate.py`，4 处路径硬编码统一改为读 DATA_DIR；本地数据已迁至 `D:\NovelData`，代码根无 projects/output/logs；服务器不设 DATA_DIR，容器内回退 `/app` 与卷挂载一致）② 前端导出按钮（项目详情页「导出 ▾」TXT/EPUB/DOCX/MD，blob 下载；顺带修复 `/download` 缺失 shutil import 的历史 bug——前端此前从未调用该接口）③ 新建目标字数下限修复（el-input-number `:min=10000` 钳制，改为 min=0，允许低于 1 万字的短篇）④ 前端产物重建修复白屏（index.html 与构建产物 hash 对齐）。回归：pytest 60 全绿、导出接口冒烟（4 格式 200 + 非法 400 + 未登录 401）通过。
- 部署记录（2026-09-20）：首次服务器部署完成（实例 i-bp14oxtecrth3wbq6thc，/opt/novel-gen）。发版方式：本地 `git archive master` → workbench upload → `python3 -m zipfile -e` 解压 → `docker compose up -d --build`（前端容器内多阶段构建）。部署前发现 compose 漏挂 projects 卷（容器实际已挂），提交 e279eb5 补 `novel_projects:/app/projects` 命名卷后部署，项目数据无缝保留；`.llm_encrypt.key` 随部署放置（BYOK 密文解密依赖）。部署验证：health/域名 200、主 JS 881KB、注册→建书→登出全链路、BYOK 加密列迁移生效、冒烟数据已清理。
- 第四批优化（feat/optimization-batch4）：项目域再拆分——`routers/projects.py`（1150 行/43 路由）拆为 5 个功能子模块（base/outline/chapters/world/generation）。路由路径/方法/返回体不变；回归：路由集合 96=96 一致、29 pytest 绿、端到端 + 业务冒烟通过。
- 第三批优化（feat/optimization-batch3）：最大架构债清理——原 2853 行单文件 `server.py` 拆分为 `webapp/core.py`（共享状态/鉴权/项目工具/结算/生成线程/任务持久化）+ `webapp/routers/`（按域 6 文件、90 个路由）+ `server.py` 组装壳。路由路径/方法/返回体逐字节不变；回归：拆前拆后 openapi 路由集合 96=96 一致、29 个 pytest 全绿、服务端到端 + 注册建书业务冒烟通过。

---

*最后更新：2026-09-27*
