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
- **开源**：代码以 MIT 许可证开源（`https://github.com/muchun0/MoZao`），任何人可 fork 自部署——自带 API Key 免费使用（密钥只存环境变量）；官方托管服务继续按字数收费。README「自部署指南（Self-hosting）」为自部署入口

### 关键功能清单

| 模块 | 能力 |
|---|---|
| 账号 | 邮箱注册（验证码）、登录、忘记密码（邮件重置）、会话管理、登录限流 |
| 建书 | 弹窗按「生成模式→基础信息→故事设定→节奏与文风→质量选项→费用估算」分组：生成模式置顶（auto 全自动 / expand AI 扩写，选扩写直接展开现有字数/上传大纲/大纲内容/手写内容/附带题材调研）；基础信息含书名/题材/目标字数/每章字数/风格/风格角色/写作技能；故事设定含核心设定/故事背景/主角设定/预设人物（回车标签）/剧情走向/避雷元素（回车标签）；节奏与文风含爽点密度/剧情速度/自定义提示词；质量选项把高质量模式（3 元/万字）与编辑质检/去AI味/防同质化/人物立体化/记忆窗口合并；三种内容类型：① 智能生成（联网调研→大纲→正文）；② AI 扩写（大纲/梗概扩写成文）；③ 续写/扩写已有内容（粘贴或上传手写正文） |
| 大纲 | 上传大纲智能解析自动填表；三级大纲编辑器（总纲→分卷→章节）；章节跨分卷拖拽、分卷增删；大纲快照保存/回滚；单独生成大纲/世界观/分卷 |
| 内容解析 | 上传/粘贴已有正文自动切分章节预览 |
| 生成 | 生成前联网搜索热门小说参考文风（博查 API）；多 agent 流水线（调研员→架构师→写手→审校→读者）；**章节生成按钮三态**（无章节=开始生成 / 有章节未完成=继续生成，成本防护/余额耗尽终止后同样可从断点继续 / 全部完成=重新生成整本重写）；**整本重写** `POST /api/projects/{id}/chapters/regenerate`（保留大纲世界观、锁定章节跳过） |
| 质量 | 去AI味、防同质化、结局优化、番茄写作纪律、Reader 读者视角审校（追更欲评分）；**生成质量升级 P0-P2**：前文直读/人物动态状态/全书质量汇总（P0）、行动表先行/场景级下沉/Lorebook 裁剪/AI痕迹评分（P1）、动态模块随机化/风格范文锚定/全书终审/质量指标报表（P2），见 §4 生成质量升级；**正文去机械感（REQ-写作正向引导，2026-10-15）**：提示词从"禁令驱动"改"正向引导"（场景规则 23→10 条）、演七分说三分（白描为主+情绪顶点直白点破）、机制数字"权威当前值只抄不推"、AI 腔词表改警示+密度意识、卷→章→场景三级目标传导（卷卡）、踩坑+正向样例双账本（good 账本：高分章/用户修订好片段自动学习并注入"参考质感非内容来源"） |
| 创作设计与读者体验 | **章级设计卡（chapter_design）**：从章级 conflict/emotion + 冲突矩阵派生逐章 A1–A7 设计卡注入写章（零 LLM）；**读者认知账本（reader_ledger）**：读者已知/在等/被误导三态 + 伏笔临期/超时/回收状态机 + 爽点节奏配额，写章注入读者视角约束（零 LLM） |
| 产品化工具（工具面板） | **场景体检**：对已生成章节做字数/事件推进/情节重演/章首承接/章末钩子 5 维度确定性体检并给修复建议（对标 Sudowrite Scene Inspector）；**三分支续写**：一次生成 3 方向（冲突升级/埋伏笔/转折）续写候选，选一条复制使用（预览模式不自动改正文）；**生成过程（思维链）**：只读展示本章「计划→产出→校验」链条；**Beat 覆盖度门禁**：正文对大纲逐拍覆盖率自检（绿/黄/红标） |
| 封面 | 硅基流动 Tongyi-MAI/Z-Image-Turbo 一体封面；每本限 5 次 |
| 作品管理 | 章节阅读/编辑/重写、**选中段落扩写/续写**、大纲编辑、从某章重生成（免费每日限次）、世界观/调研查看、下载、退款删除、归档、克隆；**章节版本化**（REQ-章节版本化，git 式多版本保留）：重写/重生成追加新版本、旧版保留，阅读器「版本」弹窗逐章选择/回退最优版本（`GET /chapters/{no}/versions`、`POST /chapters/{no}/versions/{v}/select`） |；**2026-10-17 版本质量扩展**：每次生成（含自动质检重写稿）都入版本库，弹窗每版显示质检分/改稿建议明细，支持删除非当前版本，未被用户操作时默认展示质检最高分一版（`DELETE /chapters/{no}/versions/{v}`）| Skills | 全局写作风格（admin）、我的自定义 Skills、Skill 市场（发布/安装）、**风格角色**（写作风格抽象为 agent 角色，可挂载技能，建书一键套用）；技能与风格角色管理入口已从项目工具面板迁至**用户工作台**（/workspace） |
| 计费 | 创建不扣费；按 AI 实际字数结算；余额≤0 自动停止；BYOK 不扣费 |
| 用户工作台 | `/workspace` 6 tab：概览（余额/可退/返佣/下级指标）/ 账户与账单（充值流水/退款申请）/ 推广返佣（邀请码绑定/下级列表/返佣明细）/ 模型配置（BYOK）/ 我的技能（新建/市场发布安装）/ 风格角色（增删改/挂载技能/参考文风） |
| 代理分销 | 一级代理：本人消费 8 折 + 下级消费 10% 返佣 |
| 退款 | 用户申请余额退款 → 邮件通知站长 → 人工处理 |
| 站长后台 | 用户/充值/代理/退款/日志/统计/设置/Skills 管理 |

