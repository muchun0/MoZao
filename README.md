# AI 自动生成小说（novel_gen）

输入题材/创意，自动产出完整小说。生成前先**调研热门小说共性**（情节/文风/节奏），再按「调研 → 大纲 → 编辑质检 → 人物世界观 → 逐章正文 → 导出」流水线推进，全程可断点续传。

Web 端为**多用户 SaaS**：注册登录、按 AI 实际生成字数计费（普通每万字 2 元，高质量模式 3 元）、代理分销、退款申请、站长管理后台，适合直接作为虚拟产品对外售卖。

## 核心特性

- **全自动整本成书**：调研 → 大纲 → 编辑质检 → 人物/世界观 → 逐章正文 → 导出，一键跑完全流程
- **双模式**：`auto`（全自动工作流）/ `expand`（AI 扩写，用户提供大纲或简要内容，AI 扩写为完整小说）
- **质量增强四件套**：编辑视角质检（不通过自动修正）、去 AI 味写作约束、防同质化常驻提示词（拒绝套路、人物带内在矛盾、剧情有代价）、人物立体化（角色卡一致性 + 选择代价 + 生活体感 + 对话辨识度）
- **写作风格 Skill**：站长可在管理后台配置多套写作模板（番茄爽文 / 晋江细腻 / 西幻史诗 / 悬疑冷峻 / 轻小说日常等），用户创建小说时可选，prompt 自动注入 System Prompt
- **风格角色（StylistAgent）**：写作风格抽象为独立 agent 角色——平台内置（爱潜水的乌贼·克苏鲁悬疑克制风 / 烽火戏诸侯·新古典江湖风 / 番茄爆款·短句快节奏风 / 辰东·洪荒史诗苍凉风，来自 skills_library 蒸馏）+ 用户自建；每个风格角色含人设（role_prompt）并**可挂载多个技能**（平台写作技能 + 我的 Skills），建书时一键套用，人设与技能由风格师注入写手正文与扩写路径
- **强制结局**：无论长短篇，最后一章必须是完整结局——完成人物弧光、收束核心冲突、给出情绪落点，并按结局写作规范落地（四类合格结局形态 / 开篇锚定对照 / 烂尾黑名单 / 尾声写法），结尾升维（技术→哲学 / 产品→人性 / 事件→时代）
- **多格式导出**：TXT / EPUB / DOCX / Markdown，项目详情页「导出 ▾」一键下载，另附 **ERNIE-Image-Turbo AI 生成的小说封面（PNG，每本限 5 次）**
- **单章人工编辑 + 继续生成**：可修改任意章节正文，后续章节基于修改继续生成；也可从某章重新生成
- **上传大纲智能解析**：上传 .txt / .md / .docx 大纲文档（≤10MB）或粘贴文本（≤10 万字），AI 自动解析出书名、题材、字数、主角、配角、背景、剧情走向等字段并填入新建表单；**创建项目默认原样采用用户大纲，不自动重写**（扩写模式下亦然），世界观需用户手动触发「重生成世界观」后才由 AI 派生；已建项目可**重导入大纲**，旧大纲自动备份为快照（可一键还原）
- **长文一致性**：设定库（世界观/人物卡/伏笔）+ 最近 N 章摘要回填，杜绝前后矛盾
- **断点续传**：以产物文件为唯一权威来源，中断后只补缺失部分，不重复生成、不浪费 token

## 多智能体角色

生成流水线已拆分为六个独立角色（`novel_gen/pipeline/agents/`），各有专属提示词（`prompts/*.md`）与按角色挂载的 skill：Researcher（调研）→ Architect（三级大纲/人物世界观/时间线）→ Writer（逐章正文，叠加 Stylist 风格人设）→ Reviewer（一致性/逻辑/合规审校）→ Reader（读者视角），另有 **Stylist（风格师）**：承载项目选定的写作风格角色（人设+挂载技能），注入写手正文与扩写路径。

## 架构

