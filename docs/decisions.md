# 决策记录（docs/decisions.md）

> 目的：沉淀历史技术决策与踩坑记录（"为什么这么设计"）。**AI 助手遇到与本文冲突的"优化"想法时，先读本文确认是否已评估过，不要推翻已有设计**。
> 维护规则：每次代码合并到 master 后，把新决策/新坑追加到本文（含日期与 commit），与 README 更新记录同步。

## 一、架构级决策

| 日期 | 决策 | 原因 / 影响 |
|---|---|---|
| 2026-09-20 | `webapp/server.py`（2853 行单文件）拆分为 `core.py`（核心层）+ `routers/`（9 域 90 路由）+ `server.py`（组装壳） | 最大架构债；拆分后 openapi 路由集合 **96=96 逐字节一致**、29 pytest 全绿、端到端冒烟通过才合并。**约束：新路由按域进 routers，禁止回写 core.py/server.py 堆逻辑** |
| 2026-09-20 | `routers/projects.py`（1150 行/43 路由）再拆 5 个子模块（base/outline/chapters/world/generation） | 路径/方法/返回体不变，路由集合回归一致后合并 |
| 2026-09-20 | 结算重构：`_compute_settle` 统一口径（增量/整批共用），增量结算**先落盘结算基线再扣费** | P0 资金安全：消除"扣费后崩溃重启重复扣费"窗口。**宁可漏扣、不重扣**。改结算必须跑 `tests/test_settle.py` |
| 2026-09-20 | 生成任务持久化：线程启动落 `task.json`、结束清理，lifespan 启动自动重拾中断任务并断点续跑 | 服务重启不丢生成/大纲/批量任务 |
| 2026-09-20 | BYOK Key 加密落库：`secret_encrypt.py`（Fernet，密文前缀 `enc:v1:`）；存量明文启动自动迁移；`.llm_encrypt.key` 不入库 | DB 泄露不暴露用户自配 Key。附 P0 修复：`get_user_llm` 键名与调用方契约不符导致 BYOK 自配 key 从未生效 |
| 2026-09-20 | 项目列表页 `meta.json` 轻量统计缓存，章节未变化时零文件内容读取 | 列表页性能 |
| 2026-09-20 | `tests/` 取消 gitignore、测试入库；关键路径（结算/权限/加密/路由拆分）发版前必须本地跑通 | 回归保障；当前 44 用例全绿 |
| 长期 | 产物文件（`projects/<id>/` JSON）是项目状态**唯一权威来源**，DB 只存归属/订单 | 断点续传幂等、不重复消耗 token |
| 长期 | 计费：创建不扣费，生成完成按 AI 实际生成字数结算；目标字数封顶；生成中余额 ≤ 0 自动停止 | 商业模式既定，改动需谨慎 |
| 长期 | 前端多阶段 Docker 构建、dist 一律不入库 | 见 AGENTS.md §6；防 hash 漂移/白屏 |
| 2026-09-21 | 生成正文提示词约束口头禅：`writer.md` 限同句口头禅每章 ≤2 次、禁止章末复现；`architect.md` 禁止把口头禅写进 `core_event`/`transition` 作为"每章复现"指令 | 实测《我的地球Online日记》45 万字中"先记下来"出现 150 次（≈3.3 次/万字），口头禅从识别标签退化为复读机；`repetition.py`（>5 次/万字才禁）拦不住刻意设计的短口头禅，需在提示词层约束 |
| 2026-09-27 | Agent 生产级加固阶段1：全链路 trace（`agent_trace` 表 + LLM 层模板方法自动埋点）、成本防护（AgentGuard 循环/Token 阈值）、输入注入检测（API 400 + 拼 system 前清洗）、输出合规校验、章级 QualityChecker 自校验（1 次自动重写） | 解决「执行不可回溯/成本不可控/输入无防护/输出无校验」；mock provider 不挂 guard 保持离线测试一致；各能力独立降级开关；新增 4 测试文件 38 用例，全量 118 pytest 全绿 + mock 冒烟 + MySQL 落库验证通过。**约束：新能力默认开启，异常用 `agent_guard.*` 开关关闭，勿改回硬编码** |
| 2026-09-27 | `webapp/db.py`（1409 行单文件）拆分为 `webapp/db/` 包：`base`（连接池/表结构/init_db）+ `users`（用户/返佣/认证/余额）+ `projects`（归属/退款）+ `skills`（技能/市场）+ `style_roles` + `teardowns`（拆书库）；`__init__.py` re-export 全部符号（含 `_connect`/`_valid_email`/`DB_*`） | 数据层按业务域收敛；调用方 `from webapp import db` 零改动（routers/core/admin_cli/teardown_lib/tests 全部兼容）；**回归：80 pytest 全绿 + app 加载 108 路由 + 15 业务模块导入通过才合入**。约束：新增数据函数放对应域子模块，禁止回写 base.py |
| 2026-09-29 | 章级防「多稿拼接」加固（事前+事后双保险）：① 事前：`_output_discipline()` 注入 5 个正文输出提示词（scene/chapter/polish/revise_apply/expand），禁止多版本并列/元文字开头/中途截断；② 事后：QualityChecker 新增确定性 `_self_dup_issues`（双信号：跨大跨度重复的短收尾行 8~16字 ≥3次 + n-gram 覆盖 >25%）与 `_ending_issues`（末字非句末标点视为截断）；③ 自愈：`ensure_quality` 检出拼接先走 `_salvage_single_draft` 确定性单稿抢救（切候选段取最长且句号收尾），不依赖 LLM 重写 | 根因：管线把 LLM 原始输出当最终正文，仅化妆式清理；质量关卡重复检查只比对前几章、字数检查只查过短，故四稿拼接+截断被判满分（review.json score:100）。实测故障第1章（四稿拼接+截断，5697字/面板行6次）现正确检出并抢救为 1682 字单稿；正常章无误报（第2章 40 字长行重复 2 次不算拼接，n-gram 覆盖仅 1.7%）。**关键校准**：分界标记限短行 8~16 字，长行重复多为正常叙事复用，不能当拼接。新增 test_output_discipline.py + test_quality.py 新增 3 用例，全量 227 全绿 |
| 2026-09-29 | C 类「内容/写作级缺陷」设定层+提示词源头修复（金手指规则/细节一致性/开篇锚点/母题密度）：① `World` 新增 `mechanism_rules`（机制卡：语义无歧义/结算规则/代价递增与终局/首章兑现标题，to_dict/from_dict 兼容 dict/list）；② `world_prompt` 与 `world_from_content_prompt` 均要求产出 `mechanism_rules`；③ `Memory.build_context` 把机制卡注入强约束区（`[L1·核心锁定]`，prompt 末尾最高权重、永不裁剪）；④ 新增 `_writing_constraints()` 注入 chapter/scene 提示词（人物一致性：同一物理细节全文只一种口径、禁止左右手横跳；开篇锚点前置：首章 500 字内建立共情锚点、温度内容不得沉底；母题密度：同一动作/短句/意象单章 ≤2 次防口癖） | 批注根因（与拼接不同根）：金手指规则未定义/历史死亡史矛盾/标题未兑现/蚀痕无作用、左右手九处横跳、钩子太晚情绪落点缺失、母题复读密度过高——均为内容自洽与写作质量问题，A/B 类防拼接管线治不了，需在设定层固化 + 提示词源头约束而非仅事后校验。机制卡入强约束区保证逐章注入且不裁剪，写作约束随整章/场景正文生成注入。新增 test_writing_constraints.py 7 用例，全量 234 全绿 |
| 2026-09-29 | 分卷-章节关联完整修复：`OutlineGenerator.assign_volumes` 按 `volumes.start_ch~end_ch` 区间确定性回填 `volume_no`（重叠取较小卷/区间外就近归卷/手动 `!=0` 不覆盖）；`OutlineSaveReq` 增 `volumes`、`OutlineChapterReq` 增 `volume_no`，`save_outline` 构造 `Outline` 时完整保留分卷与卷号落盘 | 根因：生成侧 `_normalize`/分批未回填卷号 + 保存链路 Pydantic 丢弃 `volume_no`/`volumes`，导致"分卷下无对应章节、前端拖拽改卷号保存后被还原"。前端拖拽 UI 本已存在，只需修生成与保存两段后端。新增 tests/test_volume_link.py 8 用例（P0 拖拽持久化/P1 回填与保存/P2 旧数据兼容），全量 241 全绿 |
| 2026-09-29 | 世界观跟随主角视角逐步展开：`World` 新增 `world_reveals`（阶段揭示计划：`from_ch`=主角得知起始章、`content`=主角视角片段，to_dict/from_dict 兼容旧数据缺字段回退全量注入）；`world_prompt`/`world_from_content_prompt` 均要求产出 `world_reveals`（禁止一次写全）；`Memory.build_context` 逐章分层注入——已揭示进普通区、未揭示进强约束区「禁止提前透露」、无 reveals 回退原全量注入；`writer.md` 加「视角与世界观」约束（只写主角已知、未揭示禁剧透） | 根因：原每次把完整 `world_view` 全量注入强约束，写手从第 1 章全知所有设定，正文无法"跟随主角逐步发现世界"（architect.md 的"写到哪铺到哪"未落地）。未揭示设定进强约束区保证不裁剪、不剧透。新增 tests/test_world_reveal.py 7 用例（P0 分层注入/P1 序列化与提示词/P2 兼容），全量 241 全绿 |
| 2026-09-29 | 世界观揭示加「铺垫链」深化（避免"到点凭空解锁"）：`WorldReveal` 增 `foreshadow_ch`（铺垫最早章）与 `hint`（铺垫线索）；`Memory.build_context` 三段式注入——潜伏期（N<foreshadow_ch 完全不注入）→ 铺垫期（fs≤N<from_ch：hint 进普通区可写暗示、真相进强约束禁剧透）→ 揭示期（N≥from_ch：真相进普通区）；旧数据（无 foreshadow_ch）未揭示真相仍进强约束禁剧透（保持原行为） | 根因：原实现"到了 from_ch 章就凭空解锁"，缺铺垫-伏笔因果链，读者觉得突兀。现要求真相必须有前文线索层层铺垫、由具体事件触发引出并收束暗示，禁止到点摊牌。`world_prompt`/`world_from_content_prompt` 均要求产出 foreshadow_ch（必须 < from_ch，提前 2-3 章）+ hint。新增 6 用例（P0 潜伏/铺垫/揭示三段注入、P1 序列化与提示词、P2 兼容），全量 255 全绿 |
| 2026-09-29 | 多Agent协作分层优化（REQ-多Agent协作分层优化.md）：① 编排层 `Orchestrator`（阶段门禁 StageGate：产出→准出校验→不合格自动重生成≤N轮→超限仲裁放行，主题>人物>世界观），`run_world/run_outline` 接入（mock provider 默认跳过门禁）；② 校验层 `LogicValidator` + `issue_triage`（5 类专项校验：设定一致性/OOC/主线偏移/重复≤5%/伏笔完整性，问题分级 fatal/major/minor）；③ 生产层细分：ThemeCharacter/WorldRules/Pacing 专项 Agent + prompts，`Architect` 收敛为骨架生产者并编排三者；④ 数据结构：`World` 增 theme_proposition/conflict_matrix/protagonist_arc/character_motives/goldfinger_rules(五要素)/setting_taboos，`foreshadowing` 升级 `ForeshadowItem`(buried_ch/reveal_ch/level)，`Outline` 增 key_nodes/volume_pacing，全部 from_dict 兼容旧数据；⑤ 成果整合 `report.py` 落盘 4 份设计文档到 `projects/<id>/design/`；⑥ 新开关 `stage_gate`/`stage_gate_retries` | 把设计文档「总控+生产+校验」分层落地为可执行机制，解决设定吃书/主线跑偏/伏笔只埋不收/节奏失控。mock provider 跳过门禁保持离线行为一致。新增 27 用例（P1 stage_gate/validator/schemas + P2 report），全量 282 全绿。**约束：金手指五要素/伏笔回收章由 StageGate 强校验，缺失自动重生成** |
| 2026-09-29 | 技能统一注入管线（REQ-技能统一.md，阶段一）：① `writing_skills`/`user_skills`/`skill_market` 加 `applicable_stages`（适用阶段，空=全量）+`writing_skills.version`，幂等迁移（仿既有 _column_names+ALTER 模式）；② 新增 `novel_gen/pipeline/skills.py` SkillAssembler：统一采集 平台全局/平台角色/用户技能/项目快照，按角色默认阶段路由（STAGE_BY_ROLE），统一清洗，产出 manifest；③ `agents/base.py` 收敛 `_role_skill_append`/`_system_with_role`/`persona_prefix` 接入管线，注入清单经 `record_agent_step("skill_injection")` 落 agent_trace（有 recorder 才记、异常吞掉）；④ `templates._skill_append` 复用 `collect_project_blocks`；⑤ 市场安装 `install_market_skill` 透传 applicable_stages | 解决 Skill 散落 4 表+2 快照+提示词文件、注入来源双轨（实时 vs 快照）、仅靠 role 字符串硬匹配的弱联动。**兼容性关键**：阶段过滤在组装层（Python 侧）做，**不新增 DB 查询参数**（避免破坏 test_agents 对 db.list_skills 的 mock 签名）；存量技能 applicable_stages='' → 全量注入，输出与改造前逐字节一致。新增 test_skill_assembler.py 14 用例（P1 聚合/阶段过滤/manifest 落 trace + P2 匹配规则/兼容），全量 296 全绿。**阶段二（另立 REQ）**：4 表物理合并、风格角色预设化、skills_library 同步、管理 UI 收敛 |
| 2026-09-29 | 技能统一注入管线（阶段二，存储层收敛，REQ §9）：① 统一表 `skills`（source∈{platform,user,market}+owner_id 区分权限/信任，含 role/applicable_stages/version/downloads/market_id），`base.py` 新增 DDL+幂等迁移，**旧 4 表保留不 DROP**（回退）；② `deploy/migrate_skills_merge.py` 幂等迁移（skills 非空即跳过）：writing_skills→platform、user_skills→user、skill_market→market，回填 user 行 market_id（旧市场id→新id），重映射 style_role_skills.skill_id（admin→platform/user→user 新 id）；③ `db/skills.py` 重写为统一读写 `skills`（函数签名与返回字段不变：platform/user 行 `owner_id AS user_id`、market 行 `owner_id AS author_id` → 前端 API 零改动）；④ `db/style_roles.py` 挂载 JOIN/校验改经 `skills`（skill_type admin=platform/user=user）；⑤ `deploy/sync_skills_library.py` 把 writing-techniques 蒸馏为平台技能（按名幂等 upsert、文件名关键词映射角色）；⑥ 注入管线 `collect_role_blocks` 零改动（db 函数签名不变） | 根治"同一能力散落 4 表+市场外链"。**关键**：迁移保留旧表可回退；返回字段别名保住前端 `r.user_id`（我的/平台）判断与市场 `author_id`；注入逻辑/输出逐字节不变。新增 test_skill_assembler.py 统一市场安装+迁移幂等 DB 用例，全量 297 全绿。**遗留**：管理 UI 收敛（REQ 9.7）API 已统一、前端零改动，UI 结构无大改需求，视需要单独排期 |