---

## 2. 技术架构

### 技术栈

- 后端：Python 3.13 · FastAPI · Uvicorn · MySQL（库 `mozao`；本地 root/yue_426983）
- 前端：Vue 3 + Element Plus + Vite + Pinia + Vue Router（`webapp/frontend/`）
- AI：DeepSeek（文本）、博查 Bocha（联网调研）、硅基流动 Tongyi-MAI/Z-Image-Turbo（封面）
- 部署：Docker 多阶段构建（node:20-alpine 编译前端 → python:3.13-slim 运行），docker-compose 编排（mysql-mozao + novel-gen）

### 目录结构

```
D:\PythonProjects\DouBao\
├── run_web.py                  # Web 服务入口
├── Dockerfile / docker-compose.yml / .dockerignore
├── WIKI.md / API.md / AGENTS.md / README.md / LICENSE（MIT）
├── requirements.txt
├── novel_gen/                  # 生成引擎（纯 Python，无 Web 依赖）
│   ├── config.py               # NovelConfig + FEATURE_FLAGS 开关注册表
│   ├── safety.py               # 输入防护（Prompt 注入检测/输出合规）
│   ├── llm/                    # Provider 抽象（base/factory/providers/usage，自动埋点+成本防护）
│   ├── research/               # 调研阶段（内置知识库 + 联网 + 拆书库回流）
│   ├── pipeline/
│   │   ├── manager.py          # 流水线编排（校验合并/终审/双锚注入）
│   │   ├── orchestrator.py     # 架构总控（StageGate 阶段门禁/冲突仲裁）
│   │   ├── validator.py        # 独立逻辑校验（5 类专项 + 机制卡三要素）
│   │   ├── stages.py           # 场景级章节写作（拆场景→逐场景→润色，防重演闭环）
│   │   ├── beats.py            # 情节节拍序列检测（LCS 重演判定，零 LLM）
│   │   ├── memory.py           # 章节记忆（设定库+摘要回填+分章设定卡注入）
│   │   ├── setting_cards.py    # 分章设定卡索引（Token 优化 P1-1）
│   │   ├── chapter_state.py    # 章内/章间状态账本（机制数字确定性闭环）
│   │   ├── character_state.py  # 人物动态状态（逐章出场/状态追踪）
│   │   ├── book_memory.py      # 小说级记忆（设定/事件/踩坑/经验四账本）
│   │   ├── quality.py          # 结果自校验（确定性规则 + 定点重写 + 记忆注入）
│   │   ├── review_summary.py   # 全书质量汇总
│   │   ├── ai_taste.py         # AI 痕迹评分
│   │   ├── style_anchor.py     # 双锚风格锚定（个人旧稿锚 + 题材锚）
│   │   ├── trace.py            # 生成轨迹（质量报表数据底座）
│   │   └── agents/             # 12 个角色（6 主角色 + 3 生产层 + 3 专项）
│   │       ├── base.py         # BaseAgent（按 role 挂 skill）
│   │       ├── researcher.py / architect.py / writer.py / reviewer.py
│   │       ├── reader.py / stylist.py / theme_character.py / world_rules.py
│   │       ├── pacing.py / opening.py / ending.py / dialogue.py
│   │       └── prompts/        # 各角色独立提示词
│   ├── store/                  # ProjectStore（产物 JSON 权威源）/ schemas
│   ├── export/                 # TXT/EPUB/DOCX/MD 导出 + AI 封面
│   └── utils/                  # chapter_splitter / paths(DATA_DIR) / migrate
└── webapp/
    ├── server.py               # FastAPI 组装壳（路由注册 + 静态托管 + 启动入口）
    ├── core.py                 # 核心层：鉴权/项目工具/结算（先落基线再扣费）/生成线程/任务持久化
    ├── routers/                # 9 域路由（96 个）：system/auth/me/skills/style_roles/admin
    │                           #   + projects 域 5 子模块：base/outline/chapters/world/generation
    ├── db/                     # MySQL 数据层包：base/users/projects/skills/style_roles/teardowns/traces
    ├── secret_encrypt.py       # 敏感配置加密（BYOK Key，Fernet）
    ├── pricing.py              # 按字数计费
    ├── referral.py             # 代理/分销
    ├── verify_code.py          # 邮箱验证码
    ├── mailer.py               # SMTP 邮件
    ├── outline_parser.py       # 上传大纲解析（.txt/.md/.docx）
    ├── logger.py               # 日志
    ├── admin_cli.py            # 命令行管理
    └── frontend/               # Vue3 前端（src/ + dist/，dist 不入库）
```

### 多智能体角色