```
config.yaml ──► 配置层(config.py)         多 Provider / 题材 / 字数
                    │
                    ▼
┌────────────── 生成流水线(pipeline/manager.py) ──────────────┐
│  ①调研(research/) ──► ②大纲 ──► ③编辑质检 ──► ④人物/世界观 ──► ⑤逐章正文 ──► ⑥导出 │
│    内置知识库+可选联网      │            │            │              │
│    → LLM提炼共性报告 ───────┴──────► 注入所有下游阶段 ◄──────────────┘
│                        （记忆系统 memory.py：设定库+最近N章摘要回填）    │
└──────────────────────────────────────────────────────────────┘
                    │
                    ▼
         projects/<项目目录>/   ← JSON 产物 + 状态机，断点续传
                    │
                    ▼
         output/   ← 最终成品：txt / epub / docx / 封面 png
```

**核心模块**

| 模块 | 职责 |
|---|---|
| `config.py` | 配置加载（YAML + 环境变量补 Key）、题材/字数/Provider 校验 |
| `llm/` | Provider 抽象层：DeepSeek / 豆包火山方舟 / OpenAI / mock（无 Key 测试） |
| `research/` | 调研阶段：内置知识库 + 可选联网搜索（博查/Serper）→ LLM 提炼共性报告 |
| `pipeline/` | 生成流水线：大纲/人物/逐章生成器 + 记忆系统 + 流程编排 + 编辑质检 |
| `store/` | 项目落盘、状态机、断点续传、txt 导出 |
| `export/` | 封面生成（Pillow）、EPUB/DOCX 多格式导出 |
| `webapp/` | 多用户 SaaS Web 服务（FastAPI + Vue 3 前端 + MySQL） |

## Web 界面（推荐，无需命令行）

前端已按"墨造 · AI 小说创作平台"设计方案整体重做——**深夜书房 × AI 工坊**视觉语言（深靛蓝主色 + 暖金强调 + 暖白纸面、衬线标题、墨水晕染进度条），前后端分离：

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

项目提供 `Dockerfile` + `docker-compose.yml`，一条命令部署到任意 Linux 云服务器：

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

- **前端多阶段构建**：`docker build` 时第一阶段用 `node:20-alpine` 在容器内自动 `npm ci && npm run build`，第二阶段把 `dist` 拷进 Python 镜像。**无需在宿主机提前 build 前端、也无需单独上传 dist**。
- **镜像**：基于 `python:3.13-slim`，已内置中文字体（Noto CJK，封面绘制必须）、时区（Asia/Shanghai）、健康检查
- **数据持久化**：四个命名卷——`projects`（中间产物）/ `output`（成品）/ `logs`（日志）/ `data`（数据库）。容器重建不丢数据；数据库通过环境变量 `NOVEL_DB_PATH=/data/novel_web.db` 落到卷内
- **数据与代码隔离**：`projects/output/logs` 的根目录由 `DATA_DIR` 环境变量指定（默认代码根，见下方 .env 配置）；服务器不设时容器内回退 `/app`，与既有卷挂载点一致，零影响
- **密钥安全**：`.env` 被 `.dockerignore` 排除、**不写入镜像**，通过 compose 的 `env_file` 从宿主机注入；`run_web.py` 遵循"环境变量优先"（`.env` 里的值不会覆盖已存在的环境变量）
- **访问**：默认映射宿主 `8000` 端口（`ports` 左侧可改，如 `8001:8000`）；首次注册用户自动成为站长
- **静态文件 / 封面 / 大纲上传** 均为纯 Python 实现，无外部系统服务依赖

> 注意：构建需服务器可访问 Docker Hub / PyPI（Dockerfile 已用清华 PyPI 源加速）。国内云服务器拉取 `python:3.13-slim` 基础镜像慢时，可给 Docker 配置镜像加速器。

### 功能