## 二、踩坑记录（避坑清单）

| 日期 | 坑 | 结论 |
|---|---|---|
| 2026-09-20 | docker-compose 漏写 `novel_projects:/app/projects` 命名卷（服务器容器实际已挂载），`docker compose up` 重建容器后项目目录消失 | compose 中挂载声明必须与运行期一致；改 compose 后核对全部卷 |
| 持续 | `httpx` 升级到 0.28 会导致 LLM 客户端创建失败 | `requirements.txt` 锁定 `httpx<0.28`，勿随意升 |
| 2026-09-20 | 服务器直连 GitHub 超时；服务器无 `unzip` | 发版用本地 `git archive master` 打包经 workbench 上传；服务器端用 `python3 -m zipfile -e` 解压 |
| 2026-09-17 | 扩写模式（mode=expand）重新生成大纲后分卷为空 | 扩写提示词 `expand_outline_prompt` 必须输出 `core_conflict/main_line/volumes`（每卷 no/name/intro/main_conflict/character_growth/climax/start_ch/end_ch）；`regen_outline` 先删 outline.json 再起线程（防止"已存在则复用"跳过） |
| 持续 | 前端白屏 / `Failed to load module script ... MIME type text/html` | = index.html 引用的 assets JS 与实际文件不匹配（git checkout 还原旧 index.html / 构建后未同步）。处理：`npm run build` → 核对引用一致 → Ctrl+F5 强刷。**dist 不入库，禁止 `git add -f`** |
| 持续 | 改了后端代码但行为没变 | 本地旧 `python -m webapp.server` 进程不加载新代码；必须重启进程 |
| 持续 | 服务器 `docker compose up -d --build` 白屏 | 先 `curl` 主 JS 看 size：几百 KB 正常，几百字节说明 dist 未正确打进镜像 |
| 持续 | 改 `requirements.txt` 或前端依赖/源码 | 必须 rebuild 镜像（`--build`），不能只 restart |
| 2026-09-29 | `World.foreshadowing` 升级为 `list[ForeshadowItem]` 后，直接 `World(foreshadowing=["..."])` 构造（不经 from_dict）的旧代码/测试在 `to_dict` 崩溃（str 无 to_dict） | `World.to_dict` 的列表字段必须兼容 str/dict/对象三种元素（统一 `_list_to_dicts`）；数据类内部统一为 ForeshadowItem，from_dict/to_dict 双兼容 |
| 2026-09-28 | pymysql 参数化 SQL 中 LIKE 字面量 `%` 未转义（如 `LIKE '管理员手动充值%'`），被误当作参数占位符，抛 `TypeError: not enough arguments for format string`，导致退款/质量报表接口 500（master 5f532c5 修复） | 字面量 `%` 必须写 `%%`（mogrify 时还原为单 `%`）：`projects.py` refundable_amount/create_refund_request、`traces.py` quality_report churn 查询 3 处已修；新增 test_refund.py（P0）+ quality_report 2 个 P2 回归（模拟 mogrify 验证），全量 190 全绿。**同类隐患**：`users.py` admin_stats、`base.py` BYOK 迁移的 LIKE 目前无参数执行（pymysql 不传参不格式化 `%`）安全，将来加参数前必须转义 |
| 2026-09-28 | 大纲/世界观生成 JSON 被 max_tokens=4096 截断导致 `Unterminated string` 解析失败 | 根因：`chat_json` 调用未传 max_tokens，走 client 默认 4096，但完整大纲 JSON（总纲+分卷+每章 8 字段）轻松超限，重试 3 次每次都在同一 token 上限处截断。修复：① 大纲/世界观/扩写/续写共 7 处 `chat_json` 调用显式传 `max_tokens=8192`；② `chat_json` 重试逻辑增加截断检测（`_is_json_truncation`：pos>0 且错误消息含 Unterminated/Expecting 等信号），自动 +4096 增大 token 上限（封顶 16384）并重试；③ pos==0 的自然语言开头错误不增大 token（属格式问题非截断）。新增 test_chat_json_truncation.py 15 用例，全量 205 全绿 |

## 三、文档漂移记录

- **2026-09-21**：修复 `README.md` 目录结构中的过期条目——`novel_gen/cli.py`、`__main__.py` 实际不存在（当前产品入口为 Web 端 `run_web.py`），已删除该行；README 中「快速开始（命令行）/ 命令一览」两节仍描述 `python -m novel_gen ...` CLI 用法，**与现状不符，待确认是否清理**。
- **2026-09-21**：`.gitignore` 现状——`docs/`（需求分析文档）与 `AGENTS.md`（注释为 "AI agent rules (local only)"）均不入库。`docs/architecture.md` / `filemap.md` / `decisions.md` 三件套默认同受 `docs/` 忽略；**若要跨机器复用 AI 知识库，需在 .gitignore 中为其加白名单（如 `!docs/architecture.md` 等），由用户拍板，勿擅自改动**。

## 四、新增决策的写法

追加时按以下模板，保持一行一条、可回溯：

```
| YYYY-MM-DD | <决策一句话> | <原因/影响，含回归验证方式> |
```