| 角色 | 文件 | 职责 | 阶段 |
|---|---|---|---|
| Researcher 调研 | researcher.py | 内置知识库 + 联网调研 → 提炼热门小说共性报告 | research |
| Architect 架构 | architect.py | 三级大纲、人物世界观、时间线；收敛为骨架生产者，编排生产层与专项 Agent | outline/world |
| theme_character 主题人物 | theme_character.py | 主题命题 / 三层冲突 / 主角弧光 / 配角动机 | world |
| world_rules 世界观规则 | world_rules.py | 世界观 / 金手指五要素 / 设定禁忌 | world |
| pacing 节奏伏笔 | pacing.py | 关键节点 / 分卷节奏 / 伏笔回收闭环 | outline |
| Writer 写手 | writer.py | 逐章正文（场景级生成，叠加 Stylist 风格人设） | chapter |
| Reviewer 审校 | reviewer.py | 一致性/逻辑/合规审校（5 类专项 + 机制卡三要素） | review |
| Reader 读者 | reader.py | 追更读者视角审校（代入感/爽点/钩子） | review |
| Stylist 风格师 | stylist.py | 写作风格角色（人设 + 挂载技能），注入写手正文与扩写路径 | chapter/expand |
| opening 开篇 | opening.py | 黄金三章强开篇设计（回填前三章 core_event/goal） | outline |
| ending 结局 | ending.py | 终局设计卡（终局状态/主题落点/人物归宿），末几章按卡收束防烂尾 | outline/world |
| dialogue 对白 | dialogue.py | 聚合全书人物 speech_style 生成对话风格指南（确定性，零 LLM） | world |

**Skill 挂载**：`skills.role` 空=全局；填对应 role 只注入该角色（source 区分 platform/user/market，统一表 `skills`）。
**风格角色**：`style_roles`（user_id=0 平台内置 / >0 用户自建，role_prompt 人设，reference_text 范文锚）+ `style_role_skills`（role_id ↔ skill_type(admin/user)/skill_id 挂载关系）；建书选风格角色后组装人设+挂载技能快照进 config（style_role_prompt），由 StylistAgent 注入写手正文与扩写路径。
**门禁与校验**：StageGate 阶段门禁（产出→准出校验→不合格自动重生成 ≤N 轮→超限仲裁放行）+ LogicValidator 独立逻辑校验。

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

### 生成质量升级（P0/P1/P2，2026-09-28）

- **P0 基础质量**：① 前文直读——逐章注入前文关键内容替代纯摘要；② 人物动态状态——`character_state.json` 逐章追踪人物出场/动机/关系/状态，跨章一致性；③ 全书质量汇总——review.json 沉淀全部质检问题 + 工具面板「改稿建议」展示（含全书终审块）
- **P1 生成控制**：① 行动表先行——先列章节行动表再写作；② 场景级下沉——提示词模板细化到场景；③ Lorebook 裁剪——按章节人物/场景/伏笔裁剪设定库注入（前文直读+动态人物状态数据源）；④ AI 痕迹评分——`ai_taste` 对正文打分并附改进建议
- **P2 收尾增强**：① 动态模块随机化——`_pick_dynamic_module` 随机+相邻去重+seed 可复现；② 风格范文锚定——`style_roles.reference_text` 迁移透传+快照进 config，`_style_reference` 注入（空则不注入）；③ 全书终审——`final_review` 开关（高质量模式默认开），落 final_review.json，异常降级不阻塞导出结算；④ 质量指标报表——`auto_revised` 真值落库，`traces._aggregate_quality/quality_report` 聚合，`GET /api/admin/quality-report`（站长专属，8 指标卡+得分分布+问题 TopN+弃书风险+阶段成本，无数据空态不报错）
- **开关注册表**：所有功能开关集中到 `novel_gen/config.py` 的 `FEATURE_FLAGS` 注册表管理（如 `review/final_review` 等），按功能模块分组

### 设定静态/动态/揭示三层约束（2026-09-30）

- **S 静态（宪法）**：全书不变的设定写入 `architect.md`/`world_rules.md` 的「设定分层约束（硬规则）」——世界法则/机制卡/金手指/禁忌/人物本质/地点/时钟/势力等，任何 Agent 不得改写
- **D 动态（账本）**：随章变化的当前数值/生死/物品/时间/事实，由 `chapter_state.json` 逐章更新，禁止写进静态描述；`core_event`/`transition` 是"已发生事实"权威
- **R 揭示（禁剧透）**：世界观真相走 `world_reveals`（from_ch 揭示章 / foreshadow_ch 铺垫章 / hint 可写暗示），铺垫提前 2-3 章，真相在 from_ch 前禁止泄露

### 揭示执行跟踪（REQ-揭示执行跟踪，2026-09-30）

- `WorldReveal` 新增 `keywords` 真相锚定词；每章正文落盘后 `detect_reveal_hits` 做确定性命中检测（零 LLM），累积到项目目录 `reveal_status.json`
- 全书完成 `check_reveal_execution` 校验：**提前剧透**（锚定词在 from_ch 前出现）→ fatal；**到点未揭示**（from_ch 已过正文未提及）→ major；旧数据无 keywords 不误报
- 开关 `reveal_tracking`（FEATURE_FLAGS，默认 True）；失败降级不阻塞生成/结算

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
| skills | **统一技能表（REQ-技能统一 阶段二）**：source∈{platform,user,market} + owner_id 区分权限；含 role/applicable_stages/version/downloads/market_id；旧 writing_skills/user_skills/skill_market 保留不 DROP（迁移回退） |
| style_roles | id, user_id(0=平台/用户自建), name, description, role_prompt, **reference_text**（范文锚 P2-1）, is_active, sort_order, created_at |
| style_role_skills | id, role_id, skill_type(admin/user), skill_id, sort_order, created_at |
| book_teardown | id, genre, title, summary, content(JSON), source_project, source_type(research/seed/manual), user_id, view_count, status（爆款拆书库） |
| agent_trace | id, trace_id, user_id, project_id, event, payload(JSON), created_at（全链路快照，质量报表聚合底座 P2-4） |