- **注册 / 登录**：token 鉴权，数据存 **MySQL**（库 `mozao`）；**注册必填邮箱**（全站唯一、格式校验），**邮箱验证码验证**（6 位数字、10 分钟有效、60 秒冷却、错 5 次作废）；登录支持**用户名或邮箱**；**忘记密码**走邮箱验证码重置，重置后该账号全部会话强制失效；登录页为"深夜书房"氛围，注册即送体验金
- **我的项目（书案）**：卡片式作品列表，支持**关键词搜索**（书名/题材/进度）与**状态筛选**（全部/未开始/生成中/已暂停/已完成/出错），未开始项目可直接"退费删除"
- **新建小说（开卷）**：表单填写书名、题材、字数、背景、主角、配角、剧情走向、避雷等；支持 `auto`（全自动）和 `expand`（AI 扩写，需提供大纲/简要内容）两种模式；可**上传大纲文档（.txt/.md/.docx）由 AI 智能解析后自动填入表单**；右侧**费用明细悬浮栏**实时显示目标总字数 / 已有手写内容 / AI 需生成字数 / 本次预估费用（按目标 AI 字数封顶计费，创建时不扣费）
- **项目详情**：**六阶段墨迹进度条**（调研 → 大纲 → 人物 → 正文 → 质检 → 导出）自动映射生成状态，开始/停止生成、下载 txt/epub/docx、查看封面
- **退费删除**：创建后尚未开始生成正文（零章节产出）的项目，可一键全额退费并删除作品；退费同步冲正上级代理返佣，防止刷量
- **章节阅读**：沉浸式阅读器——**纸白/护眼/暗夜三主题、字号调节、章节目录下拉、上一章/下一章与键盘 ← → 翻页**
- **单章编辑**：修改任意章节标题/正文，保存后后续章节基于修改继续生成；支持"从此章重新生成"
- **三级大纲编辑器**：总纲 → 分卷 → 章节，章节可跨分卷拖拽、分卷增删、章节菜单按分卷分组；生成前可编辑大纲与人物世界观
- **充值**：弹层展示「充值请联系站长」+ 微信收款二维码 + 计费说明，扫码转账 → 站长人工确认到账（**创建不扣费，生成完成后按 AI 实际字数结算**）
- **代理中心**：查看邀请码、推广链接、下级列表、返佣明细
- **退款申请**：充值余额可申请退款，站长邮件通知 + 后台人工处理

### 多用户（SaaS）模式

本 Web 端是多用户版，支持注册/登录、余额计费、项目隔离：

- **首个注册用户自动成为管理员（站长）**，拥有全部历史项目与所有用户项目可见权
- **注册必填邮箱 + 验证码**：全站唯一（不区分大小写，统一存小写），注册前先点"获取验证码"（邮件发到用户自己邮箱）；登录输入框同时支持用户名和邮箱；历史老用户无邮箱不受影响，可在后台用户表中看到邮箱列
- **忘记密码**：登录页"忘记密码？"→ 输入注册邮箱收验证码 → 输码 + 新密码重置；重置后该账号所有登录会话失效，必须重新登录（未注册邮箱不发送、不泄露注册状态）
- **邮件发送**：验证码发给用户本人邮箱，退款申请通知仍发站长（`ADMIN_EMAIL`）；未配置 SMTP 时验证码接口返回"发送失败"（注册/重置不可用），退款申请仍可提交
- **API Key 只放服务端**（`.env` 文件或系统环境变量），用户不需要也不可见 Key；用户自配的 BYOK Key **加密落库**（Fernet，密文前缀 `enc:v1:`），DB 泄露不暴露明文
- **按目标 AI 字数封顶收费**：创建项目不扣费，生成完成后按用户创建时填的目标 AI 字数结算（用户手写内容不计费），每万字 2 元；AI 多写的部分平台承担，不另收费
- **多用户并发**：不同用户生成互不阻塞，后台多线程执行
- **未生成可退**：项目零章节产出时可全额退费并删除（详情见"退费删除"）；已产出章节则不可退
- **代理折扣**：代理本人建书享折扣（默认 8 折）
- **消费返佣**：下级每笔消费，上级代理按比例返佣（默认 10%，自动入余额）

