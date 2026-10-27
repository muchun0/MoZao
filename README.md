# 墨造（MoZao）· AI 小说创作平台

> **开源（MIT License）** · AI 网文全自动创作引擎 + 多用户 SaaS 平台
> 仓库：https://github.com/muchun0/MoZao

输入题材/创意，自动产出完整小说。生成前先**调研热门小说共性**（情节/文风/节奏），再按「调研 → 大纲 → 编辑质检 → 人物世界观 → 逐章正文 → 导出」流水线推进，全程可断点续传。

Web 端为**多用户 SaaS**：注册登录、按 AI 实际生成字数计费（统一每万字 3 元）、代理分销、退款申请、站长管理后台。

**开源模式**：代码以 MIT 许可证开源（见 `LICENSE`），任何人可 fork 自部署——自带 API Key 免费使用（密钥只存环境变量，不落代码）；官方托管服务继续收费。欢迎提交 Issue / PR 参与开发（贡献规范见文末「开源与贡献」）。

## 核心特性

- **全自动整本成书**：调研 → 大纲 → 编辑质检 → 人物/世界观 → 逐章正文 → 导出，一键跑完全流程
- **双模式**：`auto`（全自动工作流）/ `expand`（AI 扩写，用户提供大纲或简要内容，AI 扩写为完整小说）
- **十二角色多智能体流水线**：调研 / 架构 / 主题人物 / 世界观规则 / 节奏伏笔 / 写手 / 审校 / 读者 / 风格师 + 开篇 / 结局 / 对白 专项 Agent（详见「多智能体角色」）
- **开篇黄金三章**：开篇 Agent 设计强开篇——第 1 章立主角 + 冲突点燃 / 第 2 章冲突升级 + 主动选择 / 第 3 章小闭环 + 强钩子
- **强制结局**：结局 Agent 生成终局设计卡（终局状态 / 主题落点 / 主角最终选择 / 人物归宿 / 基调），末几章按卡收束，杜绝烂尾；四类合格结局形态 / 开篇锚定对照 / 烂尾黑名单 / 尾声写法
- **质量增强**：编辑质检（9 维审稿，<7 分自动 **diff 式修正**）、去 AI 味写作约束、防同质化常驻提示词、人物立体化（角色卡 6 要素 + 选择代价 + 生活体感 + 对话辨识度）、Reader 读者视角审校（追更欲评分）、AI 痕迹评分、全书终审
- **章节质量防线（防重演闭环）**：拆场景规划对齐大纲人物动线（事件清单确定性派生）→ 正文节拍序列检测（LCS 判定换措辞重演）→ 事件进度硬约束（只推进本场景事件）→ 局部子序列重合判定 → 整章回退即时校验；质检 2.12 情节级重演检测兜底
- **机制数字确定性闭环**：章内/章间状态账本（面板数字 / 生死 / 物品 / 时间 / 事实跨场景承接，零 LLM 方向校验：次数/剩余只许递减、蚀化/进度只许递增），死亡判定通用 + 个性化双标准，杜绝"死而复生、数字错乱"
- **小说级记忆系统（BookMemory）**：设定 / 事件 / 踩坑 / 经验四账本——质检出问题自动记入踩坑账本，重写与后续场景注入本作历史错误（Top-N），同模式 ≥2 次升级永久纪律
- **用户级记忆库（跨书创作偏好继承）**：三层记忆架构补用户级层——从用户全部作品自动沉淀偏好画像（题材/节奏/避雷）+ 跨书纪律（同踩坑模式 ≥2 本书确认才注入）+ 个人文风锚（人工修订跨书登记）；新书创建与生成时自动继承（webapp 写 / novel_gen 读，零额外 LLM，软约束；可在「我的」页只读查看/一键清空）
- **双锚风格锚定**：个人旧稿锚（用户人工编辑过的章节）+ 拆书库题材锚（同题材爆款风格/手法/片段），覆盖正文生成、单章重写、从第 N 章重生成全部写章路径
- **Token 优化**：分章设定卡索引（按章只注入出场人物/生物/地点/势力/伏笔子集）、场景状态纯确定性抽取、一致性/读者/编辑三连校验合并为 1 次调用、定点重写（只重写问题段落窗口）、编辑修正 diff 式——单章 LLM 调用量较改造前大幅下降
- **写作风格 Skill**：站长可在后台配置多套写作模板（番茄爽文 / 晋江细腻 / 西幻史诗 / 悬疑冷峻 / 轻小说日常等），用户创建小说时可选，prompt 自动注入 System Prompt
- **风格角色（StylistAgent）**：写作风格抽象为独立 agent 角色——平台内置（爱潜水的乌贼·克苏鲁悬疑克制风 / 烽火戏诸侯·新古典江湖风 / 番茄爆款·短句快节奏风 / 辰东·洪荒史诗苍凉风）+ 用户自建；每个风格角色含人设（role_prompt）并可**挂载多个技能**，建书一键套用
- **多格式导出**：TXT / EPUB / DOCX / Markdown，项目详情页「导出 ▾」一键下载，另附 AI 生成的小说封面（PNG，每本限 5 次）
- **单章人工编辑 + 继续生成**：可修改任意章节正文，后续章节基于修改继续生成；也可从某章重新生成
- **上传大纲智能解析**：上传 .txt / .md / .docx 大纲文档（≤10MB）或粘贴文本（≤10 万字），AI 自动解析填入新建表单；**创建项目默认原样采用用户大纲，不自动重写**；已建项目可**重导入大纲**，旧大纲自动备份为快照（可一键还原）
- **爆款拆书库**：调研成果自动沉淀（同题材合并更新）+ 内置知识库种子 + **手动拆解爆款作品入库**（AI 拆解开篇钩子/悬念钩子/情节套路/人设亮点/文风/节奏/对话技巧/可借用手法清单）；按题材筛选/搜索；expand 模式建书可选「附带题材调研」；拆解与调研均不计费、失败不阻塞主流程
- **长文一致性**：设定库（世界观/人物卡/伏笔/**生物图鉴/地点卡/机制初始值/时钟规则/势力卡/语言禁忌**）+ 最近 N 章摘要回填 + 章内状态账本（面板数字/生死/物品/时间跨场景承接）+ StageGate 阶段门禁；世界观跟随主角视角逐步展开（揭示计划 `world_reveals`：铺垫/揭示节点/真相锚定词，禁提前剧透）
- **断点续传**：以产物文件为唯一权威来源，中断后只补缺失部分，不重复生成、不浪费 token

## 多智能体角色

生成流水线为**多角色编排**（`novel_gen/pipeline/agents/`），各角色有专属提示词（`prompts/*.md`）与按角色挂载的 skill：

| 角色 | 职责 |
|---|---|
| Researcher 调研 | 内置知识库 + 可选联网搜索（博查/Serper）→ 提炼热门小说共性报告 |
| Architect 架构 | 三级大纲（总纲→分卷→章节）、人物世界观、时间线；收敛为骨架生产者，编排生产层与专项 Agent |
| theme_character 主题人物 | 主题命题 / 三层冲突 / 主角弧光 / 配角动机 |
| world_rules 世界观规则 | 世界观 / 金手指五要素 / 设定禁忌 |
| pacing 节奏伏笔 | 关键节点 / 分卷节奏 / 伏笔回收闭环 |
| Writer 写手 | 逐章正文（场景级生成：拆场景→逐场景→润色，叠加 Stylist 风格人设） |
| Reviewer 审校 | 一致性 / 逻辑 / 合规审校（5 类专项校验 + 机制卡三要素校验） |
| Reader 读者 | 读者视角追更欲评分 |
| Stylist 风格师 | 承载写作风格角色（人设 + 挂载技能），注入写手正文与扩写路径 |
| opening 开篇 | 黄金三章强开篇设计（回填前三章 core_event/goal） |
| ending 结局 | 终局设计卡（终局状态/主题落点/人物归宿/基调），末几章按卡收束防烂尾 |
| dialogue 对白 | 聚合全书人物 speech_style 生成对话风格指南（确定性聚合，零 LLM 不增调用数） |

另有 **StageGate 阶段门禁**（产出 → 准出校验 → 不合格自动重生成 ≤N 轮 → 超限仲裁放行）与 **LogicValidator 独立逻辑校验**（设定一致性/人物 OOC/主线偏移/重复情节/伏笔完整性）。

## 架构

```
┌──────────────────── 前端 Vue3 (webapp/frontend) ────────────────────┐
│  登录 / 书案 / 项目详情 / 管理后台（深夜书房 × AI 工坊视觉语言）        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ REST API（同源部署，dist 由 FastAPI 托管）
┌──────────────────────────▼──────────────────────────────────────────┐
│           Web 服务层 webapp/（FastAPI + MySQL，库 mozao）            │
│  server.py(组装壳) ─ core.py(核心层) ─ routers/(9 域 96 路由)        │
│  db/ 数据层 / secret_encrypt / pricing / referral / mailer          │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ 调用生成流水线（后台线程，任务持久化/断点续跑）
┌──────────────────────────▼──────────────────────────────────────────┐
│              生成核心库 novel_gen/（纯 Python，无 Web 依赖）         │
│  llm/(Provider) research/(调研) pipeline/(多角色+记忆+编排+质量)      │
│  store/(项目落盘/状态机) export/(TXT/EPUB/DOCX/封面)                  │
└─────────────────────────────────────────────────────────────────────┘
  产物落盘：projects/<项目>/（JSON 中间产物，断点续传权威源）
            output/（成品）+ MySQL mozao（用户/余额/订单/skills）
```

**核心模块**（结构级细节以 `docs/architecture.md` 为准）：

| 模块 | 职责 |
|---|---|
| `webapp/server.py` | 组装壳：路由注册 + 静态托管 frontend/dist + 启动入口（不写业务逻辑） |
| `webapp/core.py` | 核心层：鉴权 / 项目工具 / 结算（**先落盘结算基线再扣费**）/ 生成线程 / 任务持久化 |
| `webapp/routers/` | 9 域路由：system/auth/me/skills/style_roles/projects×5（base/outline/chapters/world/generation）/admin |
| `webapp/db/` | MySQL 数据层包（users/sessions/projects/skills/style_roles/teardowns/traces） |
| `webapp/secret_encrypt.py` | BYOK Key 加密落库（Fernet，密文前缀 `enc:v1:`） |
| `novel_gen/llm/` | Provider 抽象：DeepSeek / 火山方舟 / OpenAI / mock；模板方法自动埋点 + 成本防护 + Token 统计 |
| `novel_gen/research/` | 调研阶段：内置知识库 + 可选联网 + 拆书库回流（同题材爆款条目并入调研素材） |
| `novel_gen/pipeline/` | 多角色生成流水线：agents/（十二角色）+ memory + stages + quality + manager 编排 |
| `novel_gen/store/` | 项目落盘、状态机、断点续传（产物 JSON 为唯一权威源） |
| `novel_gen/export/` | TXT/EPUB/DOCX/MD 导出 + AI 封面（Pillow） |
| `webapp/frontend/` | Vue 3 + Element Plus + Vite（`dist/` 由 FastAPI 挂载，不入库） |

> 依赖方向：`webapp` → `novel_gen`；`novel_gen` 不依赖 webapp，可用 mock provider 离线测试。

## Web 界面（推荐，无需命令行）

前端按"墨造 · AI 小说创作平台"设计语言整体实现——**深夜书房 × AI 工坊**视觉（深靛蓝主色 + 暖金强调 + 暖白纸面、衬线标题、墨水晕染进度条），前后端分离：

- 后端：FastAPI（`webapp/server.py`），封装全部流水线为 REST API
- 前端：Vue 3 + Element Plus + Vite（`webapp/frontend/`），构建产物 `dist/` 由 FastAPI 挂载
- 通信：REST API（同源部署）

### 启动

**Windows**：双击 `启动小说生成器.bat`

**任意平台**：
```bash
python run_web.py            # 默认 http://127.0.0.1:8000
python run_web.py 8080       # 指定端口
python run_web.py --host 0.0.0.0 --port 8000   # 允许局域网访问（给别人用）
```

浏览器打开 `http://127.0.0.1:8000` 即可使用。

### Docker 部署（云服务器推荐）

项目提供 `Dockerfile` + `docker-compose.yml`，一条命令部署到任意 Linux 云服务器（compose 包含 **mysql-mozao**（MySQL 8）与 **novel-gen**（应用）两个容器）：

```bash
# 1. 上传项目到服务器（可用 git / scp / 宝塔文件管理），进入项目根目录
# 2. 确保根目录有 .env（API Key / SMTP 配置，见下方），然后：
docker compose up -d --build

# 常用命令
docker compose logs -f        # 实时日志
docker compose ps             # 查看状态（含健康检查）
docker compose down           # 停止（数据保留在卷中）
docker compose down -v        # 停止并清空全部数据（慎用！）
```

**部署要点**：

- **数据库**：**MySQL 8**（库 `mozao`），数据持久化在 `data` 命名卷，容器重建不丢数据；密码走 `${MYSQL_ROOT_PASSWORD}` 环境变量注入
- **前端多阶段构建**：`docker build` 第一阶段用 `node:20-alpine` 在容器内自动 `npm ci && npm run build`，第二阶段把 `dist` 拷进 Python 镜像。**无需在宿主机提前 build 前端、也无需单独上传 dist**
- **镜像**：基于 `python:3.13-slim`，内置中文字体（Noto CJK，封面绘制必须）、时区（Asia/Shanghai）、健康检查
- **数据持久化**：`projects`（中间产物）/ `output`（成品）/ `logs` / `data`（MySQL 数据）命名卷
- **密钥安全**：`.env` 被 `.dockerignore` 排除、**不写入镜像**，通过 compose 的 `env_file` 从宿主机注入；`run_web.py` 遵循"环境变量优先"
- **访问**：默认映射宿主 `8000` 端口（`ports` 左侧可改）；生产环境用 nginx 反代 + HTTPS（服务器配置 `/etc/nginx/conf.d/mozao.conf`，反代 `127.0.0.1:8000`，证书 Let's Encrypt 自动续期）；首次注册用户自动成为站长
- **静态文件 / 封面 / 大纲上传** 均为纯 Python 实现，无外部系统服务依赖

> 注意：构建需服务器可访问 Docker Hub / PyPI（Dockerfile 已用清华 PyPI 源加速）。国内云服务器拉取 `python:3.13-slim` 基础镜像慢时，可给 Docker 配置镜像加速器。

### 功能

- **注册 / 登录**：token 鉴权，数据存 **MySQL**（库 `mozao`）；**注册必填邮箱**（全站唯一、格式校验），**邮箱验证码验证**（6 位数字、10 分钟有效、60 秒冷却、错 5 次作废）；登录支持**用户名或邮箱**；**忘记密码**走邮箱验证码重置，重置后该账号全部会话强制失效；注册即送体验金
- **个人中心（/workspace）**：6 tab 自助管理——概览（余额/可退金额/返佣/下级指标）、账户与账单（充值流水/退款申请）、推广返佣（邀请码绑定/下级列表/返佣明细）、模型配置（BYOK）、我的技能（新建/市场发布安装）、风格角色（增删改/挂载技能/参考文风）
- **我的项目（书案）**：卡片式作品列表，支持**关键词搜索**（书名/题材/进度）与**状态筛选**；未开始项目可直接"退费删除"
- **新建小说（开卷）**：弹窗按「基本信息 → 生成模式 → 故事设定 → 质量选项 → 费用估算」分组；**书名必填置顶**，生成模式切换 `auto` / `expand`，AI 扩写输入仅在 expand 模式展示；质量选项只保留记忆窗口设置，已统一高质量模式（3 元/万字）并全开编辑质检/去AI味/防同质化/人物立体化（无开关）；可**上传大纲文档由 AI 智能解析后自动填入表单**；底部费用估算实时显示
- **项目详情**：**六阶段墨迹进度条**（调研 → 大纲 → 人物 → 正文 → 质检 → 导出）；章节生成按钮三态——无章节「开始生成」、未完成「继续生成」（成本防护/余额耗尽终止后可断点继续）、已完成「重新生成」（整本重写，保留大纲与世界观）
- **退费删除**：创建后尚未开始生成正文（零章节产出）的项目，可一键全额退费并删除作品；退费同步冲正上级代理返佣
- **章节阅读**：沉浸式阅读器——**纸白/护眼/暗夜三主题、字号调节、章节目录、上一章/下一章与键盘 ← → 翻页**
- **单章编辑**：修改任意章节标题/正文，保存后后续章节基于修改继续生成；支持"从此章重新生成"；选中 ≥5 字弹出「扩写|续写」浮动条
- **三级大纲编辑器**：总纲 → 分卷 → 章节，章节可跨分卷拖拽、分卷增删；生成前可编辑大纲与人物世界观
- **充值**：弹层展示「充值请联系站长」+ 微信收款二维码 + 计费说明，扫码转账 → 站长人工确认到账（**创建不扣费，生成完成后按 AI 实际字数结算**）
- **代理中心**：查看邀请码、推广链接、下级列表、返佣明细
- **退款申请**：充值余额可申请退款，站长邮件通知 + 后台人工处理

### 多用户（SaaS）模式

- **首个注册用户自动成为管理员（站长）**，拥有全部历史项目与所有用户项目可见权
- **注册必填邮箱 + 验证码**：全站唯一（统一存小写）；登录输入框同时支持用户名和邮箱
- **忘记密码**：验证码重置，重置后所有会话失效
- **邮件发送**：验证码发用户本人邮箱，退款通知发站长（`ADMIN_EMAIL`）
- **API Key 只放服务端**（`.env` 或系统环境变量）；用户自配 BYOK Key **加密落库**（Fernet，密文前缀 `enc:v1:`），DB 泄露不暴露明文
- **按目标 AI 字数封顶收费**：创建项目不扣费，生成完成后按 AI 实际生成字数结算（用户手写内容不计费），每万字 3 元；AI 多写的部分平台承担
- **多用户并发**：不同用户生成互不阻塞，全局信号量 3 并发防过载
- **未生成可退**：零章节产出可全额退费删除；已产出章节不可退
- **代理折扣**：代理本人建书享折扣（默认 8 折）
- **消费返佣**：下级每笔消费，上级代理按比例返佣（默认 10%，自动入余额）

**API Key 配置**（在项目根目录创建 `.env`）：
```
DEEPSEEK_API_KEY=sk-你的Key
BOCHA_API_KEY=sk-你的Key      # 可选，联网调研用
DATA_DIR=D:\NovelData         # 可选：数据目录根（projects/output/logs 的父级），默认代码根
```

> 完整环境变量清单（含 ARK/OPENAI/SERPER/SMTP/MYSQL_ROOT_PASSWORD/DB_*）与一键自部署步骤，见上文「自部署指南（Self-hosting）」。

- **邮件通知**（退款申请通知站长 + 注册/重置验证码发用户邮箱）：
```
ADMIN_EMAIL=站长收件邮箱
SMTP_HOST=smtp.qq.com         # QQ 邮箱用 smtp.qq.com，163 用 smtp.163.com
SMTP_PORT=465
SMTP_USER=发件邮箱
SMTP_PASS=发件邮箱SMTP授权码    # 不是登录密码，是邮箱设置里的 SMTP 授权码
```

**定价**（`webapp/pricing.py` 可改）：

- **统一单价：每万字 3 元（高质量模式，普通模式下线）**
- **创建项目不扣费**；生成完成后按 **AI 实际生成字数**（全书总字数 − 用户手写已有字数）从余额结算
- 用户目标字数自定，多写多算
- 注册即送 10 元体验金（可在 `webapp/pricing.py` 关闭或调整）
- 封面生成：每本书限 **5 次** AI 生图

### 代理 / 分销推广

现实代理制的简化实现（单级直推，合规防刷）：

- 每个用户有专属邀请码（`INV+用户ID`），别人注册时填写即绑定为下级（关系长期有效，不可更换）
- 代理本人建书享折扣（默认 8 折，可在 `webapp/referral.py` 调整）
- 下级每笔真实消费，上级代理得 10% 返佣（自动入余额，可继续创作）
- 注册拉新奖励默认关闭（可配置开启）
- 所有商业参数集中在 `webapp/referral.py`：`AGENT_DISCOUNT` / `COMMISSION_RATE` / `COMMISSION_BASE` / `REFERRAL_BONUS`

站长开通代理：管理后台 → 用户管理 → "设为代理"，或命令行：
```bash
python -m webapp.admin_cli agent <用户名> 0.8    # 设为代理（8 折）
python -m webapp.admin_cli unagent <用户名>       # 取消代理
python -m webapp.admin_cli agents                 # 列出所有代理
python -m webapp.admin_cli downline <用户名>      # 查看某用户的下级
python -m webapp.admin_cli commissions <用户名>   # 查看某代理的返佣明细
```

### 退款申请

- 用户在充值弹层点"申请退款"，输入金额和原因提交
- **可退金额** = 累计充值（管理员手动充值的钱）－ 已退金额；注册赠金、代理返佣不可退
- 提交后系统自动发邮件通知站长（需配置 SMTP）；未配置邮件时申请仍入库，站长可在后台看到
- 站长在管理后台 → 退款管理 中处理：**确认已转账并退款** 或 **拒绝退款**（填备注）
- 防重复申请：有未处理申请时禁止再次提交

### 站长管理后台

独立页面 `http://127.0.0.1:8000/admin.html`，**仅站长（is_admin）可登录**，普通用户返回 403。站长登录前台后顶栏也会出现"管理后台"入口。

包含 9 个板块（与前台统一"墨造"设计语言）：

| 板块 | 功能 |
|---|---|
| 📊 经营概览 | 用户数、代理数、项目数、充值总额、消费总额、累计返佣、注册赠金；资金流向对比图 |
| 👥 用户管理 | 用户列表（余额/身份/折扣/邀请码/下级数/返佣），行内充值、设/取消代理、查下级 |
| 🔗 代理管理 | 代理列表，查看各代理返佣明细 |
| 📚 项目总览 | 所有用户的项目（标题/归属/题材/章节进度/状态） |
| 💰 退款管理 | 退款申请列表（全部/待处理/已退款/已拒绝），人工处理 |
| 💳 余额流水 | 全站充值与消费流水 |
| 📈 质量报表 | 质检分布 / 重写率 / 问题 TopN / 弃书风险 / 阶段成本（8 指标卡 + 得分分布） |
| 📜 系统日志 | 实时查看服务日志尾部（黑底终端样式） |
| ⚙ 系统参数 | 定价档位、返佣规则、注册赠金、邮件配置状态、版本 |

### 命令行管理（站长备用）

```bash
python -m webapp.admin_cli users                       # 查看所有用户
python -m webapp.admin_cli balance <用户名>             # 查余额
python -m webapp.admin_cli recharge <用户名> <金额>      # 人工充值
python -m webapp.admin_cli recharges [用户名]            # 查充值记录
```

### 日志系统

服务关键行为全部记录到 `logs/app.log`（单文件 5MB，自动轮转保留 3 个备份），同时输出到控制台：

- 服务启动/停止、用户注册/登录（含失败原因）
- 项目创建、生成线程启停、每阶段（调研/大纲/质检/人物）、每章开始与完成（含字数）、章节失败（带异常堆栈）
- 资金操作：充值、退款申请/处理、消费/充值返佣
- 代理操作、导出下载、邮件发送成功/失败

日志中**不记录**密码、token、API Key 等凭据。站长可在管理后台"系统日志"板块直接查看。

## 安装

```bash
pip install -r requirements.txt
```

依赖：FastAPI / Uvicorn（Web 服务）、openai（LLM SDK，含 httpx<0.28 版本锁定）、PyYAML（配置）、Pillow（封面）、ebooklib（EPUB）、python-docx（DOCX，另用于上传大纲解析）、pymysql（MySQL）。

## 快速开始（命令行）

**第一步：无 Key 冒烟跑通（mock 本地假模型）**

```bash
# 拷贝示例配置并改用 mock
cp examples/config.yaml config.yaml
# 编辑 config.yaml，把 llm.provider 改为 mock
python -m novel_gen all --config config.yaml
```

会生成 `projects/<时间戳_书名>/` 下的调研报告、大纲、6 章正文和 txt 成品。验证架构没问题后，再换真实模型。

**第二步：接入真实模型（DeepSeek 为例）**

```yaml
llm:
  provider: deepseek      # 或 doubao / openai
  api_key: ""             # 填 Key，或留空读取环境变量 DEEPSEEK_API_KEY
```

```bash
python -m novel_gen all --config config.yaml
```

## 命令一览

```bash
python -m novel_gen init      --config config.yaml        # 初始化项目
python -m novel_gen research  --config config.yaml        # 只跑「热门小说共性调研」
python -m novel_gen plan      --config config.yaml        # 生成大纲（自动含调研）
python -m novel_gen world     --config config.yaml        # 生成人物/世界观
python -m novel_gen write     --config config.yaml --from 1 --to 10   # 写指定章节
python -m novel_gen resume    --config config.yaml        # 从断点续跑
python -m novel_gen all       --config config.yaml        # 一键全流程+导出
python -m novel_gen export    --project <项目目录>         # 导出 txt
python -m novel_gen status    --project <项目目录>         # 查看进度
```

- 不填 `--project` 时默认**复用最近创建的项目**继续操作；开新书用 `--new`。
- 配置 `confirm: true` 后，`all` 会在调研/大纲/人物阶段之间暂停等你确认方向。

## 配置项（config.yaml）

| 配置 | 说明 | 默认 |
|---|---|---|
| `title` | 书名 | 未命名小说 |
| `genre` | 题材：玄幻/仙侠/都市/科幻/历史/悬疑/言情/游戏/奇幻…（可自定义） | 玄幻 |
| `total_words` | 单本目标总字数 | 100000 |
| `chapter_words` | 单章目标字数 | 2500 |
| `style` / `premise` | 文风要求 / 核心创意（可留空） | 空 |
| `background` | 故事背景/世界观（**强约束**） | 空 |
| `hero` | 主角设定：姓名/身份/性格/目标（**强约束**） | 空 |
| `characters` | 配角/反派预设列表（**强约束**，可留空让 AI 补） | 空 |
| `plot_direction` | 剧情走向/关键情节（**参考方向**） | 空 |
| `forbidden` | 避雷/不想出现的元素列表（**强约束**） | 空 |
| `mode` | `auto` 全自动 / `expand` AI 扩写（需提供 expand_input） | auto |
| `expand_input` | 扩写模式下的大纲或简要内容 | 空 |
| `confirm` | 阶段间是否暂停人工确认 | false |
| `memory_window` | 生成当前章时回填最近 N 章摘要 | 5 |
| `editor_review` | 大纲生成后执行编辑质检，不通过自动修正 | true |
| `de_ai` | 正文生成时注入去 AI 味写作约束 | true |
| `anti_similarity` | 防同质化常驻提示词，拒绝套路 | true |
| `character_depth` | 人物立体化：角色卡一致性/选择代价/生活体感/对话辨识度 | true |
| `llm.provider` | deepseek / doubao / openai / mock | deepseek |
| `llm.model` / `base_url` | 留空取该 Provider 默认 | 自动 |
| `search.*` | 调研阶段联网搜索（可选） | 关闭 |

> 进阶行为开关（防重演、小说级记忆、双锚、揭示跟踪、Token 优化等）统一走 `config.py` 的 **FEATURE_FLAGS 注册表**（如 `scene_plan_align` / `book_memory` / `teardown_style_anchor` / `reveal_tracking` / `verify_combined` 等，默认 True），可在代码中按 memory/writing/review/guard 模块分组调整。

## 用户自定义设定（强约束 vs 参考方向）

在 `config.yaml` 中填写以下字段，AI 会按约束强度区别对待：

```yaml
background: "架空民国上海滩，1935年，租界与华界并立，百乐门是各方势力交汇点"
hero: "顾沉，32岁，前巡捕房探长，现为私家侦探，沉默寡言，随身带旧怀表"
characters:
  - "林曼：百乐门歌女，真实身份是地下党联络员"
  - "赵署长：巡捕房署长，贪腐，与黑帮勾结"
plot_direction: "码头命案牵出旧搭档假死真相，最终在百乐门设局揭露赵署长"
forbidden: ["穿越", "系统", "超自然力量", "狗血三角恋"]
```

| 字段 | 约束强度 | 注入阶段 | 说明 |
|---|---|---|---|
| `background` | **强约束** | 大纲/人物/每章正文 | 世界观必须符合，不得偏离 |
| `hero` | **强约束** | 大纲/人物/每章正文 | 主角姓名/身份/性格必须遵循 |
| `characters` | **强约束** | 大纲/人物 | 预设人物必须出现并符合设定，AI 可补充其他角色 |
| `forbidden` | **强约束** | 大纲/人物/每章正文 | 避雷元素绝对不得出现 |
| `plot_direction` | 参考方向 | 大纲 | AI 在此方向上细化发挥，不强制逐字遵循 |

强约束会在每章正文提示词中以「强约束提醒」形式重复注入，防止长篇写作中跑偏。

## 质量增强（编辑质检 + 去AI味 + 防同质化 + 人物立体化）

程序集成了网文编辑视角、去AI味、防同质化、人物立体化四个质量模块，默认全部开启：

```yaml
editor_review: true       # 大纲生成后执行编辑质检，不通过则自动修正一次
de_ai: true               # 正文生成时注入去AI味写作约束
anti_similarity: true     # 防同质化：常驻System提示词，拒绝套路
character_depth: true     # 人物立体化：角色卡一致性/选择代价/生活体感/对话辨识度
```

**编辑质检**（`editor_review`）：大纲生成后，模拟资深网文编辑从 9 个维度审稿——题材标签、一句话卖点、开篇钩子、主角目标/困境、情绪回报节奏、人物关系、前三章闭环、连载风险、防同质化。评分低于 7 分或有致命问题时，基于编辑建议修正大纲（**diff 式**：只输出需改字段，Python 合并回原大纲，省 Token）。审阅结果保存在项目目录 `editor_review.json`。

**去AI味**（`de_ai`）：每章正文提示词注入 8 条写作约束——避免连接词堆砌、避免强行升华和结尾金句、对白有潜台词、叙事有详略、人物有犹豫和矛盾、用具体动作代替抽象情绪、保留留白、句长有变化。

**防同质化**（`anti_similarity`）：常驻 System 提示词注入大纲/人物/正文三阶段，核心规则：
1. 拒绝概率最高套路，剧情分叉优先选小众、次概率、反模板路径
2. 人物永远带内在矛盾（执念+软肋+阴暗面+行为悖论），由性格矛盾驱动行为
3. 剧情必须有代价与沉没成本，禁止无代价胜利、巧合救场、主角光环
4. 只展示不叙述，情绪通过动作/微表情/生理反应/细节侧面体现
5. 全网套路黑名单：打脸流、无脑爽、俗套桥段、畸形人设、机械升级、烂俗结尾、强行巧合
6. 允许失败、遗憾、不完美结局，拒绝流水线爽文闭环

关闭任一功能只需在 config 中设为 `false`。

**人物立体化**（`character_depth`，基于番茄小说课程《告别千人一面：带你写出有血有肉的人物》提炼）：解决"人物扁平、情绪干瘪、千人一面"问题，注入两个阶段：

- **人物阶段（角色卡要求）**：每个重要人物的角色卡强制写全 6 要素——身份标签、经历背景、性格核心（拒绝"善良/邪恶"式空词）、欲望与软肋、行为范式（小动作/口头禅）、压力反应（胃疼/失眠/走神）。
- **正文阶段（写作技法）**：① 人设一致性——行为必须能从经历/性格/欲望/处境解释得通；② 选择加成本——重要胜利必须付真实代价；③ 生活体感——用具体身体反应承载情绪；④ 出场标签 + 对话辨识度——新角色登场带「他是谁/跟谁有关/为何此刻重要」，不同身份性格说话方式必须不同。

## 联网搜索调研（可选，增强"参考热门小说"的真实性）

默认调研数据源 = 内置热门小说共性知识库 + 模型先验知识（零配置可用）。若想真实抓取网上热门小说信息，配置一个搜索 API Key：

```yaml
search:
  provider: bocha          # bocha(博查) / serper(Google)
  api_key: "你的key"        # 或留空读环境变量 BOCHA_API_KEY / SERPER_API_KEY
  count: 5                 # 每个关键词返回条数
```

配置后，调研阶段会自动搜索「题材+热门/套路/趋势」等关键词，把结果连同内置知识库一起交给 LLM 提炼共性。搜索服务失败或未配置时自动降级，不影响主流程。

## 目录结构

```
novel_gen/
├── config.py            # 配置层（YAML + 环境变量补 Key + FEATURE_FLAGS 开关注册表）
├── safety.py            # 输入防护：Prompt 注入检测 / System 清洗 / 输出合规校验
├── llm/                 # Provider 抽象（base/factory/providers/usage，自动埋点+成本防护）
├── research/            # 调研阶段（内置知识库 + 联网搜索 + 拆书库回流）
├── pipeline/            # 生成流水线（agents/ 十二角色 + memory/stages/quality/beats/manager）
├── store/               # 项目存储（schemas/project，产物 JSON 权威源）
├── export/              # 封面生成 + EPUB/DOCX/MD 多格式导出
└── utils/               # 章节切分 / 数据迁移 / 路径
webapp/
├── server.py            # FastAPI 组装壳（路由注册 + 静态托管 + 启动入口）
├── core.py              # 核心层：鉴权/项目工具/结算/生成线程/任务持久化
├── routers/             # 9 域路由：system/auth/me/skills/style_roles/projects×5/admin（96 路由）
├── db/                  # MySQL 数据层（base/users/projects/skills/style_roles/teardowns/traces）
├── secret_encrypt.py    # BYOK Key 加密（Fernet，enc:v1:）
├── pricing.py           # 定价档位（统一 3 元/万字）
├── referral.py          # 代理商业参数（折扣/返佣比例/返佣基数）
├── mailer.py            # 邮件通知（验证码/退款通知）
├── verify_code.py       # 邮箱验证码（6 位/10 分钟/60s 冷却）
├── outline_parser.py    # 上传大纲文档解析（.txt/.md/.docx）
├── logger.py            # 统一日志（文件滚动 + 控制台）
├── admin_cli.py         # 站长命令行管理工具
└── frontend/            # Vue 3 + Element Plus 前端（src/，产物 dist/）
docs/                    # 架构/文件索引/决策记录（AI 助手知识库，见 AGENTS.md）
tests/                   # 本地测试（已入库，612 用例全绿）
examples/config.yaml     # 示例配置
projects/<项目>/         # 中间产物：调研/大纲/人物/章节json/摘要（断点续跑用）
output/                  # 最终成品：txt / epub / docx / 封面 png
logs/                    # 服务日志（app.log，自动轮转）
```

- `projects/`：每本书一个子目录，保存调研/大纲/人物/章节等中间产物，支持 `resume` 断点续跑；列表页统计走 `meta.json` 轻量缓存（章节未变化时零文件内容读取）
- `output/`：最终成品统一输出到这里，每本书一个文件；项目目录下也保留一份副本

## 测试

```bash
python -m pytest tests/ -v
```

用 `mock` provider 离线跑通全部流程，无需任何 API Key。覆盖：配置加载、字数换算、项目落盘往返、记忆上下文、断点续传幂等、正文摘要剥离、txt 导出、强制结局判定、HTML 标签清洗、结算（P0）、加密（P0）、防重演闭环、揭示跟踪等。

> 测试用例按 P0/P1/P2 分级（`@pytest.mark.p0/p1/p2`）；发版前至少 `python -m pytest tests/ -m "p0 or p1" -v`（P0 全绿才允许发版）。当前全量 **793 pytest 全绿**。

## 说明与边界

- API Key 不落盘到项目目录：项目 `config.json` 只存 Provider/模型等，Key 每次从配置文件或环境变量补回。
- 输出字数是"目标值"，实际由模型决定；可在 `llm.max_tokens` 调大单次上限。
- 联网搜索需第三方搜索 API Key，未配置时调研退化为内置知识库 + 模型先验，质量仍可用。
- 邮件通知需配置 SMTP（QQ/163 邮箱的 SMTP 授权码），未配置时退款申请仍可提交，站长在后台手动查看处理。
- 本工具用于个人创作辅助，请勿用于批量生产侵权或违反平台规则的内容。

## 自部署指南（Self-hosting）

开源仓库可一键自部署（Docker Compose，含 MySQL），**自带 API Key 免费使用全部功能**；不配 Key 的用户走平台计费（见上文定价）。

### 前置要求

- Docker 20.10+ 与 Docker Compose v2（`docker compose` 子命令）
- 建议 Linux 服务器 ≥2 核 2G 内存；Windows/macOS 桌面 Docker 同样可跑

### 1. 获取代码

```bash
git clone https://github.com/muchun0/MoZao.git
cd MoZao
```

### 2. 配置环境变量（.env）

在项目根目录创建 `.env` 文件（已被 .gitignore 排除，**不会提交/写入镜像**）。完整清单：

| 变量 | 必填 | 说明 |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | ✅ | MySQL root 密码（compose 初始化 `mysql-mozao` 容器用，务必改为强密码） |
| `DEEPSEEK_API_KEY` | 至少一种 | DeepSeek 文本生成 Key（默认 Provider，`llm.provider=deepseek`） |
| `ARK_API_KEY` | 可选 | 火山方舟（豆包）Key，`llm.provider=doubao` 时用 |
| `OPENAI_API_KEY` | 可选 | OpenAI 兼容 Key，`llm.provider=openai` 时用 |
| `BOCHA_API_KEY` | 可选 | 博查联网调研 Key；未配置则调研退化为内置知识库 + 模型先验 |
| `SERPER_API_KEY` | 可选 | Serper（Google）联网调研 Key，与博查二选一 |
| `ADMIN_EMAIL` | 可选 | 站长收件邮箱（退款申请通知；不配则申请仅后台可见） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` | 可选 | SMTP 发信（注册/重置验证码、退款通知）；不配则邮箱验证码不可用 |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 可选 | 应用连 MySQL 的连接参数（默认 `127.0.0.1:3306 / root / 空 / mozao`，单机 compose 一般无需修改） |
| `DATA_DIR` | 可选 | 数据目录根（projects/output/logs 的父级）；不设时容器内回退 `/app`，与命名卷挂载一致 |
| `LLM_KEY_ENCRYPT_SECRET` | 可选 | BYOK Key 加密密钥；不配置则自动生成根目录 `.llm_encrypt.key`（已 gitignore） |

最小可用 `.env`：

```bash
MYSQL_ROOT_PASSWORD=改成你的强密码
DEEPSEEK_API_KEY=sk-你的Key
ADMIN_EMAIL=admin@example.com
```

### 3. 启动

```bash
docker compose up -d --build
```

首次构建会自动编译前端（容器内多阶段构建：node:20-alpine `npm ci && npm run build` → python:3.13-slim），等待数十秒后：

```bash
docker compose ps          # 两个容器均 healthy 即就绪
```

浏览器打开 `http://<服务器IP>:8000`，**首个注册用户自动成为站长**。

> 默认 `ports` 只绑定 `127.0.0.1`（`127.0.0.1:8000:8000`，安全默认）。需要公网访问时，改 `docker-compose.yml` 为 `8000:8000`，或加 nginx 反代 + HTTPS（服务器 nginx 配置 `/etc/nginx/conf.d/mozao.conf` 反代 `127.0.0.1:8000`，证书由 Let's Encrypt 自动续期）。

### 4. BYOK：用户自带 API Key

- 注册登录后，**个人中心 → 模型配置（BYOK）**：用户可填自己的 Provider / API Key / 模型 / Base URL（不填则用平台 Key）
- Key 经 **Fernet 加密后落库**（密文前缀 `enc:v1:`，加密密钥在 `.llm_encrypt.key` 或 `LLM_KEY_ENCRYPT_SECRET`），DB 泄露不暴露明文
- 用自带 Key 生成**完全不扣费**；未填用户走平台 Key 按字数计费（统一 3 元/万字）
- ⚠️ 迁移/备份服务器时，**务必一起备份 `.llm_encrypt.key`**（或固定 `LLM_KEY_ENCRYPT_SECRET`），否则用户已保存的 BYOK Key 无法解密

### 5. 日常运维

- 日志：`docker compose logs -f`（也可在站长后台「系统日志」板块查看）
- 数据：`projects`（中间产物）/ `output`（成品）/ `logs` / `data`（MySQL 数据）均为命名卷，容器重建不丢数据
- 升级：`git pull && docker compose up -d --build`（改了 requirements 或前端源码必须 `--build`）
- 备份：备份 4 个命名卷 + `.env` + `.llm_encrypt.key` 即可完整迁移

## 开源与贡献

- **许可证**：MIT（见 `LICENSE`）。代码可自由使用、修改、分发；官方托管服务与品牌「墨造」保留运营权。
- **自部署**：见上文「自部署指南」——clone → 建 `.env` → `docker compose up -d --build`，自带 API Key 免费使用。
- **分支流程**：从 `devtest` 切功能分支（`feat/*` / `fix/*` / `docs/*`）→ 提交 → 合并回 `devtest`（先推 devtest 验证）→ 确认无误后合并 `master`。
- **测试要求**：功能改动必须写 pytest 用例（`tests/`），覆盖正常/边界/异常路径；提交前本地 `python -m pytest tests/ -m "p0 or p1" -v` 全绿。
- **敏感信息**：API Key / 数据库密码只进环境变量或 `.env`（已被 .gitignore 排除），**禁止写入代码**。提交前可用 `git grep` 复查。
- **开发文档**：项目规则见 `AGENTS.md`；架构/文件索引/决策见 `docs/`；接口与业务细节见 `WIKI.md`。

## 更新记录

- **2026-10-21**：第二十八次部署（服务器代码 = master，**已上线**）——**小墨AI 一期（REQ-小墨AI v1.3 / TECH-小墨AI v1.1，feat/req-assistant-mvp）**：项目页新增「小墨AI」写作对话，只读项目产物、不写正文、不改稿、不扣费（一期不限次）。
  - 上线内容：① 服务层 `webapp/assistant/`——`prompt.py`（读 `agents/prompts/assistant.md`，mtime 缓存+降级）、`context.py`（四层上下文：人设+Bible / 事实块 / 对话窗口 / 提问，按 token 预算固定档位裁剪）、`cards.py`（确定性卡片：质检扣分 / 问题清单 / 版本对比 / 正向实测）、`service.py`（输入三闸→章节解析→组装→LLM→失败降级→输出合规→落库）；② 数据层 `conversations`/`messages` 建表 + 会话 CRUD/消息分页/最近 N 轮；③ 路由 `routers/assistant.py` 5 条（`_require_user` + 项目归属 + 会话归属）；④ 开关 assistant 模块 6 项（含 `assistant_require_user_key` 默认开：未配 BYOK 直接 400 提示去「模型设置」，不消耗平台额度；`assistant_quota_enabled=False` 一期不限次）；全局开关覆盖抽取 `webapp/config_overrides.py`，生成链路与小墨AI 共用；⑤ 前端「小墨AI」tab（会话/模式/章节/卡片/定位跳章）。
  - 口径红线：review score 只称「质检扣分（缺陷分）」；正向卡只给实测值 vs 阈值（阈值 import 自 `pipeline/ai_taste.py`，单一事实来源），不合成任何质量分；`writer.md` 写作方向仅作背景参考、非评价判据；未接入确定性检测的指标卡片明示缺口。
  - 回归：P0/P1 相关 **91 pytest 全绿**（含 assistant 4 个测试文件）；`npm run build` 通过，dist 引用一致（`index-CFQxeOIJ.js`）。
  - 部署：`git archive master` 打包 → workbench upload → 解压 `/opt/novel-gen` → `docker compose up -d --build`；线上验证：容器 healthy + 主页不白屏。

- **2026-10-18**：第二十七次部署（服务器代码 = master **e54383f**，**已上线**）——**第二十五/二十六/二十七次合并（门禁/质检正向影响改造 P0/P1/P2 + 前端交互收尾）+ 运维清理（删除一次性脚本 / 管理端日志修复 / Caddy→nginx）一次部署覆盖全部内容**：
  - 上线内容：① 第二十五次合并 P0-1~P0-5 三层正向闭环（合规红线=删旧稿重写不落盘 / 首次教育分级 / 质检诊断回喂 / good 转正+多样性治理 / 追更欲正向锚+streak+进度盘）；② 第二十六次合并 P1/P2 全链路（Reflexion 反思笔记 / StageGate 增强旁路 / 建议指令库 / BeatGate 正向供给 / scene_inspector 闭环 / 重复正向替换供给 / SPC 控制图+变异测试防刷数 / 申诉撤销+误判率）；③ 第二十七次合并（P1-3-B 多版本候选 / P2-3 前端申诉按钮 / 进度盘同步全书质检视图）；④ 运维清理：删除技能收敛与 SQLite 迁移一次性脚本（deploy/*.py）、修复管理端系统日志 500（admin.py logger 导入路径 . → ..）、反代文档 Caddy 改 nginx、新增 `tests/test_admin_logs.py`。
  - 回归：p0/p1 全量 **509 pytest 全绿**。部署：git archive master 打包 → workbench upload → 解压 /opt/novel-gen → `docker compose up -d --build`；线上验证：容器 healthy + 主页不白屏。

- **2026-10-18**：第二十五次合并（devtest → master，**96bb478，未部署**）——**门禁/质检正向影响改造 P0-1~P0-5（feat/req-positive-gating）**：把门禁/质检从「负向查错-打回」升级为「三层正向闭环」（生成前预防 / 生成中引导 / 生成后反馈）。依据《门禁质检正向影响调研报告_2026-10-18.md》（跨行业 6 领域收敛的 5+1 类正向模式）：
  - **P0-1 合规红线 = 删旧稿重写不落盘**：合规命中（`safety._COMPLIANCE_RULE_CAT` 9 词表 → 露骨色情描写/危险行为教唆/未成年人性化 3 大类）强制 `_rewrite_whole(skip_original=True)` 整章全新重写；最终稿仍违规 → `compliance_rejected=True`，stages 有健康旧版保留旧版、无旧版整体不落盘/不入版本库；best 择优与兜底排除合规稿。新增 `pipeline/compliance.py`（has_compliance / compliance_categories / compliance_education_block）。
  - **P0-2 首次教育分级**：`basic_content_check_structured` 结构化合规校验（issue 带 rule_category/rule_phrase）；`compliance_education_block` 指向内容不指向人（M6 文化层），book_memory `mark_compliance_educated` 本书内首次去重。
  - **P0-3 质检诊断回喂**：新增 `pipeline/diagnosis.py`（suggest_for 精确表+前缀表+AI痕迹兜底）；check 补 fix_suggestion/expected_gain；重写改用带建议的 build_issue_block（reask 非盲重写）；reader_review 输出 suggestion。
  - **P0-4 good 转正 + 多样性治理**：`record_good` 分层入账（demo 示范池/cand 候选池，hook≥4 或 score≥85 → demo）；来源隔离（user/external 优先 + 本作限量 ≤50% 单条 ≤200 字）；采样轮换 pick_state 持久化；注入末尾多样性纪律（每章借鉴 1-2 特征/禁连续复用）；`_style_homogeneity_issues` 跨章趋同软提示（level=小改不扣分）——防 good 片段重复套路。
  - **P0-5 追更欲正向锚 + streak + 进度盘**：`record_hook_streak` 连续达标账本（hook≥4 累计、<4 冻结一次/重置）；hook_score 透传记忆与 trace；webapp `_aggregate_quality` 增 chapter_status 每章色块（红=未过 / 绿=hook≥4 / 黄=自动重写 / 灰=通过）。
  - 回归：全量 **822 pytest 全绿**（新增 test_compliance_positive / test_quality_diag / test_hook_positive 29 用例）。**部署：master 已合并推送（96bb478），服务器暂未部署（待后续开发完成一并上线）。**

- **2026-10-18**：第二十六次合并（devtest → master，**未部署**）——**门禁/质检正向影响改造 P1/P2（feat/req-positive-gating-p1）**：把正向闭环从 P0 骨架铺到全程（读时/审稿/重写/监控全链路）：
  - **前端进度盘色带（P0-5 落地）**：Admin.vue 质量报表 tab 增「每章状态（进度盘）」el-card，`_aggregate_quality.chapter_status` 色块（红=未过 / 绿=hook≥4 / 黄=自动重写 / 灰=通过），el-tooltip 章号/质检分/通过/自动重写/追更欲；`npm run build` 通过、dist 引用一致。
  - **P1-1 Reflexion 反思笔记**（开关 `reflexion_note` 默认 True）：`quality.ensure_quality` 重写≥2 轮仍未通过 → book_memory `reflections` 账本记「错在哪/下次怎么写对」（guard revise 成本账）；写章 memory_context 注入（场景+整章）。
  - **P1-2 StageGate 增强旁路**（开关 `enhance_pass` 默认 False 控成本）：质检通过但仍有 warn「中改」或追更欲 hook<4 → 一次 LLM 增强润色（钩子更强/爽点更密/伏笔呼应），复检通过且分数不降才采纳。
  - **P1-3 建议指令库**（开关 `suggestion_lib` 默认 True）：`manager.editor_review` 审稿建议按手法分类（对话-潜台词/战斗-动作链/情绪-白描+点破/环境-物象/叙事-节奏，复用 good 分类框架、零 LLM 关键词）沉淀进 book_memory `suggestion_lib`；写章注入建议块。
  - **P1-4 BeatGate 正向供给**（开关 `beat_positive_supply` 默认 True）：missing 拍不仅列「漏了」，同时给「可往哪个方向写」示范指引（零 LLM 规则模板）存入 beat_gate；下一章生成注入兑现。
  - **P1-5 scene_inspector 闭环**（复用 `scene_inspector` 开关）：`collect_suggestions` 抽 bad/warn 维度建议 → store `inspector_suggestions` 落盘（webapp scene-inspect 触发）→ 下一章生成注入（读时体检 → 生成反馈闭环）。
  - **P2-1 重复正向替换供给**（开关 `repetition_direction` 默认 True）：重复类问题命中重写时，除「每事件只写一遍」禁令外，注入「新方向候选」（上一章未兑现承诺 + 本章场景设计推进方向），让重写知道「往哪走」。
  - **P2-2 SPC 控制图 + 变异测试防刷数**：`quality.ensure_quality` 采集每章一次通过/重写轮数/合规命中指标（store `quality_metrics`）；新增 `pipeline/spc.py` I-MR 个体值-移动极差控制图 + `quality_spc_report` 聚合（admin quality-report 接口补 spc）；`tests/test_mutation.py` 故意写坏章节断言检测器能检出（防躺平刷通过）。
  - **P2-3 申诉撤销 + 误判率**：store `appeals` 读写/撤销/误判率接口（misjudge_rate=overturned/total）；webapp POST 申诉 / 撤销(overturn) / 列表三接口；合规 issue detail 已含命中词组（「phrase」式，P2-3a 供复核）。
  - **P0-4 多样性治理**（承接用户质疑「good_block 转正注入是否导致 AI 内容越来越一致」）：来源隔离/质量分层/采样轮换/软约束强化/手法层同质度度量五件套。
  - 回归：全量 **874 pytest 全绿**（P1 五项 34 用例 + P2 变异/SPC/申诉 18 用例；850→874）。**部署：master 已合并推送（96bb478），服务器暂未部署（P0/P1/P2 一并待开发完成后上线）。**

- **2026-10-18**：第二十七次合并（devtest，**master 未合并，未部署**）——**门禁/质检正向影响改造 P1-3-B + 前端交互收尾（feat/req-positive-gating-p1）**：
  - **P1-3-B 多版本候选（Sudowrite 式，用户拍板「改大纲为人工挑选」）**：`editor.revise_candidates` 生成 3 个不同侧重（结构节奏/人物差异化/悬念钩子）修订候选（diff 式 + 递增 temperature）；**深拷贝隔离候选**（修复 `_apply_updates` 原地修改共享 outline 致候选互相污染）、指纹去重、全败兜底整章重出；store `outline_candidates` 读写 + `apply`（候选设为当前大纲）；`manager.editor_review` 未通过时生成候选存 store 由用户挑（开关 `outline_multi_candidate` 默认 True，关=保持自动修正）；webapp `GET /outline/candidates` + `POST /outline/candidates/{index}/apply`；前端 OutlineEditor 候选卡片区（标题/简介/章节摘要 + 采用按钮，采用后清空并刷新）。回归新增 `tests/test_outline_candidates.py` 5 用例。
  - **P2-3 前端申诉按钮**（后端三接口已备）：章节「版本」弹窗每条改稿建议后加「申诉」→ 弹框填理由（必填校验）提交 `POST /chapters/{no}/appeal`（带 issue_type），计入误判复核；取消/失败分流。
  - **进度盘同步到全书质检视图**：`build_review_summary` 的 summary 补 `chapter_status` 四色（未过=红 / 追更欲≥4=绿 / 自动重写=黄 / 通过=灰）；`_save_review` quality_check 落 `hook_score`（供绿色分支）；ToolsPanel「全书质检」卡片渲染色带 + 四色图例（与 Admin 质量报表一致），每色块 tooltip 章号/质检分/追更分/自动重写。
  - 回归：全量 **881 pytest 全绿**（P1-3-B 5 + chapter_status 2 用例；879→881），npm build 通过、dist 引用一致。**部署：master 未合并，服务器未部署（开发完成，待一并上线）。**

- **2026-10-18**：第二十四次发版（服务器代码 = master，**已上线**）——**超长篇生成攻坚全部（第一批 + 第二批 + 二期，REQ-超长篇生成攻坚.md）**，治长文四大天花板（记忆稀释 / 文风漂移 / 大纲承诺衰减 / 成本线性爆炸）：
  - **第一批**（ec2ba11）：① beat 门禁扩展（`beat_gate.py` loose 去标点匹配 / 章型白名单 `detect_chapter_type` 对话=引号≥0.35、过渡=<50字 / 节点优先级 P0/P1/P2）；② 章末承诺台账（`pending_commitments.py` committed/missed/planted 清单 + 下章补偿块）；③ 注入位置工程（补偿块前置 memory_context 最前）；④ 双锚开关治理（`teardown_style_anchor`/`personal_style_anchor` 默认关）。
  - **第二批**（bf912dc，降本 + 中成本）：S4.3 `json_schema_output`（response_format json_object）+ `state_extract_frequency` 3→5；S4.1 `prefix_cache_aligned`（稳定块前置 user 最前，最大化前缀缓存命中，命中价 0.02/M vs 未命中 1/M）；S1.1 `foreshadow_original_text`（伏笔原文句存档注入到期章）；S2.2 `style_reanchor_every`(5) 周期滚动风格锚；S3.3 L2 挂账镜头（连续漏兑现→下章镜头，章型豁免）；S2.4-L1 确定性漂移检测（句长波动系数/TTR/填充词/明喻密度）。
  - **二期**（8afa012，用户拍板全做 + S3.4 代码默认关）：S1.4 伏笔 stalled 状态（埋后超 8 章无推进注入"冷却"提醒防弃笔，默认开）+ 语义双路（LLM 判推进，默认关）；S2.3 固定锚双轨（开头原文+个人旧稿作风格原点，默认关）；S2.5 L3 风格裁判（每 5 章 LLM 六维比对超阈值触发再锚定，默认开）；S3.6 远期大纲粗粒度（beat_gate 远期节点不计 fail 分母，默认关）；S3.4 fail 章 LLM 二级复核（anti-length judge 确认未兑现才进 L3，默认关——启用待人工标注集验证>80%）。
  - **默认关项**（线上行为与关闭前一致）：双锚体系（题材/个人旧稿/固定锚）+ `foreshadow_semantic_check` + `outline_rolling_detail` + `beat_fail_judge`，需要时开开关即可。
  - 回归：全量 **793 pytest 全绿**（新增 reader_ledger_stalled / fixed_style_anchor / style_judge_l3 / outline_far_beats / beat_fail_judge 等 27 用例）；真实生成验收（wanxu_rerun_8000_20261018_124715，5 章）无回归。部署：git archive master 打包 → workbench upload → 解压 /opt/novel-gen → docker compose up -d --build；线上验证：容器 healthy + 主页不白屏。

- **2026-10-17**：第二十二次发版（服务器代码 = master **e76bafb**，**已上线**）——**章节重写版本落库 + 版本质量展示（feat/chapter-version-quality）**。修复"自校验自动重写/手动重写后版本不落库、版本弹窗看不到选不了"根因（方案4 落盘分流：质检未通过且旧版健康时重写稿被幂等跳过丢弃）。核心：① `save_chapter` 扩展 `quality`（质检结果写入版本 history 透传展示）与 `promote=False`（候选版本只存档不顶 HEAD），每次生成的版本（含自动重写稿）都进版本库；② 未被用户管理时自动把质检评分最高版本设为当前选中（首次生成默认展示最好一版），用户手动编辑/切换后不再自动覆盖；③ `delete_version`（selected 不可删）+ `DELETE /chapters/{no}/versions/{v}` 接口；④ `ensure_quality` 输出 versions 版本日志（初始稿+每轮重写稿，各带质检问题）；⑤ 前端版本弹窗整合质检分/改稿建议明细/删除按钮，工具 tab「改稿建议」卡片改为「全书质检」。回归：全量 739 pytest 全绿（P0+P1 459）、npm build 通过 dist 引用一致、端到端冒烟（DELETE/select/自动选分）通过。部署：本地 `git archive master` 打包 → workbench upload → 解压 /opt/novel-gen → `docker compose up -d --build`（首次因 daocloud 镜像源 502 失败，重试成功）；线上验证：容器 healthy、主 JS 200/1.1MB 防白屏、openapi 97 路由（新增 DELETE versions 接口）、版本接口鉴权正常、容器内 dist 引用一致。

- **2026-10-15**：第二十次合并（devtest → master，8783cca，**未部署**）——**正文去机械感优化（REQ-写作正向引导 一/二/三期）**：把正文生成从"禁令驱动"改为"正向引导驱动"（用户诊断：样章"不像人写的、不够直白、读着费脑子"，禁令叠罗汉矫枉过正）。
  - **一期（feat/positive-guidance-writing）**：场景规则 23→10 条（禁令改正向引导）；机制数字注入"权威当前值·只抄不推"（panel_baseline）；AI 腔词表从"禁止"改"警示 + 密度意识"（明喻用在刀刃上、零直白检测）；writer.md 写入"演七分说三分""白描是最高级写法"。验收：全书 11 章重生成均分 95.5。
  - **二期（feat/volume-cards）**：卷→章→场景三级目标传导（`Outline.volume_cards` 卷卡：goal/obstacle/climax/legacy）+ 场景字数预算收敛（1200 字/场景基准）；机制权威基线三路径补盲（场景 / `_fallback_whole_chapter` 整章回退 / `_rewrite_*` 重写均注入"承接链只抄不推"）；账本双源反推（deaths = max(蚀化差, decrease 字段)）+ 首章初始值回落；寸字段提取收紧为累计格式。验收：全书重生成均分 79.1-85.9（vs master 63.2）。**决策：章节字数超长不触发整章级 LLM 处理（超长≠写得差，浪费 token）**。
  - **三期（feat/good-memory）**：踩坑 + 正向样例双账本（REQ 5.6）——`BookMemory` 新增 goods 账本：高分章（质检 ≥85 且无大改）段落自动入账（`record_good`）+ 用户编辑保存免质检入账（`record_good_manual`，webapp 触发），按手法确定性分类（对话-潜台词/战斗-动作链/情绪-白描+点破/环境-物象白描/叙事-节奏），每类 ≤5 条、总量 ≤50、每条 ≤300 字；场景生成（stages memory_context）与重写（quality `_memory_block`）双路径注入 `good_block`（标注"参考质感非内容来源"防抄袭）；`record_taste` 联动形成"写得好→被学习→写得更好"闭环。验收：全书重生成均分 **81.4、通过 9/11**（5 章满分；ch1 从 45→100），vs 二期 79.1/7、master 63.2/6 提升确定。
  - 回归：全量 **707 pytest 全绿**（P0/P1 433 全绿；新增 volume_cards/mechanism_registry/good 账本用例）。部署：已合并 master（8783cca），内容随第二十一次发版（2026-10-16，da27b4c）一并上线。
- **2026-10-16**：第二十一次合并（devtest → master，含 7137636 + afa9529，**未部署**）——**章节提交 + 章节版本化两期**：
  - **一期·章节原子提交 + 投影一致性（feat/chapter-commit，借鉴 webnovel「写与记分离」）**：新增 `pipeline/commit.py` `ChapterCommitter`，把 manager 章末 4 个分散入账（人物状态/章末机制/揭示执行/读者账本）收敛为一次统一提交——先落基线、失败留痕 incomplete、同章幂等 skip、每次写 `projection_log.jsonl`；`check_projection` 扫描 10 个派生视图定位"哪个账本落后于当前章号"，命令行 `tools/projection_check.py <项目目录>`（lagging 退出码 2）。开关 `chapter_commit`（默认 True）。
  - **二期·章节正文版本化（feat/chapter-versions，git 式多版本保留与用户选择）**：`save_chapter` 从覆盖改为追加版本——`chapters/<no>.json` 仍为当前选中（HEAD），历史版本存档 `chapters/<no>/v<k>.json` + `chapter_versions.json` 索引；内容相同幂等、不同则追加新版并更新选中；每章上限 15 淘汰最旧（HEAD 不淘汰）；`regenerate_from` 改方案 a（不再删章节，改为追加候选版本、用户逐章自选）；`edit_chapter` 追加 user 版本；新增 `GET /chapters/{no}/versions` + `POST /chapters/{no}/versions/{v}/select`；前端阅读器加版本列表+选择/回退 UI。
  - 回归：全量 **730 pytest 全绿**（新增 chapter_commit 14 + chapter_versions 8 用例）+ 前端 build 通过。部署：已合并 master（da27b4c），第二十一次发版已上线（2026-10-16）。
- **2026-10-16**：第二十一次部署（服务器代码 = master **da27b4c**，**已上线**）——**第二十次合并（正文去机械感 一/二/三期）+ 第二十一次合并（章节提交 + 章节版本化）一次部署覆盖全部内容**：
  - 方式：手工打包（Windows 工具，zip 内文件 CRLF 行尾，与 git archive 的 LF 差异不影响运行）→ workbench upload `/tmp/novel_master.zip`（16:29）→ `python3 -m zipfile -e` 解压 /opt/novel-gen → `docker compose up -d --build`；容器 novel-gen 17:05 重建 healthy、mysql-mozao healthy。
  - 验证（2026-10-16）：关键文件 7/7（book_memory/stages/templates/writer.md/projects_chapters/schemas/config）CRLF 归一化为 LF 后 git blob 与本地 master da27b4c **完全一致**；zip→解压 cmp 无损；功能特征宿主机 + 容器内双确认（good 账本 / 卷卡 / 机制"只抄不推" / 白描措辞 / record_good_manual 全命中）。非标准流程（未走 git archive），产物字节等价已验证，无需重发。
- **2026-10-14**：第十九次发版（master 411a466）——**创作设计层 + 读者体验层 + 产品化门禁工具三连发**（devtest 三功能合并）。
  - **一期·章级设计卡接线（chapter_design）**：新增 `pipeline/chapter_design.py`（纯 Python 前置、零 LLM），从章级 conflict/emotion + `World.conflict_matrix` 派生逐章 A1–A7 设计卡（关系模板/对白功能/戏份级别等），落 `chapter_design.json`，注入 `stages.write` 的 memory_context（一处拼接覆盖场景/整章/回退三条写章路径）；并修复 `Outline.key_nodes/volume_pacing/emotion_curve` 因大纲复用短路从不填充的数据源缺口（`_backfill_pacing` 幂等补三字段）。开关 `chapter_design`（默认 True）。
  - **二期·读者认知账本（reader_ledger）**：新增 `pipeline/reader_ledger.py`（零 LLM 确定性）三块独立账本——`reader_knowledge.json`（读者已知/在等/被误导三态）、`foreshadow_commitment.json`（伏笔 planted→临期/超时/回收状态机）、`payoff_quota.json`（爽文「3 章一小爽」节奏配额）；写章时注入读者视角约束，回收检测确定性（正文命中伏笔名/内容才标记回收，防假回收）。开关 `reader_ledger`（默认 True）。
  - **三期·Beat 覆盖度门禁 + 场景体检 + 三分支续写 + 思维链可视化（产品化）**：①`beat_gate.py` 正文对大纲 `character_moves` 逐拍的字符 bigram 覆盖率自检（分级 pass/warn/fail/skip，落 `beat_gate.json`，检测+提示不独立重生成）；②`scene_inspector.py` 场景体检（字数/事件推进/情节重演/章首承接/章末钩子 5 维度 + 确定性修复建议，对标 Sudowrite Scene Inspector）；③`continue-branches` 一次 LLM 出 3 方向（冲突升级/埋伏笔/转折）续写候选，`apply-branch` 默认预览模式只记草稿不改正文；④`chain-of-thought` 只读聚合「计划-产出-校验」生成过程链。前端 ToolsPanel 新增场景体检/三分支续写/生成过程三张卡片。
  - 回归：全量 **680 pytest 全绿**（一期 16 + 二期 17 + 三期 14 用例）。
- **2026-10-14**：第十八次发版（master e6fa991）——**题材锚默认关闭（决策落地）**：修复状态差——`novel_gen/config.py` FEATURE_FLAGS `teardown_style_anchor` 默认值 True→False（2026-10-14 朱雀归因实测题材锚为独立负面变量：同新粒度下无锚 18.25% vs 有锚 55.01% AI 特征，决策默认关闭但代码未同步）+ `webapp/core.py` getattr 兜底同改；新增守护测试 `test_teardown_style_anchor_default_off`，4 个依赖旧默认值的注入测试改显式开启保留行为覆盖。效果：新书/新项目未显式配置时题材锚默认不注入；显式打开仍可用。回归：全量 633 pytest 全绿。
- **2026-10-14**：第十七次发版（master 6e0c3ef）——**场景粒度优化（REQ-scene-plan-align）**：场景数由章节字数反推（`_scene_count_for_words`：1200 字/场景为基准，N=clamp(int(words/1200+0.5),1,4)）+ 单场景 target_words 下限（max(800, 章字数×25%)，低于下限=拆得太碎回退整章）。根因：2500 字/章被按大纲拍数拆成 4 场景×625 字，模型靠"死亡-复活-面板循环注水"凑字数（行业标准单场景 800-2500 字，一章 1-3 场景）。效果：ch1 改 2 场景×~1250 字后循环注水消失（面板 1 次/死亡-复活各 1 次），编辑审稿 score=7.5 通过。朱雀 AI 检测归因闭环：AI 特征 76.86%（旧基线）→ 18.25%（2场景粒度无锚），题材锚判定为独立负面变量（同为新粒度下无锚 18.25% vs 有锚 55.01%，且锚诱发重复循环、三次生成触发防重演拦截），**决策默认关闭题材锚（teardown_style_anchor=False）**。回归：全量 632 pytest 全绿（新增 tests/test_scene_granularity.py 5 例）。
- **2026-10-10**：第十五次发版（master 818faec）——**场景文本防重演闭环（P0-1~18 生成端修复包 + v10/v11 实证修复 + v12 真实重生成验收）**。核心：① P0-15/16 机制字段注册表（closed-world，从机制基线派生字段名+方向，`unknown_mechanism_fields` 白名单兜底）+ 链式账本（`book_memory.record_chapter_state` 跨章继承）+ 事件驱动双向校验 `ledger_check`；② P0-17 大纲核心事件覆盖（`_validate_scene_plan` 分段 LCS≥2 字/命中≥60%，未覆盖回退整章）+ 质检 2.8d `_core_event_missing`；③ P0-18 超长注水（`_slim_scene` 精简 1 次，仍超保留不整章回退）；④ v10 根因修复（`33435ed`）——回退整章路径质检基线为空致 2.8c 整段跳过、幻觉行漏检：去 baseline 依赖、死亡口径统一蚀化差、回退/普通整章注入 `_panel_whitelist_block`；⑤ v11 实证修复（`9388626`）——账本 erosion 断链继承（永不破链）、质检链式取最近非空、`_pcv` 基线 0 跳过、`_chapter_healthy()` 保留旧版前健康检查（带病旧稿弃用落盘新稿）；⑥ 死亡判定通用+个性化两套独立标准（P0-14）。验收：《万墟之上》前 7 章 v12 真实重生成——「累计死亡次数」幻觉行彻底消失、跨章面板链 2→2→3(重置)→1→1→1→0 / 蚀化 97→100 只增 / 寸基线内波动、无重复段落、七章通读 ★★★★★。回归：612 pytest 全绿。
- **2026-10-01**：第十四次发版（master 3eab3b7）——**章节质量防线·场景事件级去重**。第 1 章重生成五轮实测根因链：6649 字版（拆场景 JSON 长输出结尾断裂→事前防线回退失效，3 遍救人）→ 4874 字版（事件清单互斥拦不住场景正文换措辞重演）→ 3395 字版（敌人措辞'三只灰卒'vs'三只赤尉伴生灰卒'致清单互斥+正文检测双漏检）。修复：① 拆场景 JSON 健壮化（`_strip_json_block` 回溯闭合+尾逗号修复；`_extract_json_field` 字段级救回 events；失败重试 1 次仍败才回退整章，回退注入 `chapter_events` 事件序列+final_state+每事件只写一遍纪律）；② 场景正文事件级复写检测 `_scene_text_rewrites_event`（事件槽位冲突对象+救援专属动词 救/引开/砸/挡/扑/迎，命中重试场景并注入 `forbid_event` 禁止重演）；③ 事件槽位改被救/受害人物优先（同一人物+同一动作即判重复）；④ 拆场景成功日志。回归：406 pytest 全绿。
- **2026-09-30**：第十三次发版（master 5388d57）——**揭示执行跟踪（REQ-揭示执行跟踪）**：世界观揭示计划的兑现校验闭环。① `WorldReveal` 新增 `keywords` 真相锚定词；② 每章正文落盘后 `chapter_state.detect_reveal_hits` 确定性命中检测（零 LLM），累积到 `reveal_status.json`；③ 全书完成 `validator.check_reveal_execution` 校验——提前剧透（真相锚定词在 from_ch 前出现）→ fatal，到点未揭示（from_ch 已过正文从未提及）→ major，旧数据无 keywords 不误报；④ 新开关 `reveal_tracking`（默认 True，失败降级不阻塞生成/结算）；⑤ 提示词（world_rules.md / world_prompt ×2）要求产出 keywords（不得与 hint 重叠防铺垫误判）。回归：182 pytest 全绿。
- **2026-09-30**：第十二次发版（master 4659996）——**设定静态/动态/揭示三层约束落地提示词**：`architect.md` / `world_rules.md` 新增「设定分层约束（硬规则）」——S 静态（宪法，全书不变）、D 动态（账本，随章更新）、R 揭示（禁剧透：world_reveals 含 from_ch/foreshadow_ch/hint）；人物档案三层为静态、当前状态为动态禁止入档；core_event/transition 为已发生事实权威。回归：31 passed。
- **2026-09-30**：第十一次发版（master 93edd41）——**生成 Token 消耗优化 P0+P1 全量（REQ-token优化.md）**。根因：质量机制全依赖 LLM 调用，普通模式单章 LLM 调用从 ≈2 次膨胀到 ≈13~16 次（约 5 倍），输入侧占 73%。本次按「前置解决优先」：① 分章设定卡索引（`setting_cards.py` 纯 Python 确定性脚本）；② 场景状态抽取普通模式改纯确定性面板正则（高质量模式保留 LLM）；③ 章末/人物状态 LLM 抽取降频（每 3 章 1 次）；④ 行动表并入拆场景输出；⑤ 整章润色仅高质量模式；⑥ 章内矛盾 LLM 自检仅高质量模式；⑦ 一致性/读者/编辑三连校验合并为 1 次调用（每 5 章）；⑧ 编辑修正改 diff 式（max_tokens 4096→1200）；⑨ 定点重写（只重写问题段落窗口 ±200 字，省 60~75% 重写输出）；⑩ 正文直读 2000→1200 字。开关全部走 FEATURE_FLAGS 注册表，存量项目 config.json 旧值不受影响。回归：全量 357 pytest 全绿 + HTTP 端到端冒烟 ALL PASS。
- **2026-09-28**：第十次发版（master 9b0934a）——**修复章节重写/重生成接口 500**：`rewrite` 与 `regenerate-from` 稳定 500 且无日志——根因是路由拆分时 `REGEN_DAILY_LIMIT = 10` 常量定义丢失、仅保留使用处，`_check_regen_limit` 每次抛 `NameError`（位于 try 块之外、不写日志），`regen_count.json` 永不落盘。已在 `webapp/core.py` 恢复常量定义；新增 5 个 P1 回归用例。回归：210 pytest 全绿。
- **2026-09-28**：第九次发版（master 5f532c5）——**修复退款/质量报表接口 500**：参数化 SQL 中 LIKE 字面量 `%` 未转义，pymysql 误当作参数占位符抛 `TypeError`；已转义为 `%%`（共 3 处）。新增 `tests/test_refund.py`（P0）+ `test_quality_report.py` 2 个 P2 回归用例。回归：190 pytest 全绿。
- **2026-09-28**：第八次发版（master 9a7d417）——**生成质量升级 P0/P1/P2 全量 + 用户工作台 + 导航收敛**。P0：前文直读 / 人物动态状态 / 全书质量汇总；P1：行动表先行 / 场景级下沉 / Lorebook 裁剪 / AI 痕迹评分；P2：动态模块随机化 / 风格范文锚定 / 全书终审 / 质量指标报表（站长后台质量报表 tab）。用户工作台 /workspace（6 tab）；导航收敛：工具面板移除技能/市场/风格角色（统一工作台）。回归：186 pytest 全绿、npm build 通过 dist hash 一致。
- **2026-09-24**：第七次发版（master 75725e3）——**爆款拆书库三件套**：① 基础沉淀（`book_teardown` 表 + 调研成果自动入库 + 内置知识库种子 + 4 个拆书库接口 + 前端拆书库页）；② expand 模式可选「附带题材调研」；③ 手动拆解爆款作品入库（AI 拆解 12 项写法清单）。回归：全量 80 pytest 全绿、真实 DeepSeek 拆解冒烟通过。
- **2026-09-23**：第六次发版（master e975b5d）——恢复「选中段落扩写/续写」前端入口（选中 ≥5 字弹出浮动条），后端接口一直在。构建验证：npm run build 通过、dist 引用一致。
- **2026-09-23**：第五次发版（master e5b19f7）——**用户自有内容导入**：上传大纲 .txt/.md/.docx（≤10MB）与粘贴文本（≤10 万字）；创建项目默认原样采用用户大纲不自动重写；项目详情页「重新导入大纲」+ 快照还原；大纲快照体验收敛。
- **2026-09-21**：第四次发版（master 08e8078）——**生成提示词口头禅频次约束**（修复《我的地球Online日记》45 万字中"先记下来"出现 150 次问题）；dist 移出仓库（Docker 多阶段构建容器内 build）。
- **2026-09-21**：第三次发版（master e89d563）——**数据与代码隔离**（DATA_DIR 环境变量，数据迁移至 `D:\NovelData`）+ 前端导出按钮（TXT/EPUB/DOCX/MD）+ 新建目标字数下限修复 + 前端产物重建防白屏。回归：60 pytest 全绿。
- **2026-09-20**：部署加固（deploy/compose-projects-vol）——compose 补挂 `novel_projects` 命名卷；**同日完成首次服务器部署**（mysql-mozao + novel-gen，域名 https://mozao.click），注册→登录→建书→详情→登出全链路冒烟通过。
- **2026-09-21**（feat/style-role-agent）：⑫ 写作风格抽象为 agent 角色（StylistAgent 风格师）——新增 `style_roles` / `style_role_skills` 两张表与迁移，内置 4 个默认风格角色；写手与扩写路径注入风格人设；前端建书可选风格角色。
- **2026-09-20**：⑪ 项目域再拆分——`routers/projects.py`（1150 行/43 路由）拆为 5 个功能子模块（projects_base/outline/chapters/world/generation）。回归：路由集合 96=96 一致、29 pytest 绿。
- **2026-09-20**：⑩ 最大架构债——原 2853 行单文件 `webapp/server.py` 拆分为 `webapp/core.py` + `webapp/routers/`（9 域 96 路由）+ 组装壳。回归：openapi 路由集合 96=96 一致、全量 29 pytest 绿。
- **2026-09-20**：⑥⑦⑨ 结算重构（统一 `_compute_settle` 口径，**先落盘结算基线再扣费**，消除重复扣费窗口）、生成任务持久化（task.json + lifespan 自动重拾中断任务）、测试恢复入库（29 个全绿）。
- **2026-09-20**：①②③④ 第一批优化——BYOK Key 加密落库（Fernet，存量明文启动自动迁移）；项目列表性能（meta.json 轻量缓存）；文档对齐（README/WIKI 与实际代码一致）；清理部署产物与已淘汰的 webui 目录。附带 P0 修复：`get_user_llm` 键名与调用方契约不符导致 BYOK 自配 key 从未生效。
- **2026-09-17**：修复**扩写模式**重新生成大纲后分卷为空——扩写提示词补 `core_conflict / main_line / volumes`；`regen_outline` 改为删 outline.json 后再起后台线程；前端「重生成大纲/世界观」按钮改走带 token 的封装请求。