- 章节存项目目录文件（不在 DB），`source=ai|user` 记录在文件头；**章节版本化**：`chapters/<no>.json` 为当前选中版本，历史版本存档 `chapters/<no>/v<k>.json` + `chapter_versions.json` 索引（每章上限 15、HEAD 不淘汰），`load_chapter`/`list_chapters` 读当前选中版本（对外行为不变）
- `init_db()` 启动时自动建表 + 增量迁移

---

## 6. 配置（.env，不入库）

| 键 | 必填 | 说明 |
|---|---|---|
| MYSQL_ROOT_PASSWORD | ✅ | MySQL root 密码（compose 初始化 mysql-mozao 用，务必强密码） |
| DEEPSEEK_API_KEY | 至少一种 | 文本生成模型 Key（默认 Provider=deepseek） |
| ARK_API_KEY | 可选 | 火山方舟（豆包）Key（provider=doubao 时用） |
| OPENAI_API_KEY | 可选 | OpenAI 兼容 Key（provider=openai 时用） |
| BOCHA_API_KEY | 可选 | 博查联网调研 Key |
| SERPER_API_KEY | 可选 | Serper（Google）联网调研 Key |
| DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME | 可选 | 应用连 MySQL 参数（默认 127.0.0.1:3306 / root / 空 / mozao；单机 compose 无需改） |
| ADMIN_EMAIL | 可选 | 站长收件邮箱（退款通知） |
| SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS | 可选 | SMTP 发信（验证码/通知；不配则验证码不可用） |
| LLM_KEY_ENCRYPT_SECRET | 可选 | BYOK Key 加密密钥（可选；未配置时自动生成根目录 `.llm_encrypt.key`，已 gitignore，服务器 zip 解压不覆盖） |
| ALLOWED_ORIGINS | 可选 | CORS 白名单 |
| DATA_DIR | 可选 | 数据目录根（projects/output/logs 的父级，默认代码根）。本地 `D:\NovelData`；服务器不设时容器内回退 `/app`，与既有卷挂载一致，零影响 |

> 完整自部署指引（含 .env 最小示例、docker compose 启动、BYOK 用法）见 README「自部署指南（Self-hosting）」。开源仓库：https://github.com/muchun0/MoZao（MIT License）。

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
| 退款/质量报表接口 500（`TypeError: not enough arguments for format string`） | pymysql 参数化 SQL 中 LIKE 字面量 `%` 必须转义为 `%%`（如 `LIKE '管理员手动充值%%'`），否则被误当作占位符；无参数执行的 SQL（如 `conn.execute(sql)`）不受影响 |
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
- 测试随仓库维护：tests/ 已入库（加密/meta 缓存/结算/Agent/风格角色/拆书库/生成质量升级/防重演闭环/揭示跟踪/Token 优化/用户级记忆库共 627 个用例，本地全绿；发版前至少 `pytest -m "p0 or p1"` 全绿）

---

- 部署记录（2026-10-16）：第二十一次发版（合并 devtest → master，da27b4c）。内容：**章节原子提交 + 投影一致性（A/B）+ 章节正文版本化**。① 一期·章节提交（feat/chapter-commit，借鉴 webnovel「写与记分离」）：新增 `pipeline/commit.py` `ChapterCommitter`，把 manager 章末 4 个分散入账（人物状态/章末机制/揭示执行/读者账本）收敛为一次统一提交——先落基线、失败留痕 incomplete、同章幂等 skip、每次写 `projection_log.jsonl`；`check_projection` 扫描 10 个派生视图定位账本落后章号，命令行 `tools/projection_check.py`（lagging 退出码 2）。开关 `chapter_commit` 默认 True；② 二期·章节正文版本化（feat/chapter-versions，git 式多版本保留与用户选择）：`save_chapter` 从覆盖改追加版本——`chapters/<no>.json` 仍为当前选中（HEAD），历史版本存档 `chapters/<no>/v<k>.json` + `chapter_versions.json` 索引（source=ai/user/rewrite/legacy）；内容相同幂等、不同追加新版更新选中；每章上限 15 淘汰最旧（HEAD 不淘汰）；`regenerate_from` 改方案 a（不删章节，追加候选版本、用户逐章自选）；`edit_chapter` 追加 user 版本；新增 `GET /chapters/{no}/versions` + `POST /chapters/{no}/versions/{v}/select`；前端阅读器加版本列表+选择/回退 UI。回归：全量 730 pytest 全绿（新增 chapter_commit 14 + chapter_versions 8）+ 前端 build 通过。**第二十次（正文去机械感，2026-10-15 未部署）内容随本次一并上线**。部署：git archive master（da27b4c）→ workbench upload → python3 -m zipfile -e 解压 → docker compose up -d --build（基础镜像 python:3.13-slim/node:20-alpine 首建时遇 daocloud 镜像源 502 与 docker.io 直连超时，镜像源恢复后重试成功）；验证：mysql-mozao healthy、novel-gen Up healthy、/api/health 200、主 JS 1.1MB（index-DAGe3qr1.js）无白屏。**部署后反查验证（2026-10-16）**：服务器关键文件 7/7（book_memory/stages/templates/writer.md/projects_chapters/schemas/config）git blob（CRLF 归一化为 LF 后）与本地 master da27b4c **完全一致**；zip→解压 cmp 无损；功能特征宿主机 + 容器内双确认（good 账本/卷卡/机制"只抄不推"/白描措辞/record_good_manual 全命中）。文档：README/WIKI、docs/decisions.md/architecture.md/filemap.md、REQ-借鉴webnovel.md、REQ-章节版本化.md 已同步。