**API Key 配置**（在项目根目录创建 `.env`）：
```
DEEPSEEK_API_KEY=sk-你的Key
BOCHA_API_KEY=sk-你的Key      # 可选，联网调研用
DATA_DIR=D:\NovelData         # 可选：数据目录根（projects/output/logs 的父级），默认代码根
```

- **邮件通知**（退款申请通知站长 + 注册/重置验证码发用户邮箱）：
```
ADMIN_EMAIL=站长收件邮箱
SMTP_HOST=smtp.qq.com         # QQ 邮箱用 smtp.qq.com，163 用 smtp.163.com
SMTP_PORT=465
SMTP_USER=发件邮箱
SMTP_PASS=发件邮箱SMTP授权码    # 不是登录密码，是邮箱设置里的 SMTP 授权码
```

**定价**（`webapp/pricing.py` 可改）：

- **统一单价：普通每万字 2 元，高质量模式 3 元**（不再分长短篇档位）
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
- 所有商业参数集中在 `webapp/referral.py`：`AGENT_DISCOUNT`（代理折扣）、`COMMISSION_RATE`（返佣比例）、`COMMISSION_BASE`（按消费/按充值返佣）、`REFERRAL_BONUS`（拉新奖励）

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
- 站长在管理后台 → 退款管理 中处理：**确认已转账并退款**（线下转账后点，系统自动从用户余额扣除并记流水）或 **拒绝退款**（填备注）
- 防重复申请：有未处理申请时禁止再次提交

### 站长管理后台

独立页面 `http://127.0.0.1:8000/admin.html`，**仅站长（is_admin）可登录**，普通用户返回 403。站长登录前台后顶栏也会出现"管理后台"入口。

包含 8 个板块（与前台统一"墨造"设计语言）：

| 板块 | 功能 |
|---|---|
| 📊 经营概览 | 用户数、代理数、项目数、充值总额、消费总额、累计返佣、注册赠金；**资金流向对比图**（充值 vs 消费 vs 返佣柱状图） |
| 👥 用户管理 | 用户列表（余额/身份/折扣/邀请码/下级数/返佣），行内充值、设/取消代理、查下级 |
| 🔗 代理管理 | 代理列表，查看各代理返佣明细 |
| 📚 项目总览 | 所有用户的项目（标题/归属/题材/章节进度/状态） |
| 💰 退款管理 | 退款申请列表（全部/待处理/已退款/已拒绝），人工处理 |
| 💳 余额流水 | 全站充值与消费流水 |
| 📜 系统日志 | 实时查看服务日志尾部（黑底终端样式），出问题直接定位 |
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
- 项目创建（标题/模式/价格/字数）、生成线程启停、每阶段（调研/大纲/质检/人物）、每章开始与完成（含字数）、章节失败（带异常堆栈）
- 资金操作：充值、退款申请/处理、消费/充值返佣
- 代理操作：设为代理/取消代理
- 导出下载、邮件发送成功/失败

日志中**不记录**密码、token、API Key 等凭据。站长可在管理后台"系统日志"板块直接查看。

## 安装

```bash
pip install -r requirements.txt
```

依赖：FastAPI / Uvicorn（Web 服务）、openai（LLM SDK，含 httpx<0.28 版本锁定，否则 httpx 0.28 会导致 LLM 客户端创建失败）、PyYAML（配置）、Pillow（封面）、ebooklib（EPUB）、python-docx（DOCX，另用于上传大纲解析）。

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
| `total_words` | 单本目标总字数（由用户决定，按档位收费） | 100000 |
| `chapter_words` | 单章目标字数（章节数自动换算） | 2500 |
| `style` / `premise` | 文风要求 / 核心创意（可留空） | 空 |
| `background` | 故事背景/世界观（**强约束**，必须遵循） | 空 |
| `hero` | 主角设定：姓名/身份/性格/目标（**强约束**） | 空 |
| `characters` | 配角/反派预设列表（**强约束**，可留空让 AI 补） | 空 |
| `plot_direction` | 剧情走向/关键情节（**参考方向**，AI 可细化） | 空 |
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
| `search.*` | 调研阶段联网搜索（可选，见下） | 关闭 |

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

**编辑质检**（`editor_review`）：大纲生成后，模拟资深网文编辑从 9 个维度审稿——题材标签、一句话卖点、开篇钩子、主角目标/困境、情绪回报节奏、人物关系、前三章闭环、连载风险、防同质化。评分低于 7 分或有致命问题时，自动基于编辑建议修正大纲。审阅结果保存在项目目录 `editor_review.json`。

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

- **人物阶段（角色卡要求）**：每个重要人物的角色卡强制写全 6 要素——身份标签、经历背景、性格核心（拒绝"善良/邪恶"式空词）、欲望与软肋、行为范式（小动作/口头禅）、压力反应（胃疼/失眠/走神）——保证人物有辨识度、行为有依据。
- **正文阶段（写作技法）**：① 人设一致性——行为必须能从经历/性格/欲望/处境解释得通，禁止为剧情强改性格；② 选择加成本——重要胜利必须付真实代价（牺牲/风险/两难取舍），禁止白捡的赢；③ 生活体感——用具体身体反应承载情绪（罚跪后膝盖发麻、姜汤喝急被呛、熬夜眼红），禁止"他悲痛欲绝"式直白宣告；④ 出场标签 + 对话辨识度——新角色登场带「他是谁/跟谁有关/为何此刻重要」，不同身份性格说话方式必须不同（刺头带冲劲、上位者有威压、谨小慎微者留余地）。

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
├── config.py            # 配置层
├── llm/                 # Provider 抽象（base/factory/providers）
├── research/            # 调研阶段（schemas/sources/research + 内置知识库）
├── pipeline/            # 生成流水线（templates/stages/memory/manager/editor）
├── store/               # 项目存储（schemas/project）
└── export/              # 封面生成 + EPUB/DOCX 多格式导出
webapp/
├── server.py            # FastAPI 组装壳（导入路由注册 + 静态前端托管 + 启动入口）
├── core.py              # 核心层：共享状态/鉴权/项目工具/结算/生成线程/任务持久化
├── routers/             # 按域拆分路由：system/auth/me/skills/admin/projects*
│                        # projects 域再拆 5 子模块：base/outline/chapters/world/generation（共 90 路由）
├── db.py                # MySQL 数据层（连接池，用户/会话/余额/代理/返佣/退款/skills）
├── secret_encrypt.py    # 敏感配置加密（BYOK Key，Fernet）
├── pricing.py           # 定价档位
├── referral.py          # 代理商业参数（折扣/返佣比例/返佣基数）
├── mailer.py            # 邮件通知（退款申请等）
├── logger.py            # 统一日志（文件滚动 + 控制台）
├── admin_cli.py         # 站长命令行管理工具
└── frontend/            # Vue 3 + Element Plus 前端（src/，产物 dist/）
docs/                    # 架构/文件索引/决策记录（AI 助手知识库，见 AGENTS.md）
tests/                   # 本地测试（已入库，见「测试」节说明）
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

用 `mock` provider 离线跑通全部流程，无需任何 API Key。覆盖：配置加载、字数换算、项目落盘往返、记忆上下文、断点续传幂等、正文摘要剥离、txt 导出、强制结局判定、HTML 标签清洗等。

> 注意：`tests/` 已入库随仓库提交（第二批起）；改动结算/权限/加密/路由拆分等关键路径时，请先在本地跑通测试再发版（当前 29 个用例全绿）。

## 说明与边界

- API Key 不落盘到项目目录：项目 `config.json` 只存 Provider/模型等，Key 每次从配置文件或环境变量补回。
- 输出字数是"目标值"，实际由模型决定；可在 `llm.max_tokens` 调大单次上限。
- 联网搜索需第三方搜索 API Key，未配置时调研退化为内置知识库 + 模型先验，质量仍可用。
- 邮件通知需配置 SMTP（QQ/163 邮箱的 SMTP 授权码），未配置时退款申请仍可提交，站长在后台手动查看处理。
- 本工具用于个人创作辅助，请勿用于批量生产侵权或违反平台规则的内容。