- 合并记录（2026-10-15）：第二十次合并（devtest → master，8783cca，**未部署，服务器发版待执行**）。内容：**正文去机械感优化（REQ-写作正向引导 一/二/三期）**——① 一期（feat/positive-guidance-writing）：场景规则 23→10 条（禁令改正向引导）、机制数字"权威当前值只抄不推"、AI 腔词表警示化+明喻密度/零直白检测、writer.md 演七分说三分；② 二期（feat/volume-cards）：卷卡传导（卷→章→场景三级目标）+ 场景字数预算收敛 + 机制权威基线三路径补盲（场景/整章回退/重写）+ 账本双源反推 + 寸字段累计格式提取；③ 三期（feat/good-memory）：踩坑+正向样例双账本——`BookMemory.goods` 高分章（≥85 且无大改）段落自动入账（`record_good`）+ 用户编辑保存免质检入账（`record_good_manual`），按手法确定性分类（对话-潜台词/战斗-动作链/情绪-白描+点破/环境-物象白描/叙事-节奏），每类 ≤5 条总量 ≤50 每条 ≤300 字，场景生成/重写双路径注入 `good_block`（参考质感非内容来源）。验收：全书重生成均分 81.4、通过 9/11（5 章满分，ch1 45→100），vs 二期 79.1/7、master 63.2/6 提升确定。回归：全量 707 pytest 全绿（P0/P1 433 全绿）。**决策：章节字数超长不触发整章级 LLM 处理**。文档：README/WIKI 已同步；docs/decisions.md、architecture.md、filemap.md、REQ-写作正向引导.md 已更新。部署：尚未执行（git archive master → workbench upload → docker compose up -d --build 待发版时执行），内容已随第二十一次发版（2026-10-16，da27b4c）一并上线。

- 部署记录（2026-10-14）：第十八次发版（合并 devtest → master，e6fa991）。内容：**题材锚默认关闭（决策落地，feat/teardown-anchor-default-off）**。背景：真人味调研发现"2026-10-14 决策默认 False"未落代码——`novel_gen/config.py` FEATURE_FLAGS `teardown_style_anchor` 默认值仍 True、`webapp/core.py` getattr 兜底 True，新书未显式配置时题材锚仍默认开启（已随第十七次发版上线）。本次：① `novel_gen/config.py:74` 默认值 True→False（注释注明朱雀归因依据：同新粒度下无锚 18.25% vs 有锚 55.01% AI 特征、锚诱发重复循环/字数注水）；② `webapp/core.py:836` getattr 兜底 True→False 保持一致；③ 新增守护测试 `test_teardown_style_anchor_default_off` 防回归；④ 4 个依赖旧默认值的注入测试（scene/chapter/expand prompt 注入、_inject 写文件）改显式 `cfg.teardown_style_anchor = True` 保留"开启时注入"行为覆盖。效果：新书/新项目未显式配置时题材锚默认不注入；显式打开仍可用。回归：全量 633 pytest 全绿（devtest 与 master 各验证一次）。文档：docs/decisions.md（状态差修复记录）已同步。部署：git archive master（novel_master_20261014_anchor_off.zip）→ workbench upload /tmp → python3 -m zipfile -e 解压 → docker compose up -d --build；验证：mysql-mozao healthy、novel-gen healthy、/api/health 200、主 JS 1.1MB（index-B7XQeMk-.js）无白屏、/api/projects 未登录 401。
- 部署记录（2026-10-14）：第十七次发版（合并 devtest → master，6e0c3ef）。内容：**场景粒度优化（REQ-scene-plan-align）**。背景：朱雀 AI 检测实验（用户实测）暴露 2500 字/章被按大纲拍数拆成 4 场景×~625 字，模型靠"死亡-复活-面板循环注水"凑字数（触发节拍重演拦截→回退整章）；调研（番茄官方单章节奏/话本《情节设计密码》/Adazing/Alice Sudlow/iWrity）确认行业单场景 800-2500 字、一章 1-3 场景。本次：① `_scene_count_for_words(chapter_words)`——场景数由章节字数反推：N=clamp(int(words/1200+0.5),1,4)，1200 字/场景为基准；`_derive_events_from_moves` 合并阈值 4→N（因果粘连合并优先）；chapter_words≤0 时 N=4 旧调用行为零变化；② `_validate_scene_plan` 新增单场景 target_words 下限 max(800, 章字数×25%)——低于下限=拆得太碎回退整章（2500 字章 4×625 即被拦）；③ 骨架/refs/P0-1 账本/P0-17 core_event 覆盖天然适配。效果：ch1 大纲 5 拍→2 事件→2 场景×~1250 字，循环注水消失（面板 1 次/死亡-复活各 1 次）、编辑审稿 score=7.5、2876 字质检通过。**朱雀归因闭环（2026-10-14）**：AI 特征 76.86%（旧基线）→ 72.45%（v13 无锚）→ 90.5%（锚版）→ 81.76%（新无锚）→ **18.25%（2场景粒度无锚）**；锚+新粒度对照组 55.01%——题材锚判定独立负面变量（诱发重复循环、超长注水 65%、三次生成全触发防重演拦截），**决策：默认关闭题材锚（teardown_style_anchor=False）**。回归：全量 632 pytest 全绿（新增 tests/test_scene_granularity.py 5 例；同步更新 test_scene_plan_align/test_scene_anti_repeat/test_teardowns 断言）。文档：docs/decisions.md（2026-10-13 场景粒度决策 + 10-14 复测/归因）、architecture.md、filemap.md 已同步。部署：本地 git merge devtest→master 后 git push origin master 推送（5ebf85c..6e0c3ef）；服务器发版待 workbench 上传执行（§7 流程）。
- 部署记录（2026-10-11）：第十六次发版（合并 devtest → master，e391439）。内容：**用户级记忆库（跨书创作偏好继承，REQ/TECH-用户级记忆库.md）**——三层记忆架构（章级→书级 BookMemory→用户级）补用户级层：① `webapp/user_memory.py` 新增 `UserMemoryService`——`users/<uid>/user_memory.json` 存偏好画像（题材/节奏/避雷，新书显式输入按项目幂等入账）+ 跨书纪律（书级 pitfalls 蒸馏，全量重算式幂等：book_contribs 存每本书最新贡献快照，同贡献跳过、变化时移除旧贡献重写）+ 个人文风锚（edit_chapter 人工修订跨书登记，上限 30 淘汰最旧）；全部写路径**零额外 LLM**；② 继承：新书创建 `record_project_signals` + `_inject_user_memory` 写项目快照 `user_memory_snapshot.json`（webapp 写 / novel_gen 读，与题材锚同分层，读侧校验 uid 正整数防跨项目串用），`ChapterWriter._user_memory_block` 懒读注入 scene/chapter/expand prompt（跨 ≥2 本书纪律 Top-N=`user_memory_inject_top`=5 + 文风锚 ≤600 字，软约束）；③ 新开关 `user_memory` 默认 True；`GET/POST /api/me/user-memory*` 只读/清空隐私兜底；④ 注入优先级：项目显式设置 > 用户级画像 > 平台默认，题材不匹配偏好不注入；数字/节拍/拼接确定性校验仍留代码硬校验层（用户级只做软约束）。回归：全量 627 pytest 全绿（新增 tests/test_user_memory.py 15 用例）。文档：docs/decisions.md（2026-10-11 决策 + Windows 写盘坑）、architecture.md、filemap.md、REQ/TECH-用户级记忆库.md 已同步。部署：git archive master → workbench upload → python3 -m zipfile -e 解压 → docker compose up -d --build；验证：mysql-mozao Running/Healthy、novel-gen Started、/api/health 200、主 JS 1.1MB（index-B7XQeMk-.js）无白屏。
- 部署记录（2026-10-10）：第十五次发版（合并 devtest → master，818faec）。内容：**场景文本防重演闭环（P0-1~18 生成端修复包 + v10/v11 实证修复 + v12 真实重生成验收）**。背景：拆场景事件互斥+词面节拍防线（第十二次）拦不住换措辞重演、机制数字幻觉（"累计死亡次数"行）与字符 JSON 截断。本次：① **P0-15/16 机制字段注册表（closed-world）**——`chapter_state.py` 从机制基线派生字段名+方向（次数/剩余→decrease、蚀化/进度/寸→increase），`unknown_mechanism_fields` 白名单兜底，账本数字入 `book_memory.record_chapter_state` 链式继承，`ledger_check` 事件驱动双向校验（防换字段名绕过）；② **P0-17 大纲核心事件覆盖**——`_validate_scene_plan` 分段 LCS≥2 字、命中≥60%，未覆盖回退整章；质检 2.8d `_core_event_missing`；③ **P0-18 超长注水**——`_slim_scene` 单场景超 target×1.5 且非整句复读→精简 1 次，仍超保留不整章回退（字数不触发回退）；④ **v10 根因修复**（`33435ed`）——回退整章路径 scene_state_acc 空致质检 2.8c 整段跳过、幻觉行漏检：改 `if self.memory is not None` 自取注册表/账本、死亡口径统一「蚀化差」、寸字段事件增量豁免、回退/普通整章注入 `_panel_whitelist_block`；⑤ **v11 实证修复**（`9388626`）——账本入账无面板章 erosion 继承上一章末（永不破链）、质检链式取最近非空、`_pcv` 基线 0 跳过、`_chapter_healthy()` 保留旧版前健康检查（带病旧稿弃用落盘新稿）；⑥ **死亡判定通用+个性化两套独立标准**（P0-14，commit 7076550）——通用词面（跨书）+本书机制词（book_signals 独立正则/去重）互不污染。验收：《万墟之上》前 7 章第十一轮真实重生成（v12，日志 regen_ch1_7_v12.log）——ch3/ch6「累计死亡次数」幻觉行彻底消失、七章次数链 2→2→3(重置)→1→1→1→0/蚀化 97→100 只增/寸基线内波动、无重复段落、跨章面板链全绿、逐章通读全部 ★★★★★（黄金三章/人设/爽点/断章达标）；签约评估《万墟之上_前7章第十一轮重生成签约评估.md》结论达标。回归：全量 612 pytest 全绿（新增 beats/book_memory/mechanism_registry/panel_direction/scene_anti_repeat 等 7 个测试文件）。文档：docs/decisions.md（P0-15~18 决策 + v10/v11 实证段）、architecture.md、filemap.md、REQ-scene-plan-align.md 已同步。部署：本地 git archive master → scp 上传 /tmp → 服务器备份 /opt/backup → unzip 解压 → docker compose up -d --build（前端容器内多阶段构建）；验证：mysql-mozao healthy / novel-gen Up (healthy)、/api/health 200、主 JS 1.1MB（index-B7XQeMk-.js）无白屏、/api/projects 未登录 401 鉴权正常。
- 部署记录（2026-10-01）：第十二次发版（合并 devtest → master，3eab3b7）。内容：**章节质量防线·场景事件级去重（十轮迭代收官）**。背景：第 1 章重生成五轮实测——6649 字版"3 遍救人+死亡复刻"（拆场景 JSON 长输出结尾断裂→事前防线整条回退失效）、4874 字版"坡顶/墟口救人两版"（事件清单互斥拦不住场景正文换措辞重演）、3395 字版"岩台/墟口外救人两版+蚀痕/方位矛盾"（敌人措辞'三只灰卒'vs'三只赤尉伴生灰卒'致清单互斥与正文检测双漏检）。本次修复：① **拆场景 JSON 健壮化**——`_strip_json_block` 剥 ```json 包裹→从首 `{` 回溯最近可完整解析 `}` 前缀→修尾逗号；新增 `_extract_json_field` 字段级救回（events 在前，截断常可救）；失败重试 1 次（温度 0.5）仍败才回退整章，回退时经 `chapter_prompt(chapter_events=…)` 注入事件序列+final_state+"每事件只写一遍"纪律；② **场景正文事件级复写检测**（`_scene_text_rewrites_event`）——语义互斥只校验拆场景 events 清单，拦不住场景生成时模型把已发生事件换措辞重演；对每个场景正文提取事件槽位（冲突对象+救援专属动词 救/引开/砸/挡/扑/迎），与全部已完成事件比对，命中重试本场景并注入 `forbid_event`（禁止以任何形式再次发生，从事件结局后续写）；③ **事件槽位改被救/受害人物优先**——同一人物+同一动作即判重复（敌人词仅无人物时兜底、去量词），修敌人措辞不同双漏检；④ 拆场景成功日志（场景数/事件数）。回归：406 pytest 全绿 + 第四版真实正文回验命中。实机验收：第 1 章重生成 2148 字，拆场景 2 场景 2 事件成功，自校验 5 问题→自动重写 1 次→复检通过，质检 score=100/0 问题（A 结构/B 设定/C 语言/D 适配四档对照早期验收基线全过，残留仅字数 2148 略低于 2500，按"以质量为准、不因字数重写"口径保留）。部署：git archive master → workbench upload（MD5 一致）→ python3 -m zipfile -e 解压 → docker compose up -d --build；验证：mysql-mozao healthy / novel-gen healthy、/api/health 200、主 JS 1.1MB（index-B7XQeMk-.js）无白屏。
- 部署记录（2026-09-30）：第十一次发版（合并 devtest → master，93edd41）。内容：**生成 Token 消耗优化 P0+P1 全量（REQ-token优化.md）**。背景：09-27~09-29 密集上线的质量机制全依赖 LLM 调用，普通模式单章 LLM 调用从 ≈2 次膨胀到 ≈13~16 次（约 5 倍）；agent_trace 实测（847 条）输入侧占 73%（chapter 单次输入均值 3890、最大 9.3k）。按「前置解决优先」：① **分章设定卡索引**（新 `pipeline/setting_cards.py`，纯 Python 确定性前置脚本）——world 落盘后构建 `setting_cards.json`（出场人物用 `memory.predict_appearing_chars` 预测，生物/地点/势力/伏笔按「名称直判+关键词包含」匹配），memory 按章只注入相关设定子集（空列表=命中无项不注入、索引缺失/关闭回退全量；机制卡/主题/弧光/金手指/禁忌/时钟等强约束区永不裁剪）；② **场景状态抽取分流** `scene_state_mode=auto`（普通模式确定性面板正则零 LLM，高质量模式保留 LLM）；③ **章末/人物状态 LLM 抽取降频** `state_extract_frequency=3`（每 3 章 1 次 LLM，其余确定性）；④ **行动表并入拆场景** `action_plan_first` 默认 False（split_scenes 输出 `characters_action`，纯 Python 格式化注入，不再独立调用）；⑤ **整章润色仅高质量** `polish_hq_only`；⑥ **章内矛盾 LLM 自检仅高质量** `consistency_llm_check` 默认 False；⑦ **校验合并** `verify_combined`（一致性/读者/编辑 3 连调用 → 1 次，`verify_every` 3→5）；⑧ **编辑修正 diff 式** `editor_diff_revise`（只输出需改字段 max_tokens 4096→1200，Python 合并回大纲，失败回退整章重出）；⑨ **定点重写** `targeted_rewrite`（issue pos/原句锚点定位窗口 ±200 字，窗口覆盖 <60% 正文才局部重写并拼回，省 60~75% 重写输出）；⑩ 正文直读 2000→1200 字。开关全部走 FEATURE_FLAGS 注册表；**存量项目 config.json 已持久化旧值，不写迁移（用户拍板）；新增开关默认值对存量项目仅少调用方向生效（安全）**。回归：全量 357 pytest 全绿（新增 26 用例）+ HTTP 端到端冒烟 ALL PASS + mock 整合冒烟通过。文档：docs/decisions.md（2026-09-30 Token 消耗优化决策）、architecture.md、filemap.md 已同步；README 更新记录同步。
- 部署记录（2026-09-29）：第十次发版（合并 devtest → master，31d79b4）。内容（自第九次以来一批质量与设定加固）：① **章节衔接与主线**——写手注入本章 `core_event`（正文不得偏离大纲核心事件）；场景级生成强制承接上一章结尾（split_scenes 加 prev_tail、memory 注入本章 transition）；② **世界观跟随主角视角逐步展开**——`World.world_reveals` 三段式（潜伏/铺垫/揭示），未揭示真相进强约束区禁剧透，铺垫期只放 hint；③ **多Agent协作分层**——StageGate 阶段门禁（设定缺失自动重生成）、LogicValidator 五类专项校验、Theme/WorldRules/Pacing 专项 Agent；④ **金手指机制卡固化**——`mechanism_rules` 八要素进强约束区永不裁剪，`goldfinger_rules` 五要素；⑤ **防多稿拼接**——事前输出纪律注入 + 事后确定性自检（短行重复/n-gram）+ 单稿抢救；⑥ **章内自洽账本**（`chapter_state.py`）——面板数字/人物生死/物品归属/时间锚点/身体状态跨场景承接，LLM 抽取+面板正则兜底；⑦ **修复重生成世界观清空**——删 world.json 代替写空壳；⑧ **事前设计卡**（本次新增）——`World` 增 enemies/locations/mechanism_baseline/clock/factions/style_taboos 六张结构化卡，世界观阶段生成、按相关性注入，从源头防怪物形态/空间/数字/时间矛盾，前端「人物/世界观」tab 可视化增删改；⑨ **批量生成进度条**——ToolsPanel 轮询 status 展示进度与终止原因。回归：331 pytest 全绿、前端 build 主 JS 1.1MB。部署：git archive master → workbench upload → python3 -m zipfile -e 解压 → docker compose up -d --build；验证：容器 mysql-mozao healthy / novel-gen started、/api/health 200、主 JS 1.1MB 无白屏。
- 部署记录（2026-09-28）：第九次发版（合并 devtest → master，5f532c5）。内容：**修复退款/质量报表接口 500**——`GET /api/me/refundable`、`POST /api/me/refund-request`、`GET /api/admin/quality-report` 的参数化 SQL 中 LIKE 字面量 `%` 未转义（pymysql 误当作参数占位符，抛 `TypeError: not enough arguments for format string`）；三处 SQL 转义为 `%%`（`webapp/db/projects.py` refundable_amount / create_refund_request、`webapp/db/traces.py` quality_report churn 查询）。新增 `tests/test_refund.py`（P0）+ `test_quality_report.py` 2 个 P2 回归用例（模拟 pymysql mogrify 验证转义与语义不变）。回归：全量 190 pytest 全绿、真实 MySQL 冒烟通过。
- 部署记录（2026-09-28）：第八次发版（合并 devtest → master，9a7d417）。内容：**生成质量升级 P0/P1/P2 全量 + 用户工作台 + 导航收敛**。① P0——前文直读（逐章注入前文关键内容）/ 人物动态状态（character_state 跨章一致性）/ 全书质量汇总（review.json + 工具面板改稿建议含终审块）；② P1——行动表先行 / 场景级下沉 / Lorebook 裁剪 / AI 痕迹评分（FEATURE_FLAGS 注册表集中管理开关）；③ P2——动态模块随机化（seed 可复现）/ 风格范文锚定（reference_text 迁移透传+快照）/ 全书终审（final_review，高质量模式默认开）/ 质量指标报表（/api/admin/quality-report，站长后台新增质量报表 tab，8 指标卡+得分分布+问题 TopN+弃书风险+阶段成本）；④ **用户工作台** `/workspace`（6 tab：概览/账户账单/推广返佣/模型配置/我的技能/风格角色，复用 /api/me/* 后端零改动），并修复 RechargeDialog 退款路径 404（/api/refund/request → /api/me/refund-request）；⑤ **导航收敛**——工具面板移除「我的Skills/Skill市场/风格角色」统一入口工作台（剩 8 张项目级工具卡），顶栏移除余额/充值/模型配置与下拉工作台项（收敛工作台）；⑥ skills_library「AI小说防同质化规则」改版为「去AI味·套路改编规则」（套路黑名单→改编红线）。回归：全量 186 pytest 全绿、npm build 通过 dist hash 一致。
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

*最后更新：2026-10-16*