## 更新记录

- **2026-09-23**：第四次发版（master e5b19f7）——① 用户自有内容导入：上传大纲支持 .txt/.md/.docx（≤10MB，`extract_text` 统一提取）与粘贴文本（≤10 万字）；创建项目默认**原样采用用户大纲不自动重写**（扩写模式同样在点击「重生成」时才重写），世界观不自动生成（手动触发「重生成世界观」后派生）；项目详情页新增「重新导入大纲」（POST /api/projects/{id}/outline/import），重导入自动备份旧大纲为快照并可还原；② 大纲快照体验收敛：快照列表新增 `display` 可读时间（自动备份/导入前/重生成前 → MM-DD HH:MM:SS），重生成大纲后立即刷新快照列表、生成完成也刷新；工具 tab 移除重复的「重新生成」（大纲/世界观）与「大纲快照」入口，统一到详情页顶部。部署：OSS 上传中途连续超时（4 次），重试第 5 次成功；MD5 校验一致后解压 → `docker compose up -d --build`；验证：容器 healthy、`/api/health` 200、主 JS 881KB 无白屏。
- **2026-09-21**：第三次发版（master 08e8078）——① 生成提示词口头禅频次约束：`writer.md` 新增「同句口头禅每章最多 1-2 次、禁止章末机械复现」，`architect.md` 禁止把口头禅写进章节 `core_event`/`transition` 作"每章复现"指令（修复《我的地球Online日记》45 万字中"先记下来"出现 150 次的复读问题）；② dist 移出仓库（fix/dist-not-in-repo）：历史误提交的 `webapp/frontend/dist` 移除跟踪，Docker 多阶段构建在容器内自动 build，不依赖仓库 dist。部署验证：容器 healthy、域名 /api/health 200、主 JS 881KB 无白屏。
- **2026-09-21**：第二次发版（master e89d563）——① 数据与代码隔离：新增 `novel_gen/utils/paths.py`（DATA_DIR 环境变量解析，默认代码根）+ `novel_gen/utils/migrate.py`（复制→校验→删源迁移工具），`webapp/core.py` / `logger.py` / `novel_gen/store/project.py` / `novel_gen/export/book_formats.py` 4 处路径硬编码统一改为读 DATA_DIR；本地数据已迁移至 `D:\NovelData`（projects/output/logs），代码根零残留，新增 `tests/test_paths.py` 16 用例；② 前端导出按钮：项目详情页「导出 ▾」支持 TXT/EPUB/DOCX/MD（blob 下载），顺带修复 `/projects/{id}/download` 缺失 `import shutil` 的历史 bug（此前前端从未调用该接口，未暴露）；③ 新建目标字数下限修复：表单 `el-input-number :min=10000` 会把低于 1 万字的输入钳回 1 万，改为 min=0 允许短篇；④ 前端产物重建：index.html 与构建产物 hash 对齐，修复白屏报错。回归：pytest 60 全绿、导出接口冒烟（4 格式 200 + 非法 400 + 未登录 401）通过。
- **2026-09-20**：部署加固（deploy/compose-projects-vol，commit e279eb5）——`docker-compose.yml` 补挂 `novel_projects:/app/projects` 命名卷：服务器容器实际已挂载该卷但 compose 漏写，任何 `docker compose up` 重建容器都会去掉挂载导致项目目录从新容器消失。补写后重建无缝。**同日完成首次服务器部署**（实例 i-bp14oxtecrth3wbq6thc，/opt/novel-gen，docker compose：mysql-mozao + novel-gen），部署后验证：项目数据（projects 卷）无缝保留、域名 https://mozao.click 200、主 JS 881KB 无白屏、注册→登录→建书→详情→登出全链路冒烟通过、BYOK 加密列迁移生效（llm_provider/llm_api_key/llm_base_url/llm_model）、`.llm_encrypt.key` 已随部署放置服务器。
- **2026-09-21**（feat/style-role-agent）：⑫ 写作风格抽象为 agent 角色（StylistAgent 风格师）——新增 `style_roles`（平台内置+用户自建，role_prompt 人设）/ `style_role_skills`（挂载 admin 平台技能 + user 我的技能）两张表与迁移，启动时从 skills_library/style-references 幂等种子化 4 个默认风格角色；NovelConfig/ProjectStore 持久化 style_role_id/style_role_prompt（创建项目快照）；写手 ChapterWriter 与扩写路径注入风格人设（manager._with_stylist）；新增 7 个风格角色路由（用户级 6 + 管理级 1）；前端建书可选风格角色、工具面板支持风格角色增删改与技能挂载；测试 29→44 全绿，端到端冒烟验证风格人设+挂载技能真实进入正文系统提示。
- **2026-09-20**：第四批优化（feat/optimization-batch4）——⑪ 项目域再拆分：`routers/projects.py`（1150 行/43 路由）拆为 5 个功能子模块——`projects_base`（列表/创建/详情/退费/归档/克隆/删除/下载，8）、`projects_outline`（大纲/快照，7）、`projects_chapters`（章节/审校/扩写/批量，13）、`projects_world`（世界观/锁定/调研/伏笔/封面，10）、`projects_generation`（生成控制，5）。路由路径/方法/返回体不变，回归：路由集合 96=96 一致、29 pytest 绿、端到端 + 业务冒烟通过。
- **2026-09-20**：第三批优化（feat/optimization-batch3）——⑩ 最大架构债：原 2853 行单文件 `webapp/server.py` 拆分为 `webapp/core.py`（共享状态/鉴权/项目工具/结算/生成线程/任务持久化）+ `webapp/routers/`（按域：system/auth/me/skills/admin/projects，90 个路由）+ `server.py` 组装壳（仅导入注册 + 静态托管 + 启动入口）。路由路径/方法/返回体逐字节不变，回归验证：拆前拆后 openapi 路由集合 96=96 完全一致、全量 29 个 pytest 用例绿、服务端到端 health/pricing/鉴权/静态页正常、注册→建书→详情→登出业务冒烟通过；`tests/test_settle.py` 结算测试目标由 server 壳改为 core（monkeypatch 作用点随拆分迁移）。
- **2026-09-20**：第二批优化（feat/optimization-batch2）——⑥ 结算重构：统一 `_compute_settle` 计算口径（增量/整批共用），增量结算改为**先落盘结算基线再扣费**（P0 资金安全：消除"扣费后崩溃导致重启重复扣费"窗口，宁可漏扣不重扣）；⑦ 生成任务持久化：线程启动落盘 `task.json`、结束清理，服务启动（lifespan）自动重拾崩溃/重启中断的生成/大纲/批量任务并从断点续跑；⑨ 测试恢复入库（tests/ 取消 gitignore），新增加密/meta 缓存/结算单元测试 17 个（共 29 个，全绿）。
- **2026-09-20**：第一批优化（feat/optimization-batch1）——① BYOK Key 加密落库（`webapp/secret_encrypt.py`，Fernet；存量明文启动自动迁移）；② 项目列表性能（`meta.json` 轻量统计缓存，章节未变化时零章节读取）；③ 文档对齐（server.py 头注释/README/WIKI 与实际代码一致）；④ 清理部署产物与已淘汰的 webui 目录。附带 P0 修复：`get_user_llm` 键名与调用方契约不符导致 BYOK 自配 key 从未生效。
- **2026-09-17**：修复**扩写模式**（用户上传大纲，`mode=expand`）重新生成大纲后分卷为空的问题——扩写提示词 `expand_outline_prompt` 原只让模型输出 title/intro/chapters，现补 `core_conflict / main_line / volumes`（每卷 no/name/intro/main_conflict/character_growth/climax/start_ch/end_ch）；`regen_outline` 改为删 outline.json 后再起后台线程（不再先写空壳导致「已存在则复用」直接跳过）；前端「重生成大纲/世界观」按钮改走带 token 的封装请求。服务器无 unzip，dist.zip 用 `python3 -m zipfile -e` 解压。详见 WIKI.md 第 14 节。


