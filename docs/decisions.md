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
| 2026-09-28 | 封面 AI 模型由硅基流动 `baidu/ERNIE-Image-Turbo` 更换为 `Tongyi-MAI/Z-Image-Turbo`（代码默认值 + 本地 .env COVER_MODEL 同步更新；生产可经 COVER_MODEL 环境变量覆盖） | 硅基流动官方通知 ERNIE-Image-Turbo 2026-09-30 下线；Z-Image-Turbo 兼容现有 payload（image_size=768x1024 / seed / output_format=png，官方 images API 参数已核实）；回归全量 205 pytest 全绿。merge master 8719855 |
| 2026-09-28 | 扩写大纲（expand 模式）分批生成：先出总纲（title/intro/core_conflict/main_line/volumes），再按 8 章一批生成章节；`expand_outline_prompt` 增加 batch 参数（None=总纲 / (start,end,prev_summary)=章节批），跨批传前情摘要保持连贯 | 承接 6326ac5 截断修复：即使显式 max_tokens=8192，全书大纲 JSON（几十章 × 8 字段）仍可能超限；分批把单次输出降到安全长度，避免反复重试截断。回归全量 205 pytest 全绿。merge master 8719855 |
| 2026-09-29 | 章级防「多稿拼接」加固（事前+事后双保险）：① 事前：`_output_discipline()` 注入 5 个正文输出提示词（scene/chapter/polish/revise_apply/expand），禁止多版本并列/元文字开头/中途截断；② 事后：QualityChecker 新增确定性 `_self_dup_issues`（双信号：跨大跨度重复的短收尾行 8~16字 ≥3次 + n-gram 覆盖 >25%）与 `_ending_issues`（末字非句末标点视为截断）；③ 自愈：`ensure_quality` 检出拼接先走 `_salvage_single_draft` 确定性单稿抢救（切候选段取最长且句号收尾），不依赖 LLM 重写 | 根因：管线把 LLM 原始输出当最终正文，仅化妆式清理；质量关卡重复检查只比对前几章、字数检查只查过短，故四稿拼接+截断被判满分（review.json score:100）。实测故障第1章（四稿拼接+截断，5697字/面板行6次）现正确检出并抢救为 1682 字单稿；正常章无误报（第2章 40 字长行重复 2 次不算拼接，n-gram 覆盖仅 1.7%）。**关键校准**：分界标记限短行 8~16 字，长行重复多为正常叙事复用，不能当拼接。新增 test_output_discipline.py + test_quality.py 新增 3 用例，全量 227 全绿 |
| 2026-09-29 | C 类「内容/写作级缺陷」设定层+提示词源头修复（金手指规则/细节一致性/开篇锚点/母题密度）：① `World` 新增 `mechanism_rules`（机制卡：语义无歧义/结算规则/代价递增与终局/首章兑现标题，to_dict/from_dict 兼容 dict/list）；② `world_prompt` 与 `world_from_content_prompt` 均要求产出 `mechanism_rules`；③ `Memory.build_context` 把机制卡注入强约束区（`[L1·核心锁定]`，prompt 末尾最高权重、永不裁剪）；④ 新增 `_writing_constraints()` 注入 chapter/scene 提示词（人物一致性：同一物理细节全文只一种口径、禁止左右手横跳；开篇锚点前置：首章 500 字内建立共情锚点、温度内容不得沉底；母题密度：同一动作/短句/意象单章 ≤2 次防口癖） | 批注根因（与拼接不同根）：金手指规则未定义/历史死亡史矛盾/标题未兑现/蚀痕无作用、左右手九处横跳、钩子太晚情绪落点缺失、母题复读密度过高——均为内容自洽与写作质量问题，A/B 类防拼接管线治不了，需在设定层固化 + 提示词源头约束而非仅事后校验。机制卡入强约束区保证逐章注入且不裁剪，写作约束随整章/场景正文生成注入。新增 test_writing_constraints.py 7 用例，全量 234 全绿 |
| 2026-09-29 | 分卷-章节关联完整修复：`OutlineGenerator.assign_volumes` 按 `volumes.start_ch~end_ch` 区间确定性回填 `volume_no`（重叠取较小卷/区间外就近归卷/手动 `!=0` 不覆盖）；`OutlineSaveReq` 增 `volumes`、`OutlineChapterReq` 增 `volume_no`，`save_outline` 构造 `Outline` 时完整保留分卷与卷号落盘 | 根因：生成侧 `_normalize`/分批未回填卷号 + 保存链路 Pydantic 丢弃 `volume_no`/`volumes`，导致"分卷下无对应章节、前端拖拽改卷号保存后被还原"。前端拖拽 UI 本已存在，只需修生成与保存两段后端。新增 tests/test_volume_link.py 8 用例（P0 拖拽持久化/P1 回填与保存/P2 旧数据兼容），全量 241 全绿 |
| 2026-09-29 | 世界观跟随主角视角逐步展开：`World` 新增 `world_reveals`（阶段揭示计划：`from_ch`=主角得知起始章、`content`=主角视角片段，to_dict/from_dict 兼容旧数据缺字段回退全量注入）；`world_prompt`/`world_from_content_prompt` 均要求产出 `world_reveals`（禁止一次写全）；`Memory.build_context` 逐章分层注入——已揭示进普通区、未揭示进强约束区「禁止提前透露」、无 reveals 回退原全量注入；`writer.md` 加「视角与世界观」约束（只写主角已知、未揭示禁剧透） | 根因：原每次把完整 `world_view` 全量注入强约束，写手从第 1 章全知所有设定，正文无法"跟随主角逐步发现世界"（architect.md 的"写到哪铺到哪"未落地）。未揭示设定进强约束区保证不裁剪、不剧透。新增 tests/test_world_reveal.py 7 用例（P0 分层注入/P1 序列化与提示词/P2 兼容），全量 241 全绿 |
| 2026-09-29 | 世界观揭示加「铺垫链」深化（避免"到点凭空解锁"）：`WorldReveal` 增 `foreshadow_ch`（铺垫最早章）与 `hint`（铺垫线索）；`Memory.build_context` 三段式注入——潜伏期（N<foreshadow_ch 完全不注入）→ 铺垫期（fs≤N<from_ch：hint 进普通区可写暗示、真相进强约束禁剧透）→ 揭示期（N≥from_ch：真相进普通区）；旧数据（无 foreshadow_ch）未揭示真相仍进强约束禁剧透（保持原行为） | 根因：原实现"到了 from_ch 章就凭空解锁"，缺铺垫-伏笔因果链，读者觉得突兀。现要求真相必须有前文线索层层铺垫、由具体事件触发引出并收束暗示，禁止到点摊牌。`world_prompt`/`world_from_content_prompt` 均要求产出 foreshadow_ch（必须 < from_ch，提前 2-3 章）+ hint。新增 6 用例（P0 潜伏/铺垫/揭示三段注入、P1 序列化与提示词、P2 兼容），全量 255 全绿 |
| 2026-09-29 | 多Agent协作分层优化（REQ-多Agent协作分层优化.md）：① 编排层 `Orchestrator`（阶段门禁 StageGate：产出→准出校验→不合格自动重生成≤N轮→超限仲裁放行，主题>人物>世界观），`run_world/run_outline` 接入（mock provider 默认跳过门禁）；② 校验层 `LogicValidator` + `issue_triage`（5 类专项校验：设定一致性/OOC/主线偏移/重复≤5%/伏笔完整性，问题分级 fatal/major/minor）；③ 生产层细分：ThemeCharacter/WorldRules/Pacing 专项 Agent + prompts，`Architect` 收敛为骨架生产者并编排三者；④ 数据结构：`World` 增 theme_proposition/conflict_matrix/protagonist_arc/character_motives/goldfinger_rules(五要素)/setting_taboos，`foreshadowing` 升级 `ForeshadowItem`(buried_ch/reveal_ch/level)，`Outline` 增 key_nodes/volume_pacing，全部 from_dict 兼容旧数据；⑤ 成果整合 `report.py` 落盘 4 份设计文档到 `projects/<id>/design/`；⑥ 新开关 `stage_gate`/`stage_gate_retries` | 把设计文档「总控+生产+校验」分层落地为可执行机制，解决设定吃书/主线跑偏/伏笔只埋不收/节奏失控。mock provider 跳过门禁保持离线行为一致。新增 27 用例（P1 stage_gate/validator/schemas + P2 report），全量 282 全绿。**约束：金手指五要素/伏笔回收章由 StageGate 强校验，缺失自动重生成** |
| 2026-09-29 | 技能统一注入管线（REQ-技能统一.md，阶段一）：① `writing_skills`/`user_skills`/`skill_market` 加 `applicable_stages`（适用阶段，空=全量）+`writing_skills.version`，幂等迁移（仿既有 _column_names+ALTER 模式）；② 新增 `novel_gen/pipeline/skills.py` SkillAssembler：统一采集 平台全局/平台角色/用户技能/项目快照，按角色默认阶段路由（STAGE_BY_ROLE），统一清洗，产出 manifest；③ `agents/base.py` 收敛 `_role_skill_append`/`_system_with_role`/`persona_prefix` 接入管线，注入清单经 `record_agent_step("skill_injection")` 落 agent_trace（有 recorder 才记、异常吞掉）；④ `templates._skill_append` 复用 `collect_project_blocks`；⑤ 市场安装 `install_market_skill` 透传 applicable_stages | 解决 Skill 散落 4 表+2 快照+提示词文件、注入来源双轨（实时 vs 快照）、仅靠 role 字符串硬匹配的弱联动。**兼容性关键**：阶段过滤在组装层（Python 侧）做，**不新增 DB 查询参数**（避免破坏 test_agents 对 db.list_skills 的 mock 签名）；存量技能 applicable_stages='' → 全量注入，输出与改造前逐字节一致。新增 test_skill_assembler.py 14 用例（P1 聚合/阶段过滤/manifest 落 trace + P2 匹配规则/兼容），全量 296 全绿。**阶段二（另立 REQ）**：4 表物理合并、风格角色预设化、skills_library 同步、管理 UI 收敛 |
| 2026-09-29 | 技能统一注入管线（阶段二，存储层收敛，REQ §9）：① 统一表 `skills`（source∈{platform,user,market}+owner_id 区分权限/信任，含 role/applicable_stages/version/downloads/market_id），`base.py` 新增 DDL+幂等迁移，**旧 4 表保留不 DROP**（回退）；② `deploy/migrate_skills_merge.py` 幂等迁移（skills 非空即跳过）：writing_skills→platform、user_skills→user、skill_market→market，回填 user 行 market_id（旧市场id→新id），重映射 style_role_skills.skill_id（admin→platform/user→user 新 id）；③ `db/skills.py` 重写为统一读写 `skills`（函数签名与返回字段不变：platform/user 行 `owner_id AS user_id`、market 行 `owner_id AS author_id` → 前端 API 零改动）；④ `db/style_roles.py` 挂载 JOIN/校验改经 `skills`（skill_type admin=platform/user=user）；⑤ `deploy/sync_skills_library.py` 把 writing-techniques 蒸馏为平台技能（按名幂等 upsert、文件名关键词映射角色）；⑥ 注入管线 `collect_role_blocks` 零改动（db 函数签名不变） | 根治"同一能力散落 4 表+市场外链"。**关键**：迁移保留旧表可回退；返回字段别名保住前端 `r.user_id`（我的/平台）判断与市场 `author_id`；注入逻辑/输出逐字节不变。新增 test_skill_assembler.py 统一市场安装+迁移幂等 DB 用例，全量 297 全绿。**遗留**：管理 UI 收敛（REQ 9.7）API 已统一、前端零改动，UI 结构无大改需求，视需要单独排期 |
| 2026-09-29 | 章内自洽与机制账本（REQ-章内自洽与机制账本.md）：① 新增 `chapter_state.py`——章末/场景状态抽取（机制数字/人物生死/物品归属/时间锚点/已发生事实，LLM 主抽取 + 确定性面板正则兜底 + 失败降级），store 落 `chapter_state.json`，manager 写章循环随 character_state 一起更新（开关 `chapter_state_tracking`/`chapter_state_llm`）；② `memory.build_context` 新增 L1.6「世界状态·承接上一章」（账本章号<当前章才注入），并把 `character_motives` 动机卡注入出场人物区；③ `stages._generate_chapter_scenes` 逐场景累积「本章已推进状态」注入后续场景提示词；④ 提示词：`split_scenes_prompt` 要求 time_marker/关键事件等权重/时间顺延，`scene_prompt` 注入状态承接+时间过渡+视角+内心底线 4 条硬约束，`_writing_constraints` 增时间轴/单口径/内心底线/悬念句 4 规则，`world_prompt`×2 机制卡增数字账本/时间词语义/多数字关系/历史衔接；⑤ 校验：`validator._check_mechanism` 机制卡三要素（语义/结算/代价终局）缺失→fatal，`quality._repeat_sentence_issues` 章内同句复读确定性检测（叙述≥3次/长句2次/面板行2次→大改触发重写，对话口头禅≤2次豁免） | 根因（《万墟之上》第1章 32 条批注）：模型在「场景级独立生成+静态记忆注入」结构下维护不了章内可变状态——面板数字 3→2→1→1（第3次死亡漏减）、疤脸死而复生、押金责任方前后矛盾、"找到你了"四处口径冲突、整句复读 9 处、情绪零外显。数字/生死/物品/时间是叙事内可变状态，此前既不喂回也不校验，属 C 类更深一层。新增 test_chapter_state.py 15 用例 + test_quality/test_validator/test_writing_constraints 扩展，全量 329 全绿。**约束：状态账本为弱约束提示（以直读正文为准），LLM 抽取失败静默降级；确定性面板正则优先于 LLM 数字** |
| 2026-09-29 | 事前设计卡（REQ-事前设定卡.md）：World 新增 enemies/locations/mechanism_baseline/clock/factions/style_taboos 六个结构化字段；world_prompt 要求世界观阶段产出；memory 按相关性注入（怪物/地点/势力普通区，机制初始值/时钟/语言禁忌强约束区）；validator 加缺卡门禁；前端世界观 tab 可视化增删改 | 把事后矛盾检查前置为事前设计——模型不再自行发明怪物形态/空间/数字，从源头防"灰卒无固定形状却有利齿""地下出现暮色""四数字对不上"。331 测试绿 |
| 2026-09-30 | 开发流程引入角色职责分工（AGENTS.md §9 重构为 需求/方案设计/开发/测试/发版交付 五阶段，主责角色依次为 产品经理/架构师/工程师/QA工程师/交付，每阶段设必交产出与准出关口；新增方案设计阶段，补齐架构评审与决策记录） | 小团队一人可兼任多角色，但每阶段必须有明确主责与关口，避免"需求不清直接写码 / 无方案硬开发 / 测试后置 / 发版无线上验证"。LLM 按任务所处阶段以对应角色执行。文档流程变更，无 pytest 影响 |
| 2026-09-30 | Token 消耗优化（REQ-token优化.md，P0+P1 全落地，用户拍板 6 项全按推荐）：① 前置解决优先——新增 `setting_cards.py` 纯确定性分章设定卡索引（world 后构建 `setting_cards.json`，memory 按章只注入出场人物/相关生物·地点·势力·伏笔子集；**空列表=命中但本章无项→不注入，None=索引缺失/关闭→回退全量**；匹配=名称直判（目标词含名称即命中）+关键词包含双条件）；② 调用裁剪——`scene_state_mode`（auto=普通确定性/高质量 LLM）、`state_extract_frequency`（默认 3，每 3 章 1 次 LLM 状态抽取）、`action_plan_first` 默认 False（行动表并入拆场景输出 `characters_action`，`action_plan_first=True` 保留原独立调用）、`polish_hq_only` 默认 True（普通模式关整章润色）、`consistency_llm_check` 默认 False（章内矛盾自检仅高质量模式）、`verify_combined`+`verify_every`（默认 5，原每 3 章；3 连校验合并为 1 次调用）、`editor_diff_revise`（diff 式修正只输出需改字段 max_tokens 4096→1200，Python 合并回大纲，解析失败/关闭回退整章重出）、`targeted_rewrite`（定点重写：issue pos/原句锚点定位窗口 ±200 字，窗口覆盖 <60% 正文才局部重写并拼回，无定位/跨度过大回退整章）；③ `memory_direct_read_chars` 默认 2000→1200 | 根因：09-27~09-29 密集上线的质量机制全依赖 LLM 调用，单章调用从改造前 ≈2 次膨胀到 ≈13~16 次（普通模式），约 5 倍；agent_trace 实测（847 条）in 占 73%（chapter 单次输入均值 3890、最大 9.3k）。优化方向：能前置固化（设定卡/确定性脚本）就不反复调 LLM；必须校验则降频合并裁剪输入；必须重写则定点化。**存量项目 config.json 已持久化旧默认值 → 不写迁移（用户拍板）；新增开关默认值对存量项目仅少调用方向生效（安全）**。全量 357 pytest 全绿（新增 26 用例）+ HTTP 端到端冒烟 ALL PASS + mock 整合冒烟（setting_cards 落盘验证）通过 |

| 2026-09-30 | 章节质量防线修复（REQ-章内自洽与机制账本·扩展，P0×3+P1×3+P2×1，用户拍板全做）：① **重写注入设定上下文**（P0-1）——`_rewrite_whole/_rewrite_span` prompt 增加「世界设定/金手指机制卡/机制初始值面板标准答案/人物卡/上一章机制数字」块（`_context_block`，取 world_view/mechanism_rules/mechanism_baseline/characters + chapter_state.numbers，异常降级跳过），根治"真空重写"（《万墟之上》第1章重写后复检仍 5 条的直接原因）；② **机制数字结算确定性校验**（P0-2）——`_panel_progress_issues`：同标签同数值面板行跨段（间距>150字）复现 = 结算未推进，判大改扣 30 分（零 LLM）；③ **复检失败不再静默交付**（P0-3）——`ensure_quality` 自动重写 1 次→最多 `_MAX_REWRITE=3` 轮（通过即停，重写无效提前停）；单章重写路由返回 `quality` 字段（passed/score/issue_count/auto_revised），前端 ProjectDetail 未通过弹警告（不再无声给坏章）；④ **拆场景事件边界互斥**（P1-1）——`split_scenes_prompt` 加事件互斥硬规则，`stages._scene_events_overlap` 用 event 文本相似度（>0.55 且次数标记不同才豁免）检测重叠场景→回退整章；⑤ **面板行不再作草稿分界**（P1-2）——`_draft_markers` 排除面板行（同值复现归"数字结算未推进"专检），`_salvage_single_draft` 抢救保留下限 `_SALVAGE_MIN_KEEP=0.4`（丢>60%拒绝，防误截断）；⑥ **状态抽取去截断**（P1-3）——`_norm_text` 上限 3500→12000（章末/场景 LLM 抽取覆盖全文关键状态），`_consistency_llm_check` 正文输入同步 3500→12000；⑦ **矛盾自检降频覆盖普通模式**（P2）——新开关 `consistency_llm_every`（默认 3，普通模式每 N 章 1 次，高质量模式不受限） | 实证根因链（《万墟之上》第1章单章重写，本地服务跑当前代码）：review.json `score=0, passed=false, auto_revised=true` 检出 5 条大改仍放行落盘——检测能力够（LLM 矛盾自检抓到 4 条硬矛盾），修复能力为零（重写 1 次即放行 + 重写无设定上下文 + 数字无确定性校验 + 账本 3500 截断背书错误 2/3）。**约束：①面板行重复=数字结算问题，不归拼接；②拼接抢救只对非面板行 marker 生效，找不到 marker 不抢救、走重写兜底；③新开关已注册 FEATURE_FLAGS + NovelConfig 字段，存量项目走默认值**。新增/调整 22 用例，全量 379 pytest 全绿；用真实 001.json（5825字）验证：面板 2/3 复现正确报"机制数字未结算推进"、不再误判多稿拼接、全文进入状态抽取 |

| 2026-10-03 | 流水线 Agent 接线补全（P0，改小但解结构问题，用户给出核查结论后执行）：① **激活 ReaderAgent**——`manager.py` 实例化 `ReaderAgent` 为正式角色成员，`_run_verify` 把其 persona（reader.md + reader 专属技能，`verify_combined_prompt` 新增 `reader_persona` 参数）注入读者视角检查，不改变输出 schema、保持单次合并调用（不增 token 调用数）；② **细分 Agent 接线**——`STAGE_BY_ROLE` 补 theme_character/world_rules/pacing 三 key（此前缺失，三 Agent 内部零 LLM 调用、persona 从未被读取即"完全不生效"）、移除 validator 死配置（LogicValidator 非 BaseAgent 不参与技能注入）；`ArchitectAgent` 编排时把 theme+rules persona 注入 WorldGenerator、pacing persona 注入 OutlineGenerator，使细分角色要求（含专属技能）真正进入 LLM 生成阶段，确定性 apply_anchor/plan_* 仍兜底；③ **拆书库回流**——拆书库此前"只沉淀不消费"（`sink_research` 仅在 webapp/core 调用，novel_gen 零引用）；新增 `ProjectStore.teardown_material` 读写 + `webapp/core._inject_teardown_material`（调研前按题材查拆书条目格式化写入 store）+ `ResearchEngine._material_text` 并入调研素材，保持 novel_gen 不依赖 webapp 的分层 | 解决"建了能力却没接线"：ReaderAgent 无独立审校执行点、三个细分 Agent 完全失效、爆款拆书库长期只进不出。**关键事实修正**：ReaderAgent 并非纯死代码（writer.py 已用它注入 persona），缺的是独立审校执行点；三个细分 Agent 连"经 architect 间接生效"都没有（architect 用自己的 persona）。**约束：拆书库回流素材为弱约束（并入调研素材供模型借鉴手法，非强约束），webapp 侧注入失败静默降级不阻塞生成；素材文件走 store 目录（产物权威源，断点续传幂等）**。新增 tests/test_p0_wiring.py 5 用例，全量 411 pytest 全绿（含 24+70 定向回归） |

| 2026-10-03 | 开篇/结局/物品/等级卡与专项 Agent（P1，质量收益最直接，用户确认后推进）：① **开篇 Agent**（`agents/opening.py` + `prompts/opening.md` + `opening_prompt`）——黄金三章此前与普通章节共用 writer 与同一套大纲；现 architect.run_outline 后调用 `OpeningAgent.apply`，为前三章专门设计强开篇（第1章立主角+冲突点燃/第2章冲突升级+主动选择/第3章小闭环+强钩子），回填前三章 core_event/goal（已有注入机制），前 3 章 core_event 非空则幂等复用；② **结局卡 + 结局 Agent**（`schemas.EndingCard` + `agents/ending.py` + `prompts/ending.md` + `ending_prompt`）——全书此前无终局设计，末几章靠 writer 自然收束；`EndingAgent` 基于主题命题/主角弧光/大纲关键节点产出终局设计卡（终局状态/主题落点/主角最终选择/人物归宿/基调），写入 world.ending_card，architect.run_world 两个分支调用（幂等），末几章写手按此收束防烂尾；③ **物品卡 + 等级体系卡**（`schemas.ItemCard/PowerLevel/PowerLevelSystem` + world_prompt/world_from_content_prompt 输出要求 + memory 强约束区注入）——物品卡静态定义（来源/代价/限制/作用）防万能工具，等级体系卡（境界阶梯/数值区间/主角当前境界/突破规则）防长篇后期战力崩坏；两卡均为**可选**（无道具/非升级流文不产出，validator 不做 fatal，防误拦） | 解决"黄金三章决定生死却无专职设计""长篇烂尾/战力崩坏无终局锚点""道具易写成万能工具"三个质量缺口。**关键设计决策**：①开篇/结局 Agent 是「设计锚点」而非「独立正文生成」——开篇回填大纲 core_event、结局写回 ending_card，正文仍由 writer 统一生成（不破坏 token 优化/场景级生成架构），通过 memory 强约束区注入约束写手；②物品/等级卡为弱校验可选卡，只因题材需要才产出/注入，避免误拦无道具/非升级流作品；③三项幂等（core_event 非空/ending_card 非空即复用），LLM 失败静默降级不阻塞生成。**约束：新卡全部 from_dict 兼容旧数据（缺字段回退空）；强约束区新增物品/等级/结局卡，注入体积小、不破坏 token 优化的分章裁剪**。新增 tests/test_p1_cards_agents.py 8 用例，全量 419 pytest 全绿 |

| 2026-10-03 | 对白/情绪曲线/章末钩子/场景落盘/术语统一卡（P2，用户确认继续）：① **对白 Agent**（`agents/dialogue.py` + `prompts/dialogue.md`）——聚合全书人物 speech_style 生成对话风格指南（口吻区分+节奏规则）写回 `world.dialogue_guideline`，architect.run_world 编排，memory 强约束区注入；确定性聚合（零 LLM、不增 token 调用数），保留角色卡供后续叠加 LLM；② **情绪曲线卡**（`schemas.EmotionBeat` + `Outline.emotion_curve`）——pacing.plan_outline 基于各章 emotion/tag 确定性聚合成跨章区间（爽点/虐点/悬念/笑点/平淡），只补缺失幂等，补足"单章 emotion 无全本配比"的缺口；③ **章末钩子**（`ChapterPlan.cliffhanger`）——大纲 prompt（open/expand/continue）chapters 新增 cliffhanger 字段，ChapterWriter 读取当前章钩子注入 memory_context，writer 章末须兑现（防流水账结尾）；④ **场景落盘**（`ProjectStore.scenes_path/save_scenes/load_scenes`）——拆场景结果+场景状态累积写入 `scenes.json`，供回看/编辑/复用（此前场景不落盘，无法回看）；⑤ **术语统一卡**（`schemas.TerminologyItem` + `World.terminology`）——专有名词（译名/招式名/地名）标准写法+禁用别名，world_prompt 输出、memory 强约束区注入，与 style_taboos（禁用词）职责区分 | 补足"写作过程无专职对白/全本情绪/章末钩子/场景回看/术语统一"五个质量缺口。**关键设计决策**：①对白 Agent 取**确定性聚合版**（零 LLM、幂等、不破坏 token 优化），专职负责对话口吻区分并通过强约束区注入，而非新增一次 LLM 调用；②情绪曲线卡为**确定性聚合**（基于既有各章 emotion 字段，零额外成本），不新增 LLM；③章末钩子走**大纲事前设计 + 写章注入**（不新增钩子 Agent，复用已有 core_event 注入机制）；④场景落盘走 **store 产物文件**（权威源、可回看），后端落盘先行，前端展示接口后续接；⑤术语卡与 style_taboos 职责分明（统一写法 vs 禁用词），分别注入。**约束：新增卡/字段全部 from_dict 兼容旧数据；全部幂等（已存在即复用）；LLM 失败静默降级不阻塞生成**。新增 tests/test_p2_dialogue_emotion_scenes.py 9 用例，全量 428 pytest 全绿 |

| 2026-10-04 | review 修正（用户指出 P2 新卡无条件塞"永不裁剪"强约束区的两个缺陷）：① **物品卡/术语卡改走 setting_cards 分章命中**（REQ-token优化 P1-1）——从强约束区移除，改为普通区按 sub_items/sub_terminology 命中裁剪注入（模式同生物/地点/势力卡），`build_setting_cards` 每章索引新增 items/terminology 字段；本章只用 1 个道具不再每章注入全部卡（未命中该项则不注入，缺索引回退全量）；② **结局卡改末 5 章才注入**——从无条件强约束区改为 `up_to_chapter >= num_chapters - 5` 才注入，与 world_reveals「未揭示设定进强约束区禁剧透」逻辑一致，写第 1 章不再预知终局；num_chapters 未知时保守始终注入 | 修正"建了能力却越界注入"：新卡不应绕过分章命中与剧透控制。**关键决策**：物品/术语卡与生物/地点/势力同属"分章相关实体卡"，归 setting_cards 命中（命中才注入、命中为空不注入、缺索引回退全量）；结局卡属"未揭示高阶设定"，末 N 章才注入（N=5，配合 up_to_chapter + num_chapters 判断）；dialogue_guideline 保留强约束区（全书一致的写作纪律、每章都需要、体积小，非分章实体卡）。更新 test_memory_injects_cards（显式 100 章验证末 5 章窗口）+ 新增 test_setting_cards_items_terminology_trimming（命中裁剪），全量 429 pytest 全绿 |

| 2026-10-07 | 双锚风格锚定（REQ-双锚风格锚定.md，A+B 合做）：① **个人旧稿锚**——`Chapter` 新增 `user_edited`（edit_chapter 保存时置 True，历史章节缺字段默认 False），新增 `style_anchor.py`：`extract_personal_anchor` 只抽人工编辑章片段（每章≤2 段、总长≤600）、`expand-selection`（原裸调用无任何锚）与 `expand_chapter_prompt`（原缺 `_style_reference`）注入风格范文+个人锚；② **拆书库题材锚**——新增 `_inject_teardown_style_anchor`：同题材拆书条目抽 styles/takeaways/excerpts 组装（excerpts 限 2 段×100 字）写 `teardown_style_anchor.txt`，`ChapterWriter` 懒读经 `teardown_anchor` 参数注入 scene/chapter prompt，扩写循环同步注入；新开关 `teardown_style_anchor` 默认 True；③ **分层保持**——novel_gen 不依赖 webapp（题材锚走 store 文件，webapp 写/novel_gen 读，与 teardown_material 同模式） | 根因：朱雀检测 AI 特征 76.86%、人工特征 0；`cfg.de_ai` 为指令式约束（"要有真人质感"）无文本样本可模仿、实测无效；拆书库回流只进调研阶段（正文写手接触不到）；扩写链路裸调用。结论：**示范式锚定（给模型真实人类文本样本模仿）> 指令式约束**；题材锚管广度、个人锚管深度（个人统计指纹对降 AI 率价值最高）。**约束：①题材锚为弱约束（只借鉴语感严禁抄袭），与既有"拆书库回流弱约束"决策一致；②user_edited 仅由 edit_chapter 落、历史章节默认 False——方案 C（前端 diff 个人指纹库）后续单独排期；③重写/质检重写/润色路径未注入，本次只覆盖 scene/chapter/expand 三正文路径**。新增 tests/test_style_anchor.py 23 用例，全量 452 pytest 全绿 + import 冒烟通过 |

| 2026-10-07 | 双锚扩展·重写路径（用户确认"补上吧"）：解除首版决策约束③中"重写路径"部分——① **个人锚接入全部写章路径**：`ChapterWriter` 新增 `_personal_anchor()` 懒提取（只抽 `user_edited` 章，失败静默返回空），`scene_prompt`/`chapter_prompt` 新增 `personal_anchor` 参数注入（对齐 expand 路径格式，空不注入）；单章重写（`rewrite_chapter`）与从第 N 章重生成（`regenerate_from`→`_run_generation`）都走 `ChapterWriter.write`，因此**重新生成章节现在同时吃到个人锚+题材锚**；② **题材锚生成入口统一**：`_inject_teardown_style_anchor` 从 `_research_needed` 分支上移到 `_run_generation` 开头无条件调用——start_from>0 从第 N 章重生成、复用大纲继续生成也注入（旧项目无锚文件时重生成自动补写）；质检重写/润色路径仍未注入（保持首版约束，后续单独评估） | 用户原问"旧项目点重新生成章节会不会触发双锚"：改前单章重写/从第 N 章重生成只走 chapter/scene prompt（无个人锚），题材锚文件旧项目不存在且该路径不创建——双锚都不触发（行为不变但吃不到新能力）。本次改动后：**新项目重写/重生成双锚全生效；旧项目重生成也会自动补题材锚**（个人锚仍需先有人工编辑过的章节才有）。改动：stages.py 个人锚懒提取+3 处传参、templates.py 两 prompt 注入块、core.py 入口上移；新增 4 用例，全量 456 pytest 全绿 |

| 2026-10-09 | 拆场景规划对齐大纲人物动线（REQ-scene-plan-align.md）：**拆场景事件清单从大纲 `character_moves` 确定性派生**（`_derive_events_from_moves`：→/句切拍→死亡-复活因果合并→最短拍兜底至 2-4 事件，纯字符串零 LLM），新开关 `scene_plan_align` 默认 True；`split_scenes_prompt` 注入【事件清单硬骨架】（模型只补 conflict/action/final_state，禁止增删/改写事件），派生事件覆盖模型输出（模型同 id 的结构化字段按 id 合并保留），新增"场景 `scene_event_ref` 必须恰好等于派生事件 id 集合"校验（不通过回退整章）；moves 缺失/派生失败/开关关闭 → 完整回退原 LLM 规划路径 | 根因（《万墟之上》前7章 5/7 章同事件多版本拼接）：大纲 `character_moves` 本已是具体唯一逐拍序列，但从未注入拆场景层——拆场景只收到 goal/core_event，模型每次从叙述文本"重建"事件清单（temperature=0.4 每次不同），并把同一冲突递进误判为不同事件（盘问/试探/失控=一次盘问+一次失控），事后防线只防原文复刻不防换措辞。**关键设计决策**：①事件骨架=确定性派生，LLM 只做"补字段+拆场景"，从源头消灭"每次不一样+同事件拆多版本"；②派生模式下事件互斥改用 desc（骨架原文）相似度兜底而非语义槽位——模型补充的 conflict/action 会把同一人物交互的递进拍标成同动作（扫描/装傻都标"试探"）造成误杀回退；③引用完整性校验兜底模型不守骨架（自造/漏 id→回退整章，整章路径仍注入派生事件序列）。**约束：只改拆场景路径，不改大纲生成/事后质检/webapp；大纲 character_moves 本身重复由大纲层治理，本需求只保证"骨架不被放大"**。新增 tests/test_scene_plan_align.py 13 用例，全量 472 pytest 全绿；真实 outline 前 7 章全部派生为 4 个互斥事件（第2章"盘问/失控/征召"互斥、第3章"撕碎-复活-引导"同事件） |

| 2026-10-11 | 用户级记忆库（REQ/TECH-用户级记忆库.md，跨书创作偏好继承）：三层记忆架构补"用户级"层——① `webapp/user_memory.py` 新增 `UserMemoryService`：`users/<uid>/user_memory.json` 存偏好画像（题材/节奏/避雷，新书显式输入按项目幂等入账）+ 跨书纪律（书级 pitfalls 蒸馏，**全量重算式幂等**：`book_contribs` 保存每本书最新贡献快照，同贡献跳过、贡献变化移除旧贡献重写）+ 个人文风锚（edit_chapter 人工修订跨书登记，上限 30 条淘汰最旧）；全部写路径**零额外 LLM**；② 继承：新书创建 `record_project_signals` + `_inject_user_memory` 写项目快照 `user_memory_snapshot.json`（webapp 写 / novel_gen 读，与题材锚同分层，novel_gen 读侧校验 uid 正整数），`ChapterWriter._user_memory_block` 懒读注入 scene/chapter/expand prompt（跨 ≥2 本书纪律 Top-N=`user_memory_inject_top`=5 + 文风锚 ≤600 字，软约束）；③ 新开关 `user_memory` 默认 True；`GET/POST /api/me/user-memory*` 只读/清空隐私兜底；④ 注入优先级：项目显式设置 > 用户级画像 > 平台默认；题材不匹配的偏好不注入 | 根因：所有沉淀型记忆（BookMemory 四账本 / 个人旧稿锚 / 题材锚）都绑死单本书，用户写完 N 本书的偏好/跨书踩坑/文风指纹第 N+1 本完全继承不到，多用户场景缺"记住用户"能力。**关键设计决策**：①用户级=「书级即时账本 → 用户级画像（离线蒸馏）→ 新书继承（画像注入）」第三层，不推翻既有书级设计；②蒸馏零 LLM（确定性聚合 + book_contribs 全量重算，防漂移/防重复累计）；③跨书纪律需 ≥2 本书确认才注入（单书一次不污染新书）；④继承走 store 文件保持 novel_gen 不依赖 webapp（同题材锚分层）；⑤归属校验在 webapp 写入侧（uid=项目 owner）+ novel_gen 读侧校验 uid 正整数，防跨项目数据串用；⑥用户级只做**软约束**，数字/节拍/拼接确定性校验仍留代码硬校验层。**坑**：Windows 高频覆盖同一文件时 `os.replace` 偶发 WinError 5（Defender/索引服务锁定）→ `_write_json` 加重试 + 直写回退。新增 tests/test_user_memory.py 15 用例，全量 627 pytest 全绿 |

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
| 2026-09-28 | server.py 拆 core+routers（e1c68d1）把 `REGEN_DAILY_LIMIT = 10` 常量定义弄丢、仅保留使用处 → `_check_regen_limit` 每次调用抛 NameError（位于 try 块外、无日志），单章重写 rewrite / 从第 N 章重生成 regenerate-from 两个接口稳定 500，且 regen_count.json 永不落盘 | 已在 `webapp/core.py` 恢复常量定义；新增 tests/test_regen_limit.py 5 个 P1 用例（正常计数/超限 429/损坏容错/跨天重置），全量 210 pytest 全绿 + 真实接口重写 200。**教训：路由拆分回归不能只比对 openapi 路由集合，必须覆盖受影响接口的真实调用路径** |
| 2026-09-28 | 章节生成章节间硬切、无铺垫转折（实测《万墟之上》第1章结尾在墟底石门，第2章开头直接切登记大厅）——场景级生成路径缺「承接上一章结尾」约束：split_scenes_prompt 不知道上一章结尾、scene_prompt 无承接指令（普通整章路径反而有）、记忆上下文只注入近章 goal 从不注入大纲 transition（架构师生成的转场衔接被丢弃）、_prev_tail 仅截 120 字 | 修复：① scene_prompt 首场景必须从【上一章节结尾】直接续写、后续场景承接上一场景结尾；② split_scenes_prompt 新增 prev_tail 参数并要求首场景承接；③ memory 新增 L3.5 注入本章 transition（写手必须按架构师转场续写）；④ _prev_tail 截 300 字；⑤ writer.md 新增「章首」通用承接规则。新增 test_chapter_flow.py 5 个 P1 用例，全量 215 全绿，真实接口重写第2章验证承接成功。**决策：所有生成路径（整章/场景级）都必须有明确的章首承接约束；大纲 transition 是衔接设计的唯一权威来源，必须注入写手** |
| 2026-09-28 | 章节正文偏离大纲 core_event 导致时间线错乱（实测《万墟之上》第1章：大纲设计'跟随临时队下墟→灰卒→死而复生→墟口外醒来→混入逃出人群回城'，正文却写成'进入石门阶梯往墟底深处走'；读者看到第1章像下墟中途、第2章（登记被拒）像下墟前，时间线倒置）——写手只收到抽象的 chapter_goal，核心事件 core_event（本章具体发生什么）从未注入，模型自由发挥 | 修复：write() 内部从 outline 取本章 core_event，注入全部三条写手路径（chapter_prompt/split_scenes_prompt/scene_prompt），提示词统一标注【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】。新增 3 个 P1 用例，全量 218 全绿；真实接口重写第1、2章验证时间线理顺。**决策：写手必须同时收到 goal （方向）与 core_event（事件主线）；正文偏离大纲核心事件的场景视为生成缺陷** |
| 2026-09-29 | 点「重生成世界观」后世界观/人物档案全空：`regen_world` 先 `save_world(空World)` 再起线程，但 `load_world()` 对存在的文件一律返回对象（空壳≠None），`run_world._produce` 判定"已存在则复用"直接返回空壳、不调 LLM；空 world 又无致命问题被 StageGate 静默放行 | 重生成类接口必须**删产物文件**（与 `regen_outline` 删 outline.json 同模式），不能写空壳；并在起线程前备份旧产物，失败时恢复，异常落 `logger.exception`（原 `_do` 吞异常仅存 handle.error，排查无日志）。回归：点重生成世界观→app.log 出现 LLM 调用与阶段日志→world.json 非空 |

## 三、文档漂移记录

- **2026-09-21**：修复 `README.md` 目录结构中的过期条目——`novel_gen/cli.py`、`__main__.py` 实际不存在（当前产品入口为 Web 端 `run_web.py`），已删除该行；README 中「快速开始（命令行）/ 命令一览」两节仍描述 `python -m novel_gen ...` CLI 用法，**与现状不符，待确认是否清理**。
- **2026-09-21**：`.gitignore` 现状——`docs/`（需求分析文档）与 `AGENTS.md`（注释为 "AI agent rules (local only)"）均不入库。`docs/architecture.md` / `filemap.md` / `decisions.md` 三件套默认同受 `docs/` 忽略；**若要跨机器复用 AI 知识库，需在 .gitignore 中为其加白名单（如 `!docs/architecture.md` 等），由用户拍板，勿擅自改动**。

## 四、新增决策的写法

追加时按以下模板，保持一行一条、可回溯：

```
| YYYY-MM-DD | <决策一句话> | <原因/影响，含回归验证方式> |
```

## 2026-09-30 章节质量防线·第三轮：面板事件锚点 + 摘要一致性
- 背景：21:52 版《万墟之上》第1章（3574字）质检 6 大改中，"机制数字未结算推进"实为误报——"1/3"三次显示（第二次死亡结算/通道确认/章末备注栏变化）数字本就不该变，属冗余显示而非结算错误；另发现摘要"3/3→2/3"与正文最终"1/3"不符（摘要未随正文更新）。
- 决策：
  1. _panel_progress_issues 加结算事件锚点（死了/死亡/复活/苏醒/睁眼/醒来/结算/重置）：两次同值间有强结算词 → 真未推进（大改）；无 → 降级"面板数字重复显示（冗余）"中改。锚点排除"死一次"（预判）、"咬穿"（回忆复述）、"刷新"（"面板刷新的时候"设定陈述）——实测误命中。
  2. 新增 _summary_consistency_issues：正文最终面板分数 vs 摘要分数，不一致 → 大改（摘要未随正文更新）。
  3. 事前 prompt（scene_prompt 19-22）：面板显示纪律（数字只在结算瞬间出现一次）、状态度量单一口径（蚀痕累积口径禁多处重新计量）、角色身份全局锁定（同一角色不得两套描述）、摘要覆盖最终结算状态。
  4. 重写 prompt（_rewrite_whole）同步注入面板显示纪律 + 摘要必须写最终面板数字。
- 验证：392 全绿；真实 3574 字正文回验：1/3 冗余→中改（误报消除）、摘要 2/3 vs 1/3→大改。

## 2026-09-30 章节质量防线·第四轮：锚点语境排除 + 面板行移出复读统计
- 背景：22:32 版第1章（4471字）质检 2 条大改"机制数字未结算推进"经上下文核查全部误报——6 处锚点全是回忆/指认/他人语境（"她看见他死了""你死了""她快死了""他应该已经死了""感知到了他死亡的情绪印记"），面板 2/3 未变是正确行为；另有"同句复读"大改实为面板行"2/3"×3（面板行已由专检归类，复读再判大改 = 双重大改误触发重写）。
- 决策：
  1. _panel_progress_issues 锚点加语境排除（_PANEL_EVENT_RECALL，前 16 字含 看见/说/读/感知/应该/快死 等回忆转述词 → 跳过），特判"快死了"（前 1 字"快"）——回忆不算结算事件。
  2. _repeat_sentence_issues 不再统计面板行（_is_panel_line 直接 continue）——面板行重复唯一归属 _panel_progress_issues（跨结算大改/冗余中改），杜绝双重大改。
- 验证：393 全绿；真实 4471 字正文回验：面板→冗余中改（误报消除）、复读→空、字数→中改（4471 字 1.79 倍，真问题）、对话轮/结尾→无。

## 2026-09-30 章节质量防线·第五轮：跨场景事件重复 + 字数收紧（事前+事后）
- 背景：22:32 版第1章（4471字）真问题——①"救苏妄言"事件写了空地/坡地两个版本（场景间"已推进状态"只含数字/生死/物品，不含事件，场景3不知道事件已发生）；②字数 4471=1.79 倍仅中改不阻断；③蚀痕"一寸"与"三指"单位混用。
- 决策：
  1. 事前·事件清单注入：stages.py 场景循环按拆场景定义的 event 累积【本章已发生事件】清单（确定性、零 LLM），拼入 scene_state_hint 注入后续场景；scene_prompt 新增 23 条"已发生事件禁止重复"（同人物+同冲突+同结果=重复，换地点重写也算）。
  2. 事前·蚀痕单位锁定：scene_prompt 20 条细化"增长量只用'寸'，禁止'三指/一指宽'"。
  3. 事后·矛盾自检加"同一事件两版本"：_consistency_llm_check prompt 增加第⑤类检查（事件重复），type 枚举含 事件重复。
  4. 事后·字数审计收紧：>1.6 倍 → 大改（触发重写压缩，4471=1.79 触发）、>1.35 倍 → 中改。
- 验证：395 全绿（新增 场景事件注入/矛盾自检事件重复/字数阈值 用例）；真实 4471 字回验：字数→大改。

## 2026-09-30 章节质量防线·第六轮：字数审计方向修正（超长≠质量失败）
- 用户确认：超长还触发整章重写浪费 token；正确做法是看生成质量，而非完全看生成字数。
- 决策：_length_audit_issues 不再产出大改——>1.35 倍目标仅中改记录（不阻断、不触发重写）；质量硬伤（截断/循环/拼接/矛盾/数字错误/摘要不符）仍由各自维度判大改触发重写，重写 prompt 已含字数区间约束会自然压缩。
- 验证：395 全绿；真实 4471 字回验：中改（不再大改）；6202 字版因含循环/截断/拼接大改仍会重写（合理——质量问题触发，非字数触发）。

## 2026-09-30 章节质量防线·第七轮：拆场景设计重构（事件清单先行，治本而非打补丁）
- 用户指出：一直在打补丁（事件清单注入/同句复读/段落重复检测），根因在场景拆分设计——模型把同一事件拆成多个场景（6316 字版"苏妄言被围"5 版、"灰卒咬喉死"2 版），旧 _scene_events_overlap 只做文本相似度、措辞一变全放行，且不校验场景数。
- 决策：
  1. split_scenes_prompt 重构：事件清单先行（events，1 事件 = 1 场景，事件互斥+因果推进），场景 scene_event_ref 逐字引用事件编号（机制上杜绝换措辞把同一事件塞进两场景）；场景数 = 事件数（2-4，由总字数反推）。
  2. 结构化校验 _validate_scene_plan（零 LLM）：场景数 ≤4（>4=凑数拆重复场景→回退整章）、多场景 target_words 和 ∈±10%、事件引用唯一性（同编号被 ≥2 场景引用=重叠）；旧格式降级文本相似度。
  3. 场景衔接强约束：scene_prompt 加【本场景唯一事件】+ 后续场景必须从上一场景"结局之后"续写（禁止重演上一场景情节，连起手式都禁）；force_new_progression 重试块。
  4. 生成端兜底（拼接发生点拦截）：相邻场景全文相似度 >0.55 → 重试本场景 1 次（强制从结局后续写），不整章重写。
- 验证：398 全绿（新增 场景数超限/事件引用唯一/引用展开/相邻场景重试 用例）；旧校验函数保留兼容别名。

## 2026-10-01 章节质量防线·第八轮：事件语义互斥 + 局部复刻拦截 + 重写事件纪律
- 背景：4500 字版质检 4 条大改全命中但重写 3 轮修不掉——①"救苏妄言"石殿版/墟口外版（事件 desc 措辞不同，文本相似度 <0.55 放行）；②死亡复刻段约 200 字嵌进场景 2（相邻场景全文相似度 <0.55 漏检）；③蚀痕状态两次相同无增量。
- 决策：
  1. 事件语义槽位互斥（零 LLM）：_scene_events_semantically_overlap——事件输出结构化 conflict+action，归一化动作词表（救/围/杀/逃/追/挡…）；两事件（冲突对象, 动作）相同 = 重复事件，拆场景阶段回退整章。desc 兜底提取冲突对象/动作。
  2. 相邻场景长段复刻率：_long_sentence_overlap_ratio——当前场景中与上一场景完全相同的 ≥15 字句子占比 >20% → 重试本场景（全文相似度对局部复刻漏检）；重试仍复用全文+长段双指标择优。
  3. 事件状态增量规划：拆场景 events 新增 final_state（事件结束时的面板/蚀痕/伤势增量），scene_prompt 注入【本场景结束时的机制状态】硬约束（禁止与上一场景结束状态相同）。
  4. 重写事件纪律：_rewrite_whole 在质检含事件重复/两版本时注入 8-10 条（删除重复版本保留最完整者、事件间因果推进禁止重演起手式、每事件结束状态必须有增量）——补"检测到位但修复无锚点"缺口。
- 验证：401 全绿；真实回验：救人二版语义互斥=True（拆场景拦）、死亡复刻长段重叠率=0.82（场景生成重试）。

## 2026-10-01 章节质量防线·第九轮：拆场景 JSON 解析健壮化 + 回退整章事件注入
- 背景：实机重生成暴露真正根因——拆场景 LLM 输出三块（events+scenes+characters_action）较长，多次在结尾处 JSON 语法断裂（'Expecting value: line 46 col 17'），json.loads 失败直接 scenes=[] 回退整章 → 事件清单（含救苏妄言核心事件）整条丢失/事前防线（语义互斥/长段复刻/final_state）全部没机会执行：①6649 字版整章自我重复 3 遍救人+死亡复刻（质检 6 大改全命中）；②4874 字版拆场景失败回退整章、救人事件整体丢失、score=45 仍 3 问题。
- 决策：
  1. _strip_json_block 健壮化：剥代码块 → 逐级回溯最近可解析 } 前缀 → 修尾逗号（,\s*[}\]] → 闭合符）。
  2. 字段级兜底 _extract_json_field：整体解析失败时用正则分别救回 events/scenes/characters_action 数组（events 在前，截断通常只丢尾部，events 常可救回）。
  3. 拆场景失败重试 1 次（温度 0.4→0.5，prompt 追加"只输出合法 JSON，禁代码块/注释/省略号/尾逗号"），仍失败才回退整章。
  4. 回退整章注入事件序列：chapter_prompt 新增 chapter_events 参数，拆场景失败但 events 可救回时把事件清单（含 final_state）拼入整章 prompt，并附"每事件只写一遍、按编号顺序推进、禁重演"纪律；整章 prompt 无条件新增 3.5 条通用防自我重复纪律。
- 验证：404 全绿（新增 _strip_json_block 健壮/字段级截断救回/回退事件注入/拆场景失败重试用例）。

## 2026-10-01 章节质量防线·第十轮：场景正文事件级复写检测（治本，非打补丁）
- 背景：第三版 3395 字（score=0，4 大改）——救人事件坡顶版/墟口外版两处完整重演（质检 LLM 抓"事件重复"），但语义互斥只校验拆场景 events 清单：两个场景各自引用唯一事件、conflict 措辞'三条赤尉'vs'巨型赤尉'不同→文本相似度与事件清单双漏检。根因=场景生成时模型无视【本场景唯一事件】软约束，把已发生事件换措辞又写一遍。
- 决策：
  1. _scene_text_rewrites_event（零 LLM）：对每个刚生成的场景正文提取事件槽位（冲突对象 _event_conflict 优先敌人词 赤尉/灰卒/蚀兽，其次人物），正文含救援专属动词（救/引开/砸/挡/扑/迎）+ 冲突对象与任一已完成事件同现 → 判复写（"冲/拽/拖"太通用会误判撤离场景，不放宽）。
  2. 场景循环接入：场景 N 生成后与全部已完成事件比对（不只相邻场景），命中 → 重试本场景（scene_prompt 新增 forbid_event 参数：明确"上版把已发生事件【原文】重演了，禁止以任何形式再次发生，须从该事件结局之后续写"）；重试版二次检测通过才采用。
- 验证：406 全绿；真实回验：第三版场景3(墟口救人)复写场景2(坡顶救人) 检测=True（生成端即拦），场景1(灰卒死亡) 不误报。

## 2026-10-07 质检误报根因修复（事件重复判定收紧 + 落盘分流）
- 背景：第1章 22:05 单章重写后 review.json 报「自相矛盾-事件重复」大改（score=75, auto_revised=true），正文仍落盘。逐字核对 001.json：正文为「墟口观察→决定绕走（内心念头）→掉头冲出救人（实际行动，仅1次）→苏妄言口型'你刚才……在下面'」，不存在同一事件两版本——该条大改为质检误报。根因链：①_consistency_llm_check 第⑤类 prompt 未区分「决策/念头」与「事件发生」，LLM 把"决定绕走"当事件发生判两版本；②_locale_problem_spans 对该 detail 定位出 2 窗口→不满足单窗口且<60%→回退 _rewrite_whole 整章重写，dedup_block 命中"事件重复"注入"删除重复版本"指令，正文无重复可删→复检再报同一条→_MAX_REWRITE=3 轮耗尽，浪费整章重写 token；③stages.write() 457-470 调用 ensure_quality 后 472-473 无条件 save_chapter，passed=false 不阻断落盘。
- 用户拍板：方案1（收紧质检判定标准）+ 方案4（落盘分流）组合实施。
- 方案1：_consistency_llm_check 第⑤类 prompt 新增排除条款——同一冲突对象下「内心决定/犹豫/退缩念头」与「实际行动」属抉择弧光、可同时为真，不判重复；未执行的念头/心理活动不算"事件发生"；无法引用两处实际行为原句不得输出事件重复；事件重复 detail 必须引用两处实际发生行为原句。
- 方案4：stages.write() 对 ensure_quality 返回 passed=false 时，若该章已有旧版（load_chapter 非空）→ 保留旧版正文不覆盖（坏章不得顶掉用户已有可用版本）；仅无旧版时才落盘新稿（供前端红标提示）；rewrite 路由返回 kept_previous 字段暴露该 decision 给前端。
- 验证：459 全绿（新增 prompt 排除条款注入、保留旧版落盘、无旧版落盘 3 用例）；P0+P1 244 全绿。

## 2026-10-09 拆场景规划对齐（scene-plan-align）实机验证：规划稳定但正文仍重演，master 已回退
- 背景：REQ-scene-plan-align 实现后真实重生成前7章，拆场景规划层达标（7章全部 4事件4场景 骨架通过、无回退整章、"每次生成不一样"消除）；但逐章通读全文发现 6/7 章正文仍是"同一完整情节循环演绎3遍"（第2章盘问-失控-挡死-留牌×3、第3章引赤尉-死-复活-对峙×3、第4章逼问-结拜-找人×3、第5章诊脉-试探×3、第6章暗格+酒肆×3、第7章赤尉战-贯穿-杀-包扎×3），含面板账本矛盾（蚀化98→97回退、0→3无重置依据）、苏妄言蚀痕左右矛盾、小九开口说话（违反机制硬规则"禁说人话"）。此前"拼接已消除"结论（段落相似度检测）有误——该检测只抓文本级重复，抓不住情节级重演。
- 根因（代码实证，详见 REQ-scene-plan-align §7.2）：
  1. 骨架事件粒度与场景粒度不匹配——场景正文"越权消费"：模型写"事件1"场景时把事件2~4完整流程一次写完，后续场景无新事件可写→换措辞重演；done_events 只登记规划事件（sc.event），不登记正文实际发生，场景2-4 无感知。
  2. 场景2+ 注入全章行动表（"必须逐条落实"）→ 每场景落实完整行动序列。
  3. 防重演检测口径窄：相邻场景全文相似度（0.55）/长句重叠（0.20）只查文本复刻；_scene_text_rewrites_event 只认救援动词+冲突对象、骨架 desc 多数无救援动词直接跳过。
  4. 质检按"人物+冲突+结果完全相同"判重，换措辞重演漏报。
- 决策：master 回退至 5190063（origin/master 一致）；feat/scene-plan-align 与 devtest 合并保留，继续迭代。解决措施（场景正文消费边界/行动表按场景切片/done_events 正文级节拍登记/骨架 desc 互斥/质检情节级检测）已列入 REQ-scene-plan-align §7.3，待实现。
- 教训：验证"同事件重演"必须以全文通读或情节节拍序列检测为准，文本级相似度检测存在盲区；场景正文层是重演的最终出口，规划层修复不能替代场景层约束。

## 2026-10-09 正文层防重演实现（REQ-scene-plan-align §7.3 措施1/2/3/5）
- 背景：规划层修复（骨架派生）生效但正文仍重演，§7.2 根因 A-E 实证（越权消费/全章行动表/检测口径窄/规划事件登记/质检漏报）。本次实现正文层防线。
- 决策与实现（feat/scene-text-anti-repeat）：
  1. 新增 novel_gen/pipeline/beats.py：情节节拍序列（长词优先归一化 + LCS 判定）。_beats_rewrite_detected：场景重演判定 LCS>=4 且占比>=0.7（正常单事件 2-3 拍不误判；机制允许的多次死亡-复活序列短不触发）；_beat_cycle_issues：质检 2.12 整章同节拍序列非重叠>=2 次判大改（子序列匹配，容忍换措辞版插入节拍）。
  2. stages.py：场景正文节拍序列登记 done_beat_seqs（正文级，补 done_events 只登记规划事件的盲区）+ 防线3 拦截（命中重试至多 2 次，forbid_event=本场景唯一事件；仍重演回退整章 _fallback_whole_chapter 复用）；场景 prompt 注入 event_progress 事件进度硬约束（只推进本场景事件、禁提前消费后续事件）；行动表措辞改"只落实本场景事件相关行动、禁跨场景重复落实"（防全章行动逐场景落实）。
  3. templates.py scene_prompt：新增 event_progress 参数 + 【事件进度（硬约束）】块 + 场景衔接规则补"后续事件留给各自场景"。
  4. quality.py：check() 新增 2.12 情节级重演检测（_beat_cycle_issues，大改 -30 分，触发自动重写链路）。
- 验证：tests/test_scene_anti_repeat.py 14 用例全过；全量 P0+P1 271 绿（新增 14）；真实回验——第1章（干净）未检出、第2/3/5/7章（人工通读确认重演）全部命中（如第2章「面板→对峙→留牌→失控→挡死」循环检出）——检测器在真实文本上验证有效。
- 待办：机制一致性强化（§7.3 措施6：蚀痕位置全局唯一/小九禁说人话可检测项）后续专项；真实重生成 1-7 章验证正文重演是否消除后再评估签约。

## 2026-10-09 正文层防重演第二轮实机验证：修复端仍失败，转生成端修复（REQ-scene-plan-align §7.3 措施3/5 生成端）
- 背景：正文层防重演实现后触发真实重生成前7章（第二轮稿 5487/2081/4991/5280/2752/3201/3086 字）。双验证（节拍检测器+逐章通读全文）结论：**检测器与人工一致——第1/3/4章仍重演**（第1章铁钩版/铁锤版两遍演绎、第3章"洞窟北壁炸开了"逐字重复两次+赤尉循环两遍、第4章贺枭两遍+面板蚀化99→97→98→97违反只增不减）；第2/5/6/7章干净。检测端有效、修复端（场景重试）失败。
- 日志实证根因（regen_console.log）：
  1. 第4/7章场景防线连续 2 次重演→回退整章，但整章单次生成又写出两遍（_fallback_whole_chapter 无生成后即时校验，注释还写"重演由质检 2.12 兜底"）；
  2. 第1/3章场景重试"1次即放行"太松——重试稿压缩重演段/插序使整序列 LCS 占比 <0.7 逃逸；
  3. 场景1内部两遍是盲区——done_beat_seqs 为空时无比对对象；
  4. 行动表 7 次全部生成失败（split_scenes_prompt 输出 characters_action JSON 解析 "Expecting ',' delimiter"，行动表按场景切片措辞从未实际生效——已记入待办，本轮未修）。
- 决策：**生成端优先，修复端（质检重写）为第二层**。生成端第二轮修复（工作区，未提交）：
  1. beats.py 新增 `_beats_local_rewrite_detected`：已完成序列中任一 4-7 拍子序列被新场景复现 ≥0.8 即判局部重演（重试稿压缩/插序逃逸兜底）；
  2. stages.py 防线3 触发条件三重叠加：`_beat_cycle_issues`（本场景正文自身内部循环，堵场景1盲区）+ 整序列 LCS + 局部子序列重合；
  3. done_beats_text 事前注入：登记 done_beat_seqs 后维护最近 3 个场景节拍组合文本，注入后续场景 prompt（scene_prompt 新增 done_beats 参数 + 【本章已发生节拍序列（硬约束）】块），事后比对升级为事前告知；
  4. `_fallback_whole_chapter` 生成后即时校验：`_beat_cycle_issues` + 字数超限（>1.35×目标）→ 重试至多 2 次（注入重复节拍证据 + 压缩字数目标），不再把重演全部押给质检重写。
- 验证：test_scene_anti_repeat.py +7 用例（局部重合命中/不误报/短序列/空 done、done_beats 注入/不注入、场景1内部两遍→重试、整章回退生成后重试），相关 35 用例全绿，全量 P0+P1 279 绿；真实文本回验行为不回归（第1/3/4章命中、第5/6/7章干净、第2章短节拍为正常节奏误报）。
- 待办：真实重生成 1-7 章验证生成端第二轮是否消除重演→评估签约；行动表 JSON 解析失败修复；§7.3 措施6（机制一致性可检测项）后续专项；master 回退（5190063）是否 push origin/master 未定。

## 2026-10-09 生成端第三轮：事件消费账本 + 面板数字确定性闭环 + characters_action 补全（外部调研后用户拍板"生成端的优化都做"）
- 背景：第三次真实重生成（22:39-22:53）双验证仍 5/7 章重演（第2/5/7章从"干净"回归"重演"，生成端第二轮未根治），另有系统性面板账本崩坏（第2章起死亡不扣次数、蚀化98→97/99→97回退、2→0跳变）。用户要求先调研市面产品/开源/学术怎么做，OrganizerAgent 四路并行调研（NovelAI/Sudowrite/Goink/AutoNovel/LongWriter/Re3/SimCTG/DOC + vLLM/Outlines 等 12+ 对象，关键来源独立核验），产出《竞品对比分析_防重演方案_20261009.md》。五条主线：规划层"指针制"防越权、正文层真实前文+采样抑制、检测层词面→语义指纹、状态层确定性账本契约（**全行业无人做机制数字确定性计算=差异化机会**）、校验层独立审稿。用户拍板实施生成端三项（P0-2 语义指纹属检测端，不在本轮）。
- 决策与实现（feat/scene-text-anti-repeat 工作区，未提交）：
  1. **P0-1 事件消费账本状态机（治 a 跨场景分布型重演）**：`done_events`（list[str]）升级为 `event_ledger`（{desc, consumed_scene_idx}）；`scene_prompt` 新增 `consumed_events`/`upcoming_events` 参数与两块注入——【本章已消费事件（只许一句话回响，禁再展开完整流程）】【本章后续事件预览（禁提前消费，只许铺垫提及）】。对标 AgentWrite `$STEP$` 指针制（计划全文可见、每次只给当前一个执行单元）与 Goink 伏笔账本状态机。
  2. **P0-3 面板数字确定性状态闭环（治 c，差异化机会）**：`chapter_state.py` 新增 `panel_baseline_dict`/`panel_direction_violations`/`panel_numbers_sequence`（零 LLM：次数/剩余只许递减、0→N 视为子时重置放行；蚀化/进度只许递增）；`scene_prompt` 新增 `panel_baseline`（机制数字基线注入）+`panel_violations`（带错重试）；场景循环防线4：正文机制数字违反方向规则→带错重试至多 2 次（注入基线+违规描述），仍违反用最后稿（质检 2.x 二次拦截）。工程对标：Observed→Propose→Verify→Commit 闭环 + Re3 属性-值字典矛盾纠偏。
  3. **P0-4 characters_action 单字段补全（清 7 章行动表全部解析失败待办）**：拆场景大 JSON 长输出截断只丢尾部 characters_action（"Expecting ',' delimiter"）时，新增 `_fill_characters_action` + `characters_action_fill_prompt` 让模型只输出行动表数组（小调用低截断风险，最多重试 1 次，仍失败返回 [] 不阻塞）；**仅整体 JSON 解析失败且救回为空时触发**（整体成功但为空数组=模型真未规划，不补全，防无谓额外 LLM 调用——首版无条件补全致既有用例 side_effect 错乱的教训）。行动表 key_actions 与事件清单逐条对应（每事件至多一个 key_action，防行动表诱导跨场景重复落实）。
- 验证：专项 47 用例全绿（35 基线 + 12 新增：P0-4 截断补全/空数组不补全、P0-1 双块注入、P0-3 单元 7 例 + 集成重试 1 例）；全量 506 用例 505 绿（1 例 test_final_review_llm_error_safe Windows 文件锁瞬时失败，单独重跑通过，与本轮改动无关）。已踩坑：①characters_action 无条件补全会额外消耗 llm.chat 调用破坏 side_effect；②测试占位文本若含节拍词（铜牌/跟上/离去）且 80 遍重复会形成 160 拍内部循环误触发防线3——测试文本须避开节拍词；③场景 target_words 和超章节 ±10% 会回退整章（2×2500 vs 目标 2500）。
- 待办：验证通过后触发第四次真实重生成（双验证：节拍检测器+逐章通读全文）→ 评估签约 → 达标后走 devtest→master 合并；P0-2 语义指纹并联（检测端，bge-small-zh 本地或云 embed、阈值 0.60-0.70 需自建标定集）留待后续；§7.3 措施6 蚀痕位置全局唯一/小九禁说人话仍待专项；master 回退（5190063）是否 push origin/master 未定。

## 2026-10-10 生成端第四轮（P0-5）：蚀化抽取补齐 + 重写 best-version 兜底 + 重写拼接禁令 + 行动表补全日志
- 背景：第四次真实重生成（v4，00:28-00:45）6/7 章干净，仅第 5 章重演 + 蚀化 97/98 跨章回跳未根治（第四轮评估 §5 两盲区）。盲区 A（第 5 章 5945 字=2.4×，多版本拼接）：ensure_quality 自校验整章重写产物不回跑场景级防线——重写 1 把拼接写得更严重（2→4 问题）、重写 2 无效后坏稿直接落盘（首次生成无旧版，方案4 也救不了）。盲区 B（蚀化回跳）：_PANEL_RE（chapter_state.py）抽取正则后缀缺「蚀化」——第三轮新建的校验正则 _PANEL_DIR_NUM_RE 含蚀化、抽取正则没回补，两正则分家；numbers 账本无蚀化→panel_baseline_dict 基线无蚀化→防线4「无基线不校验」→蚀化回跳从未被拦。用户指令：p0-5 开始做；plan_re 先查为什么少了蚀化；提示词可以加；日志可以加。
- 决策与实现（feat/scene-text-anti-repeat 工作区）：
  1. **修复1（盲区 B 根因）**：chapter_state.py _PANEL_RE 后缀补「蚀化」→(次数|进度|蚀痕|蚀化|剩余)，docstring 说明分家根因；quality.py _panel_progress_issues 的 pattern 同步补蚀化（质检层同漏）。
  2. **修复2（盲区 A 兜底）**：quality.py ensure_quality/check 新增 baseline 参数；check 新增 2.8b 面板方向校验（baseline 非空时调 panel_direction_violations，每条大改 -30，重写/改稿产物质检层二次兜底蚀化回退/次数回升）；ensure_quality 新增 best-version 账本——初始稿为基准，重写轮内按「问题数升序、同数则分数降序」择优，循环结束仍未通过且最优版严格优于当前 → 回退最优版（坏稿不得顶掉相对较好版本；首次生成无旧版时尤其关键）。
  3. **修复3（重写拼接禁令）**：_rewrite_whole has_event_dup 关键词补「循环重演/多版本/换措辞/重演」（2.12 的 type=情节-同事件循环重演、detail 含多版本/换措辞循环此前完全不匹配，dedup_block「每事件只写一遍」不触发）；重写 prompt 无条件新增【硬性要求：重写即整体替换】块（只输出单一完整连贯新版、严禁保留原文段落再追加新写法、严禁同一场景/事件换措辞写多遍拼接、修改在原处替换）。
  4. **修复4（日志）**：stages.py 模块4 调 ensure_quality 时经 panel_baseline_dict 透传场景循环累计 numbers 为 baseline（场景路径；整章路径无则 None，与防线4 同一基线杜绝口径漂移）；_fill_characters_action 补成功日志（第 N 次补全成功：注入 K 条行动），调用处补「行动表补全成功，注入 N 条」——此前只有失败日志，无法区分「独立行动表调用失败回退」与「拆场景救回+单字段补全成功」。
- 验证：新增 tests/test_quality_p05.py 8 用例（蚀化抽取/基线含蚀化/方向违规端到端、check baseline 拦截+无 baseline 不误伤、重写「有效但更差」+下一轮无效→回退最优、重写稿更优→采用不误回退、dedup_block 命中循环重演+无条件拼接禁令）；test_quality.py 短章重写用例适配（原重写文本本身触发 3 大改，P0-5 兜底后正确回退——测试改用 _LONG_OK_TEXT 通过稿，意图不变）；test_chapter_flow.py 两个 _fake_ensure 补 baseline 参数。专项 54 绿 + 全量 513 绿（test_reveal_tracking 文件锁瞬时用例单独重跑通过）。
## 2026-10-10 生成端第五轮（P0-7）：小说级记忆系统（book_memory）
- 背景：第五轮真实重生成（v5）后第 3/6 章重演、第 7 章跨章扣次错误仍未根治——历轮防线（节拍词典/LCS/面板正则/dedup 关键词）全是"写死的规则"：规则覆盖的场景已能拦截，但新名词、新错误模式（第 3 章两次下墟双战斗、第 6 章酒肆双版本、第 7 章跨章扣次）规则不认识 → 每轮只能人工定位再改代码，线性成本不可扩展。用户提出给每本小说加记忆系统：质检出问题 → 踩坑记录更新 → 自动优化提示词重写 → 针对本书持续更新记忆写出更好作品；并拍板"开始做吧"。四账本分层设计 + HTML renderer 架构图已交付（设定 canon/事件 events/踩坑 pitfalls/经验 taste）。
- 决策与实现（feat/scene-text-anti-repeat 工作区，docs/TECH-book-memory.md）：
  1. **新模块 `pipeline/book_memory.py`**：`BookMemory` 四账本持久化 `book_memory.json`（store 新增 `book_memory_path/load/save`，复用原子写）；`record_pitfall` 质检出问题结构化入账（零额外 LLM、只记大改级、pattern=issue.type 归并、同章去重跨章累加、example 保留最近 2 条）→ `escalate` 同模式 ≥2 次升级永久纪律进 canon（排除结构-/合规-/读者- 无操作性指令类，只升一次）→ `decay` 超 3 章未复发 recent_rounds 清零降权；注入组装 `pitfall_block`（Top-N 按次数×最近频率，for_rewrite/场景双措辞）+ `canon_block`（新名词规范/永久纪律/机制基线，拆场景注入）+ `numeric_backfill`（面板方向违规 detail → 确定性修复指令「基线 X：你写的 Y 违反，正确应为 Z」回灌重写）+ `record_event_consumed` 跨章事件消费 + `record_taste` 章节评分（最近 20 章防膨胀）。
  2. **接入 stages.py**：`ChapterWriter._memory()` 懒加载（开关关闭/异常 → None 降级安全）；拆场景 `split_scenes_prompt(book_canon=...)` 注入设定账本；场景循环开头算一次 `book_pitfalls_text = self._book_pitfalls(top_n=3, min_count=2)`（**只注入已反复 ≥2 次的纪律类，防低频问题束缚正常生成**），5 处 scene_prompt 调用（正常/重试1/重试2/节拍重试/面板重试）全注入；场景事件消费登记后 `record_event_consumed` 跨章持久化；质检块后 `record_taste`+`decay`；`_fallback_whole_chapter` 拼入踩坑块（top_n=5, min_count=1）。
  3. **接入 quality.py**：`__init__` 可选 `memory=None`（老调用方零改动）；`_record_pitfalls` 在 ensure_quality 每轮 check（初始/抢救后/重写复检）后调用 + escalate；`_memory_block` 组装 pitfall_block(for_rewrite=True) + numeric_backfill（只对机制数字/面板/蚀化 detail 提取），`_rewrite_whole`/`_rewrite_span` 在 ctx 之后、质检问题清单之前注入。
  4. **接入 templates.py**：`split_scenes_prompt` 新增 `book_canon=""`（骨架块之后追加）；`scene_prompt` 新增 `book_pitfalls=""`（panel_violation_block 之后追加）。
  5. **config.py**：FEATURE_FLAGS 注册 `book_memory`（默认 True，"False=关闭行为与 P0-5 一致"）+ `book_memory_top_n`（默认 5）；NovelConfig 对应字段。
- 边界（硬约束）：可计算可验证的（数字方向/节拍循环/拼接检测）永远留在代码硬校验（chapter_state/beats），记忆只做软约束——规则覆盖不到的开放域（新名词规范、历史错误形态、修复指令）；最坏情况仍靠硬校验 + best-version 兜底。写入时机 = 质检/防线每次触发时（零额外 LLM）。
- 验证：新增 tests/test_book_memory.py 21 用例（踩坑入账去重累加/只记大改、升级只升一次/排除通用类、降权、Top-N 排序、canon 组装、数字回灌解析与透传、QC 质检出问题入账、重写注入历史错误+正确应为 Z、无 memory 不注入、scene/split prompt 注入、writer 懒加载、book_memory=False 全跳过不落盘）；专项 59 绿（含 quality_p05/scene_anti_repeat/panel_direction 回归），全量 534 全绿。
- 待办：验证通过后触发第六轮真实重生成（双验证：节拍检测器+逐章通读全文）→ 评估签约 → 达标后走 devtest→master 合并；P0-6 其余四项根因修复（回退整章 baseline+即时校验/跨事件结构重复检测/死亡-扣次因果校验/酒肆型同场景双版本检出）待 P0-7 验收后按序实施；P0-2 语义指纹并联留待后续；master 回退（5190063）是否 push origin/master 未定。

## 2026-10-10 生成端第六轮（P0-8）：机制数字链确定性闭环（8 项，工作区未提交）
- 背景：第六轮真实重生成（v6）评估结论——**0 章事件级真实重演（历史最优）但未达签约**，阻塞项切换为机制数字链 5 处硬伤：蚀化 97→98→98→99→97→98 跨章波动（第 3 章未递增/第 6 章回跳/第 7 章承接错）、次数 2→0 超扣（第 2 章）、死亡后次数 1→1 死而不扣（第 7 章）、蚀痕位置 5 章口径混乱（基线「已伸三十七寸距心约一寸」从未被遵守）；另发现：记忆系统两缺陷（`_fix_of` 关键词错配——循环重演 issue 的 detail 含"面板"被劫持成机制数字指令致两条纪律 rule 错标；机制基线从未入 canon）、行动表 JSON 7 章全失败、成本防护场景[review] 25 次致第 4 章起一致性校验全停。第六轮评估报告给出 8 项生成端建议，用户拍板"继续"，实施全部 8 项（P0-8）。
- 决策与实现（feat/scene-text-anti-repeat 工作区，未提交）：
  1. **根因1（行动表 7 章失败）**：`_gen_action_plan` `max_tokens=800` 太小（中文约 400-500 字，多人物行动表常超 → 输出在 ~1000-1200 字符处截断 → JSON 解析失败，v6 日志 `Expecting ',' delimiter: line 25/26 column 6 (char 992-1195)`）。修复：max_tokens 800→2000；解析失败 → `_extract_json_field(raw,"characters")` 数组级救回 → 仍失败重试 1 次（温度 0.5、上限 2000）。
  2. **根因2（质检基线透传 bug）**：write() 原 `locals().get("scene_state_acc")` 拿不到 `_generate_chapter_scenes` 嵌套函数变量，**_bl 恒为空 → 质检层方向校验从未生效（v6 之前全盲）**。修复：新增实例属性 `self._scene_state_acc`，场景路径返回前赋值，write() 改用它。
  3. **根因3（蚀化 99→97 跨章回跳）**：`_fallback_whole_chapter` 生成后即时校验只查节拍循环+字数，不查面板方向。修复：`panel_baseline` 参数 + 生成后方向/因果校验 + 违规数字修复回灌（_dedup 增补第 5 条）；两个调用点分别传"上一章章末基线"（拆场景失败处=本章开始基线）与"本章场景累计基线"（场景重试耗尽处）。
  4. **根因4（死亡-扣次因果缺校验）**：方向校验只查"回退/回升"（b<a/b>a），"死亡后未变化（1→1）/超扣（2→0）"不触发。修复：chapter_state 新增 `death_signals`（显式死亡表达词表 + 否定/比喻语境排除正则）+ `panel_causal_violations`（死亡 → 次数期望=基线-1（子时重置 0→N 后按 N-1）、蚀化期望=基线+1；末值==基线→未递减/未递增，末值<期望→超扣；无死亡或空基线不校验防误伤）；场景防线 4 并联（死亡信号=正文 OR 事件层 event/conflict/final_state 拼串，覆盖"很冷。然后是黑"式隐式死亡），重试稿复检同并联；回退整章同套校验。
  5. **根因5（蚀痕位置混乱）**：BookMemory 的 canon.mechanism_baseline 从未被程序写入（恒 {}），world.mechanism_baseline 只被 quality._rewrite_span 直接拼字符串。修复：book_memory 新增 `sync_baseline(world)`（world.mechanism_baseline 同步进 canon，幂等、空/None/关闭跳过、失败静默）+ `baseline_block()`（仅机制基线注入块，场景层用）；stages.write() 开头每章 `_mem.sync_baseline(world)`；5 处 scene_prompt 调用点加 `book_canon` 参数注入。
  6. **根因6（_fix_of 关键词错配）**：分支顺序修正——「重演/循环/两版本/多版本」提前到「机制数字/面板/蚀化」之前（原机制数字分支在前，v6 两条纪律 rule 错标根因）；quality._memory_block 数字 issue 挑选同步收紧为 type 精确匹配（"机制数字" or "蚀化"，去掉 `"面板" in detail`）。
  7. **根因7（成本防护跨章累计）**：guard 新增 `reset_scene_counts()`（只清 _scene_counts，保留全局 loop/token 上限）；write() 开头每章重置（v6 第 4-7 章 review 25 次被拦致一致性校验全停的修复）。
  8. **根因8（canon 基线注入位置）**：templates.scene_prompt 新增 `book_canon=""` 参数，渲染 `book_canon_block`（book_pitfalls_block 之后、写作要求之前）。
- 验证：新增/更新测试 15 用例（test_book_memory +5：_fix_of 优先级×2、sync_baseline×2、baseline_block×1；test_panel_direction +10：death_signals 命中/排除、因果递减合法/未递减/超扣/无死亡跳过/递增合法/未递增/子时重置/无基线跳过；test_agent_guard +1：reset_scene_counts 只清场景计数）；专项 54 绿；**全量 549 全绿**（P0-7 基线 534 + 15 新增）。
- 待办：验证通过后触发第七轮真实重生成（先手工备份 v6 稿到新目录——regen 脚本 BACKUP 跳过已存在备份目录；双验证：节拍检测器+逐章通读）→ 数字链验收（蚀化 97→98→99→100→100→100→101 严格递增、次数死亡必扣、蚀痕位置全程遵守"三十七寸距心一寸"基线、事件级重演 0 章）→ 评估签约 → 达标后 devtest→master 合并；P0-8 提交（feat 分支不推远程）待用户放行；P0-2 语义指纹并联留待后续；master 回退（5190063）是否 push origin/master 未定。



## 2026-10-10 生成端第七轮（P0-10）：记忆+多agent 混合双环方案（9 项，工作区未提交）
- 背景：v7 真实重生成双验证（节拍检测器 + 逐章通读，通读为准）评估**未达签约**，阻塞项切换为 3 个：ch5 瑶光界药铺场景三重拼接（同一「进店→诊脉→试探→给药包→离开」写 3 遍约 2900 字注水）、ch3「你不对劲→命大」对峙对话循环 4 次、蚀化跨章口径不一致（ch6 仍 97%，未反映 ch1/3/4 三次死亡；章内「第 98 次蚀化满」自相矛盾）。用户提出"用记忆系统 + 多 agent 架构自动迭代、拆分更合理、重写更合理规避"，结论：概率层（记忆+多agent 可迭代学习）能规避 2/3，第 3 个（机制数字）必须留确定性兜底——依据四轮实证：v4 重写把拼接写得更严重、v5/v6/v7 自校验重写各 3 轮均未检出 ch5 三重拼接（纯 LLM 自评链路对同构场景检出率结构性不足）。用户拍板"按混合方案能加的都加上"。
- 决策与实现（feat/scene-text-anti-repeat 工作区，未提交）：
  1. **④机制状态账本 + 确定性锚（治蚀化跨章口径）**：book_memory 新增顶层 `state` 账本（chapters 死亡次数/蚀化/蚀痕 + erosion_start）；`record_chapter_state`（章末零 LLM 入账，同章覆盖、上限 200、首次从 canon 基线解析 erosion_start）、`cumulative_deaths`（代码求和）、`erosion_anchor`（确定性数字锚：蚀化 = 起始基线 + 累计死亡、蚀痕寸数 = 37 + 累计死亡，注入拆场景/场景/重写 prompt，模型禁止自造口径）；chapter_state 新增 `death_count`（死亡结算计数，相邻 50 字合并，**修复 last_end=-1 初值 bug——此前第一条死亡恒被吞**）+ `erosion_cumulative_check`（正文蚀化峰值必须 == 起始 + 累计死亡，低于=未推进/高于=超发；无基线/无数字/无死亡不校验）；quality.check 新增 2.8c 蚀化累计校验（大改 -30）。实测：修复后 canon 基线全量同步、erosion_anchor(6)=「蚀化当前应为 100%」正确。
  2. **⑥蚀化累计确定性校验（零 LLM 兜底）**：quality 2.8c 用 memory 累计死亡 + death_count 双源计算期望值，正文不符 → 重写回灌（LLM/记忆不参与计算，机制数字永远代码说了算）。
  3. **⑤跨场景同构节拍指纹（治 ch5 三重拼接）**：beats 新增 `_beats_scene_isomorphic`（LCS≥3 拍 + 占空比 0.8 + 非通用拍≥2，通用拍集合=离场/对峙/复活/死亡/挡死/盘问/失控）；stages 新增 `scene_beat_ledger`（历史场景序列全集，每章重置）并联到防线判定与重试稿复检。实测 v7 ch5「诊脉→药包→离场」3 拍 + 非通用拍 2 → 命中。
  4. **①拆场景评审 agent（治拆分同构）**：stages 新增 `scene_split_review_prompt` + `_scene_split_review`（temperature=0.2、max_tokens=800、失败降级空）+ `_merge_duplicate_events`（确定性合并：保留 idx 小者、删 idx 大者、删引用被删 id 的场景）；接入点 `_expand_scene_event_refs` 后、`_validate_scene_plan` 前，仅 `not derived_events and len(events_list)>=2` 调用（派生模式 desc 是骨架原文，合并会破坏 id 对应 → 不合并）。
  5. **②重写前诊断 agent（治 v4 教训：重写越改越差）**：quality 新增 `rewrite_diagnose_prompt` + `_diagnose_rewrite`（独立审稿判多稿拼接/对话循环，输出 {findings:[{type,start,end,action,reason}]}，失败降级空）+ `_diag_block`（转「删除 [s:e] 的重复版本」确定性指令）；`_rewrite_whole` 在 mem_block 之后、context_block 之前注入。
  6. **③读者评审专项 + 记忆注入**：reader_review_prompt 重写——新增 `book_pitfalls` 参数（注入本作历史错误）+ 三项专项检查（6.同构场景/7.对话循环/8.机制数字口径，各带例证与 level 要求）；ReaderReviewer.__init__ 新增 `memory=None`，review 传 `pitfall_block(top_n, min_count=1)`；stages 读者审校调用点传 memory。
  7. **关键根因修复（sync_baseline 对象兼容）**：实测 `isinstance(wb, dict)` 对 `world.mechanism_baseline`（MechanismBaseline pydantic 对象）恒 False → **基线从未同步进 canon（book_memory.json canon.mechanism_baseline 恒空）**——这正是此前"plan_re 为什么少了蚀化"排查的答案（P0-8 修复未真正生效）。修复：`hasattr(wb, "to_dict") → wb.to_dict()`；蚀痕基线兼容英文键 scar_current/scar_start。
  8. **⑦v7 三问题入记忆种子（迭代闭环起点）**：book_memory.json 直接落账——pitfalls 新增「规划-同构场景拆多遍」「机制数字-蚀化跨章口径冲突」2 条（count=1/last_ch=7/带 fix 与 example）+ 对话循环并入既有「写作-对话轮重复（循环拼接）」count+1；state 账本补全 7 章死亡次数（ch1/3/4/7 各 1、ch2/5/6 各 0，累计 4 次 → 蚀化 101%，符合"永久累计不重置"）。闭环：种子 → 每轮自动入账 → 评审注入 → 新变体由 ⑤⑥ 确定性兜底 → 人工确认补记忆。
- 边界（硬约束）：机制数字（蚀化/蚀痕/次数）永远是代码确定性计算（⑥/④），LLM/记忆只做软约束与提示注入；⑤同构指纹与 ⑥累计校验零 LLM；新增 LLM 成本 = 每章拆场景评审 1 次 + 重写前诊断 1 次（仅重写时触发）。
- 验证：新增 19 用例（test_book_memory +6：对象基线同步/状态入账/累计/锚/中文数字解析；test_panel_direction +5：蚀化累计低/合法/超发/跳过/死亡计数；新文件 test_scene_iso.py 8 用例：同构命中/通用拍不误判/短序列/合并函数/评审解析/降级/诊断解析/降级）；两个既有用例适配（test_panel_violation 序列 4→6 含评审、test_scene_plan_align 序列 4→5 含评审）；**全量 568 全绿**（P0-8 基线 549 + P0-10 新增 19）。待办：第八轮真实重生成（先手工备份 v7 稿到 backup_v7_... 新目录——regen 脚本 BACKUP 跳过已存在目录）→ 双验证（节拍检测器 + 逐章通读，通读为准）→ 数字链验收（蚀化跨章严格递增=起始+累计、次数死亡必扣、蚀痕位置遵守基线、事件级重演 0 章）→ 评估签约 → 达标后 devtest→master 合并；P0-10 提交（feat 分支不推远程）待用户放行。

## 2026-10-10 P0-14 生成端账本根因修复（v8 未达标三项：ch1 三重拼接 / 数字链系统性失序 / ch2 面板回升 + ch7 死而不结算）
- 背景：v8 真实重生成双验证未达签约，三项阻塞定位：①ch1「断墙遇灰卒」三重拼接（约 2900 字注水，⑤ 跨场景指纹管不了章内、旧 ratio 0.8 漏检）；②数字链系统性失序——账本 cumulative_deaths 3→4→7→7→8→11→11 vs 通读实际 1→2→4→6→6→6→7，ch5/ch6 蚀化 106/108 虚高（自洽地错）；③ch2 面板回升 + ch7 死而不结算。用户对草案逐条纠正：**不做扩展重写（防重于修，扩展后重写浪费 token）；死亡判定要素是这本书的机制（面板/复活/蚀化三联），不做成通用硬编码，应书级个性化；账本不准先查根因，不做个性化处理掩盖；字数治理——质量过关就不需要整章回退重写**。
- 根因（v8 代码实证）：
  1. **账本被词面计数污染**：旧 `death_count` 词面匹配三类实证污染——ch1 重复段落多计（"死了"多次命中）、ch4/ch7 隐式死亡漏计（"呼吸断了""尸体被翻过来"）、ch5/ch6 自述面板误计（"累计死亡次数：3/11"含"死亡"词被当成结算事件）。
  2. **锚正反馈放大**：旧 `erosion_anchor` = 起始 97 + 被污染累计 → 锚虚高（106/108）→ 模型忠实跟随 → ⑥校验验证的是"模型是否跟随锚"而非"锚是否正确"——确定性被计数错误污染成确定性错误。
  3. **章内同构（段落级）不在 ⑤（跨场景）覆盖范围**：v8 ch1 场景 2/3 占比 ~0.6-0.75 低于旧 0.8 判据漏检。
  4. **字数超限触发整章回退重写**：历轮实证重写越改越差、v8 ch5/ch7 字数超但质量好，纯浪费 token。
- 决策（四条全部落地）：
  1. **账本蚀化差反推（治根因，非个性化掩盖）**：stages 章末入账 deaths 改为「本章末蚀化 - 上一章末蚀化」（无上一章记 0，蚀化回落不反推）；`erosion_anchor` 改**链式承接上一章末蚀化**（无账本回落 erosion_start；蚀痕寸数 = 基线寸数 + 链式增量）——杜绝跨章求和口径与正反馈放大；质检 2.8c 改链式校验：期望 = 账本上一章末蚀化 + 本章死亡次数（death_count 已修正），未死亡蚀化必须保持不变（防虚涨/回退），死亡必须 +1（防死而不结算）。
  2. **通用+个性化两套独立死亡标准（记忆闭环，用户二轮纠正）**：死亡判定拆成两套互不污染的独立标准——**通用标准**（`death_signals/death_count`，跨书词面基线：死了/死亡/被杀/复活…+否定/比喻/自述统计排除）与**个性化标准**（`book_death_signals/book_death_count`，本书金手指机制驱动的隐式死亡表达：如《万墟之上》面板结算式死亡"呼吸断了/尸体被翻过来"，由 book_memory 记忆闭环按本书实证写入 `state.extra_death_signals`，独立正则/独立 50 字去重/同样过排除）。**通用词表不得被单本书的词污染**——其他书没有这套机制，无书级信号时个性化标准自动为空=不启用；质检 2.8c 本章死亡次数 = 通用计数 + 个性化计数（各自独立去重，加法合并）；stages 两处防线 = 通用 OR 个性化。通用排除表追加「累计死亡|死亡次数|已死亡|死亡数量|总死亡」（v8 ch5/ch6 自述面板误计根治）+「差点|险些|差一点|差点儿」（将死未死不计）。v8 实证种子「呼吸断了/尸体被翻过来」已入本书账本；后续章质检/人工确认漏检表达 → `add_book_signal` 记忆闭环 → 自动命中。
  3. **⑤判据放宽**：`_SCENE_ISO_RATIO` 0.8→0.6（v8 ch1 部分重复 ratio≈0.6-0.75 漏检根治）；`_SCENE_ISO_LEDGER_MIN`=3（=MIN，3 拍药铺同构仍可命中）；非通用拍≥2 防误判保留。
  4. **字数治理**：回退整章重试循环删除 `_over`（字数超限）硬条件——仅附注展示，不再触发整章重写（用户约束"质量过关就不需要整章回退重写"）。
- 验证：新增 10 用例（test_panel_direction +4：自述统计排除/书级信号命中/extra 空安全；test_book_memory +3：书级信号记忆闭环/去重/空 store；test_scene_iso +2：部分重复命中（v8 ch1 实证型）/占比下界仍不误判）；**两套独立标准重构后**书级用例改为独立函数断言（通用标准不受书级词影响、书级标准空词表=不启用、个性化独立去重/否定排除），排除表补「差点|险些」；**test_panel_violation 用例适配**——P0-10 时该用例拆场景 target_words=2500×2 超 ±10% 校验**实际一直走回退整章字数重试路径**（场景链路从未被真实测过），P0-14 改 target_words=1250×2 使其真正走场景模式（7 = 拆场景+评审+场景1+场景2违规+重试稿+摘要+扩写）；**全量 576 全绿**（P0-10 基线 568 + P0-14 新增 10 - 1 合并适配 + 书级独立断言重构）。待办：第九轮真实重生成（先手工备份 v8 稿到 backup_v8_... 新目录）→ 双验证（节拍检测器 + 逐章通读，通读为准）→ 数字链验收（账本/锚/正文三者一致：链式承接、死亡必+1、未死亡不变）→ 评估签约 → 达标后 devtest→master 合并（需用户放行）。P0-14 提交（feat 分支不推远程）待用户放行。

## 2026-10-10 P0-15/16/17/18 全会话生成端修复包（v9 评估后，用户：P0-17/18 直接实施、P0-15/16 必须通用化不以本书写死）

- 背景：v9（第八轮重生成，P0-14 生效）三大阻塞根治（ch1 三重拼接/数字链系统性失序/ch2 回升+ch7 死而不结算），剩余 6 个阻塞项。用户明确：**P0-15/16 不做本书个性化写死（蚀痕=37+死亡、次数 0 不得复活等均为本书特有），改为以机制基线为唯一事实源的通用方案**。
- 决策（P0-15/16 通用版，chapter_state 新增「机制字段注册表 + 链式账本 + 事件驱动双向」模块）：
  1. **机制字段注册表（closed-world）**：`mechanism_field_registry(baseline)` 从机制基线（panel_format/reset_rule/蚀痕文本）派生「正文面板允许出现的字段」{核心词: 方向}——字段名去通用限定词（今日/距离/累计/剩余…）归一，方向按字段名语义（次数/剩余/余量→decrease；蚀化/进度/蚀痕/寸→increase）；`unknown_mechanism_fields` 检测正文面板行出现注册表外字段（v9 ch3/ch6「累计死亡次数：39/37」幻觉行）即违规——**任何书通用，基线声明什么字段正文只能出现什么字段**。
  2. **链式账本通用化**：`ledger_extract_numbers` 零 LLM 抽取每章各注册字段末值（寸数字段取最大值防「距心脏不到一寸」误覆盖），`record_chapter_state(numbers=...)` 入账 + `chapter_ledger(up_to_ch)` 链式继承（本章未提及字段继承上一章末值——治 v9 ch5 无面板行 → erosion=null 断链）。
  3. **事件驱动双向校验**：`ledger_check(content, prev_ledger, registry, deaths, initial)`——无事件时 decrease/面板 increase 必须与上章末一致（治 ch2 次数 3→2 无依据递减）、寸单位字段只查不回退（允许碎片等合法正向增长）；有事件时 decrease 必减（上章末 0 仍死亡 = 边界违规，治 ch4 次数 0 后仍复活）、increase 必 ≥ 上章末 + 死亡数。prev 缺失回落基线初始值。质检 2.8c 叠加：未知字段违规（白名单）+ ledger_check 全字段链式。
  4. **生成端注入**：场景防线并入未知字段检测（带错重试）；scene_prompt 注入「机制面板字段白名单 + 次数/剩余类字段为 0 时本章不得再安排死亡」边界规则。
- 决策（P0-17，大纲事件偏离）：
  1. **拆场景核心事件覆盖校验**：`_validate_scene_plan(core_event=...)` 将 core_event 分段（→，。；；），每段与事件清单并集做 2 字以上最长公共子串匹配，命中段 ≥60% 才通过（整数不等式 hit*10 ≥ len*6 表达，2 段 1 命中=50% 判未覆盖——v9 ch6 大纲「忘忧阁密报」被替换为「不问阁」即此类），未覆盖回退整章。
  2. **质检兜底 2.8d**：`_core_event_missing(content, core_event)` 正文 vs 大纲核心事件同法校验（60% 阈值），命中不足 → 大改触发整章级重写；ensure_quality/check 透传 core_event。
  3. **摘要一致性**：模型自带摘要与正文 2-gram 命中率 <0.35 = 摘要含正文完全没有的表述（大纲残留专有名词）→ 基于正文重生成摘要（治 v9 ch6 摘要写「忘忧阁密报」正文是不问阁）；摘要 <8 字跳过（无统计意义防误触发）。
- 决策（P0-18，超长与注水）：
  1. **单场景字数上限治理**：场景正文超 target_words×1.5 且非整句复读型注水（唯一 2-gram 占比 ≥15%）→ `_slim_scene` 精简重写 1 次（保留剧情/数字/伏笔、删重复措辞/注水对话/无效描写，压到 target×1.2）；仍超保留（不做整章回退——用户约束"质量过关就不需要整章回退重写"）。整句复读型注水（"正文"×N）由节拍防线/质检 2.7 处理，slim 不浪费 token。
- 验证：新增 tests/test_mechanism_registry.py 28 用例（注册表派生/方向/停用词归一/未知字段拦截/末值抽取含寸 max/中文数字/链式四象限：无事件递减、无事件寸回退、无事件寸合法增长、次数 0 死亡边界、死亡递增/未递增/prev 回落初始/core_event 覆盖 60% 阈值三态/拆场景门/摘要命中率三态）；既有测试适配：test_chapter_flow 两处 ensure_quality fake 签名补 core_event、slim 对测试重复构造文本跳过（_is_repetitive_text 前置）保持调用数不变；**全量 604 全绿**（576 + 28）。待办：第九轮真实重生成（先手工备份 v9 稿到 backup_v9_... 新目录）→ 双验证（节拍检测器 + 逐章通读）→ 数字链验收（注册字段账本/锚/正文一致）→ 评估签约 → 达标后 devtest→master 合并（需用户放行）。P0-15~18 提交（feat 分支不推远程）待用户放行。

## 2026-10-10 v10 第九轮重生成实证：回退整章路径数字防线全线失效 + 三处修复

- 背景：P0-15~18 提交（d41245f）后触发第九轮真实重生成（v10，日志 regen_ch1_7_v10.log）。生成端新防线大量生效：slim 精简 ×5（ch1 场景1 1909→970、ch3 场景3 866→733、ch6 场景3 1002→912 等）、摘要一致性重生成 ×2（ch3/ch5）、机制数字重试 ×5、回退整章 ×8、拆场景 target_words ±10% 回退 ×1。各章字数 2564/2377/2895/5050/2748/4310/2750。节拍检测器：ch1「次数 2→3」（子时重置误报，正文有重置声明）、ch3/ch6「0→39/37」异常——**ch3「累计死亡次数：39。」ch6「累计死亡次数：37」幻觉行仍在**。
- 实证根因（关键）：ch3/ch6 均走**回退整章路径**（场景节拍重演耗尽）→ `_generate_chapter_scenes` 内 scene_state_acc 为空 → write() 的 `_bl={}` → **质检 check baseline=None → 2.8c 条件 `if baseline and self.memory` 整段跳过**（含 P0-15 未知字段白名单 + ledger_check 全字段链式校验）→ 幻觉行无人拦截；且回退/普通整章 prompt 无字段白名单预防（场景循环有，整章路径没有）——**"回退路径 = 数字防线黑洞"**。
- 决策（三处修复，2026-10-10）：
  1. **质检 2.8c 去 baseline 依赖**：条件改为 `if self.memory is not None`（注册表/账本从 canon/state 自取）；区块内 2.8b 方向校验单独保留 `if baseline`（需场景级基线才可判）。v10 实证注明。
  2. **死亡次数口径统一为账本口径**：2.8c 的 `_cumu` 从「词面 death_count 两标准合并」改为「本章末蚀化 − 上一章末蚀化」（与 stages 账本入账同口径）——词面计数因复活/死于等表达多词命中虚高（v10 ch3 实际死 2 次词面计 6），导致 increase 字段「应 ≥ prev+6」误报合法 98→100。
  3. **寸字段有事件增量豁免**：`ledger_check` 有事件时 increase 校验对寸字段豁免（增量依赖机制基线自由文本——碎片/外力/剩余描述均可合法影响寸数，无法通用推导「应 ≥ prev+deaths」）；无事件寸回退检查保留（治 v9 ch4 四十一寸→ch5 三十七寸矛盾）。
  4. **回退/普通整章路径注入面板白名单**：stages 新增 `_panel_whitelist_block()` 方法（从 memory canon 注册表派生字段白名单 + 次数 0 边界规则），`_fallback_whole_chapter` 与普通整章 `chapter_prompt` 分支均注入；回退整章重试校验 `_viols` 循环条件从 `if _text and panel_baseline` 放宽为 `if _text`（注册表未知字段在无面板基线时也拦截）；场景循环白名单块改为复用该方法。
- 验证：smoke 脚本实证 v10 ch3/ch6 正文「累计死亡次数：39/37」现被 unknown_mechanism_fields 拦截、ch3 合法 98→100 不再误报；tests/test_mechanism_registry.py 新增 TestV10Regression 5 用例（无 baseline 未知字段仍拦/蚀化差口径/合法蚀化无误报/寸无事件回退仍拦/白名单块内容）；**全量 609 全绿**。待办：提交本批修复（feat 分支不推远程）→ 手工备份 v10 稿到 backup_v10_... → 第十轮真实重生成（v11）→ 双验证重点验收 ch3/ch6 幻觉行消除 + 账本 nums 不含「死亡次数」键 → 评估签约。

## 2026-10-11 情绪描写与场景白描平衡调整（feat/emotion-writing-balance，未合并）

- 背景：用户反馈生成正文「情绪描写管控过严、情绪描写很少，场景白描太多」。调研（外部：余华/福克纳《沃许》讲法、PolyProse、Story Grid、SelfPubHub、番茄作家课堂、亵渎/荷花淀范例；内部：番茄写作指南原文、AI小说防同质化规则源文档）结论：① show don't tell 是工具非铁律，"只展示不直白"绝对化是新手误读，telling 有清晰（复杂情绪）/强调（情绪顶点）/节奏控制三大正当功能；② 优秀场景描写=景随人动+情绪染色+只挑 2-3 个核心细节+环境承担叙事功能（心理镜像/推动情节/留白暗示），白描不是景物罗列；③ 本管线 6 处「禁止抽象情绪词」绝对禁令源头为 AI小说防同质化规则源文档「只展示不直白叙述」，蒸馏进模板时被放大；番茄指南原文其实只有「精准描写、删无关环境描写」（无情绪词禁令）。
- 决策（13 处提示词修改，feat/emotion-writing-balance 分支，未合并、未推远程）：
  1. writer.md ×4：「怎么写」改「情绪演为主、说为辅」；「情绪与内心」允许关键节点直接点破（点破处必须紧跟动作/生理反应）；「输出纪律3」改「情绪词少而准」；「反例对照」增「一句点破+生理/记忆支撑」好例与「纯景物白描」差例。
  2. stylist.md ×1：「克制是高级」补「关键情绪节点允许一句点破，让情绪被读者明确接住」。
  3. templates.py ×8：`_de_ai_rules` #6 / `_writing_constraints` #6 / `_anti_similarity_system` core #4 / `_character_depth_rules` #3 / `_fanqie_rules` F / `chapter_prompt` 4.5 全部由「禁止抽象情绪词/只展示不直白/删除抽象情绪词」改为「演为主、说为辅，点破处必须紧跟动作/生理反应，禁连发堆砌与无画面抒情」；`_writing_constraints` 新增 #8「场景白描克制」（景随人动、挑 2-3 个细节、环境=人物心境镜子、禁大段客观白描，纯景物铺陈单章占比压到最低）；`_fanqie_rules` 语言排版 5 补「禁止大段客观景物/陈设/天气白描，环境必须带情绪功能」。
- 边界：不动 ai_taste.py 确定性扣分器（只扣分不阻断，其「AI痕迹-情绪词直白」issue 类型有测试断言）；不改 skills_library 源文档与 DB 技能（平台技能经 sync 蒸馏注入，如需消除同源禁令可后续单独评估）；写作约束开关 de_ai/anti_similarity/character_depth/fanqie_style 默认 True，改动覆盖全部新项目。
- 验证：定向回归 67 绿（test_writing_constraints/test_chapter_flow/test_output_discipline/test_book_memory/test_ai_taste）；全量 612 绿；渲染核对 _writing_constraints/_de_ai/_anti_similarity/_fanqie/_character_depth 均无旧禁令残留。

## 2026-10-13 场景粒度优化：场景数由章节字数反推 + 单场景字数下限（feat/scene-granularity）

- 背景：朱雀检测实验（已停）期间用户实测《万墟之上》第1章（2500 字/章）被拆成 4 场景×~625 字。无锚生成时模型靠"死亡-复活-面板循环注水"凑字数，触发节拍重演拦截（`_beat_cycle_issues` / 场景循环防线）→ 回退整章。调研结论（番茄官方单章节奏 / 话本《情节设计密码》/ Adazing / Alice Sudlow 编辑标准 / iWrity）：行业单场景 800-2500 字、一章 1-3 个相关场景；2500 字/章按字数反推应拆 2 场景×~1250 字，而非按大纲拍数拆 4×625——拆得太细必然注水。
- 决策（stages.py，改动保持小函数、未超 AGENTS.md §11.1 红线）：
  1. 新增 `_scene_count_for_words(chapter_words)`：N = clamp(int(chapter_words/1200+0.5), 1, 4)，1200 字/场景为基准；`_derive_events_from_moves(moves, chapter_words=0)` 合并阈值由固定 4 改为 N（因果粘连合并优先，再最短拍合并）；chapter_words<=0 时 N=4，旧调用方行为零变化。
  2. 调用点（`_generate_chapter_scenes`）传入 `cfg.chapter_words`；骨架/校验链路（事件数=场景数、refs==derived_ids、P0-1 账本、core_event 覆盖≥60%）全部天然适配，无需改动。
  3. `_validate_scene_plan` 新增单场景 target_words 下限 = max(800, 章字数×25%)：低于下限 = 拆得太碎（疑似注水）→ 回退整章（整章单场景兜底）。2500 字章 4×625 即被拦，3×833 可通过。
- 测试：新增 tests/test_scene_granularity.py 5 例（字数→场景数映射、真实 ch1 5 拍@2500→2 事件且死亡-复活同事件、旧行为兼容 5 拍→4 事件、拍数≤N 不合并、4×625 校验拦截）；同步更新编码旧行为的断言（test_scene_plan_align 派生链路 4→2 事件×1250、test_scene_anti_repeat 拆场景 JSON 与调用计数、test_teardowns 题材计数避开拆书入库占用的题材改科幻/军事）。全量 pytest 632 绿。
- 验证：重生成《万墟之上》第1章（大纲原文 5 拍 → 新逻辑 2 事件 → 2 场景×~1250 字）→ 质检通过产出新稿（验收结果见分支交付说明）。
- **验证结果（2026-10-14 用户朱雀复测）**：2 场景粒度版（2876 字）AI 特征 76.86%（旧基线）→ 72.45%（v13 无锚）→ 90.5%（锚版）→ 81.76%（新无锚）→ **18.25%（2场景粒度版）**，疑似 AI 升至 81.75%，人工特征仍 0%。结论：①朱雀"AI 特征"分主要由内容层重复模式（死亡-复活-面板循环注水）驱动，与语言层关系不大——只改场景粒度、未动语言层即降 58.6pp；②人工特征 0% 是独立的语言层问题，内容层干净后可再单独做语言层干预（个人统计指纹锚等），避免被内容层污染掩盖。
- 边界：朱雀 AI 检测方向已被用户叫停，本次优化只解决"拆得太碎导致节拍循环注水"的内容层问题；不动 ai_taste.py 软扣分器、不动题材锚注入（teardown_style_anchor 保持 False）。
- **归因实验闭环（2026-10-14 锚+新粒度复测）**：为验证"锚版 AI 特征 90.5% 高是锚的问题还是旧拆分的问题"，用同款题材锚（玄幻 3 条，2414 字符）+ 新场景粒度重生成第1章：两次正常质检生成均被自校验拦截（16/17 个问题起步，3 轮重写后仍 4-8 个问题，全部重复类：同句复读/同事件循环重演/机制数字方向违规），无成品；第三次临时关闭 quality_check 强制落盘 4119 字锚稿（超目标 65%，"面板"出现 4 次 vs 无锚版 1 次，场景 2 三次生成全部触发防重演拦截）。用户朱雀复测：**AI 特征 55.01%、疑似 AI 44.99%、人工 0%**。归因结论：①AI 特征 = 内容层重复模式主开关，场景粒度是主变量（旧拆分 81.76%→新拆分 18.25%）；②题材锚是独立负面变量（同为新拆分：无锚 18.25% vs 有锚 55.01%，+36.76pp），且锚诱发重复循环、推高字数，属"借鉴语感"提示词的副作用；③决策：新书/续写默认 `teardown_style_anchor=False`，保留新场景粒度；如要用锚需重构注入方式（只借鉴断句节奏、禁止模仿循环结构），独立 REQ 未做。
- **状态差修复（2026-10-14，feat/teardown-anchor-default-off）**：真人味调研发现"决策默认 False"未落代码——`novel_gen/config.py` FEATURE_FLAGS `teardown_style_anchor` 默认值仍 True、`webapp/core.py` getattr 兜底 True，新书未显式配置时题材锚仍默认开启（已随第十七次发版上线）。本次改两处默认 False + 新增守护测试 `test_teardown_style_anchor_default_off`；原依赖默认 True 的 4 个注入测试改显式 `cfg.teardown_style_anchor = True` 保留开启行为覆盖。全量 pytest 633 绿。

## 2026-10-12 AGENTS.md 新增 §11「代码开发规范」（治高耦合 / 大文件 / 代码数据不分离）

- 背景：项目反复踩三类结构性坑——①**功能高耦合**（跨模块可变状态散落、'建了能力却没接线'、注入位置不对致机制不生效，见 §2 踩坑史多轮"根因=未接线/位置不对"）；②**代码全写在一个大文件**（server.py 2853 行、db.py 1409 行已拆，但当前 stages.py 2228 / templates.py 1784 / quality.py 1306 / core.py 1207 行仍超业界 500 行红线）；③**代码与数据不分离**（大段提示词文本嵌在 templates.py、排除词表/正则/阈值硬编码在 pipeline 各 py，历史已出现"排除词表分家""两套正则不同步""基线未同步进账本"）。
- 决策：AGENTS.md 末尾新增 §11，含 11.1 文件规模红线（150–500 行宜 / >1000 行禁新增须重构 / 单函数 >50 行须拆 / 拆分先行为零变化，沿用 server/db 拆分成功标准）/ 11.2 分层解耦（webapp 强制 router→db→service 三层、db 唯一写 SQL、依赖单向、跨模块状态收敛单一权威源、新能力必须接线验证、YAGNI）/ 11.3 代码数据分离（配置进 env/config/FEATURE_FLAGS、业务数据抽独立模块标唯一来源、提示词放 .md、密钥只进 env、12-Factor 开源标准）/ 11.4 命名风格（PEP8 + 行宽 120 + 绝对导入 + __all__）/ 11.5 评审自查清单；并在 §2 Agent 行为约束新增第 7 条强制引用。文档流程变更，无 pytest 影响。
- 待办（已识别债，非本次范围）：stages.py / templates.py / quality.py / core.py 超红线文件的重构，需单独立会话按 11.1 行为零变化标准推进。
- 补充（同日第二轮，用户"还有其他开发规范问题也一起加"）：§11 扩展新增 6 小节——11.6 异常与降级纪律（降级必须留痕、禁止无日志裸吞——实测全库 292 处 except、历史"`_do` 吞异常排查无日志"、业务禁 print）/ 11.7 幂等与原子写（os.replace+重试防 WinError5、重生成先删产物禁写空壳、结算先落基线、JSON 解析统一健壮封装）/ 11.8 测试纪律（§9.3/9.4 落地：mock provider、占位文本避节拍词、side_effect 敏感）/ 11.9 依赖与安全（锁 httpx<0.28、SQL 参数化 + LIKE %%、密钥不进日志）/ 11.10 并发一致性（禁 locals() 取嵌套函数变量、信号量、金额用整数分/Decimal）/ 11.11 前端规范（组合式 API、api/ 封装、Pinia、dist 不入库）。文档流程变更，无 pytest 影响。

## 2026-10-14 章级设计卡核心字段接线（feat/chapter-design 一期）

- 背景：防御/记忆层完备但"正向创作设计层"缺口。能力评估（精读万墟之上 80 章 + 傩面新书 10 章 world/outline 落盘）修正了任务前提：**theme_proposition/protagonist_arc/character_motives/goldfinger_rules/setting_taboos 已在 `memory.build_context` 强约束/按需区注入**，重复造块只浪费 token；**真·未接线的高价值主源是章级 conflict/emotion（每条 60-150 字优质文本）+ `World.conflict_matrix`（三层冲突标尺）**；`Outline.key_nodes/volume_pacing/emotion_curve` 在两个真实项目里**全是空数组**（根因：pacing.plan_outline 只挂 ArchitectAgent.run_outline，manager.run_outline 复用短路 + _run_expand_outline 都不调它）。
- 决策：①新增 `pipeline/chapter_design.py`（纯 Python 前置、零 LLM），从章级 conflict/emotion + conflict_matrix 派生逐章 A1–A7 设计卡，落 `chapter_design.json`（store 唯一读写、空 data 不落盘、幂等复用）；②注入走 stages.write 的 memory_context 组装区（仿章末钩子块），一处拼接覆盖场景/整章/回退整章三条写章路径，templates.scene_prompt/chapter_prompt 预留 `chapter_design` 一级参数；③新开关 `chapter_design`（默认 True）先注册 FEATURE_FLAGS + NovelConfig；④**修数据源**：`manager._backfill_pacing` 在大纲复用短路 + 扩写落盘前幂等补三字段（借 architect.pacing.plan_outline 这个纯确定性函数，不触发 opening.apply 等副作用）。
- 边界：不重复注入已接线字段；关系模板是 20 种开放指引（非白名单枚举，绝不误杀模板外关系）；后校验（event_ledger 应发生 vs 已消费对账/戏份级别/对白功能弱覆盖）只 logger.warning 留痕，不阻断不重写。
- 测试：新增 `tests/test_chapter_design.py`（16 用例，p0×2/p1×12/p2×1），mock provider 端到端冒烟 ALL PASS（设计卡块真实到达写手 prompt）。全量 pytest：553 passed（本环境 MySQL 不可用，7 个 DB 用例收集期报错 + 13 个 DB 用例 1045 失败，均已 stash 验证为 clean checkout 预先存在的环境问题，与本次改动无关）。停在 feat/chapter-design，未合并。

## 2026-10-14 二期：读者认知账本 + 伏笔承诺状态机 + 爽点配额（feat/reader-ledger，未合并）

- 背景：生成链路已有防重演/面板数字闭环/记忆四账本/质量防线，但缺「读者体验层」约束——写手不知道读者此刻已知什么/在等什么/被误导成什么；伏笔只有世界阶段设计时校验（validator 只埋不收 fatal），进入逐章后 `buried_ch/reveal_ch` 是死数据，无临期提醒/超时告警/回收确认；爽文「3 章一小爽」无写章时节奏缺口提醒。
- 决策（novel_gen/pipeline/reader_ledger.py 单模块三内聚类，零 LLM 确定性）：
  1. **三块账本收敛为单代码模块 + 三个独立落盘 JSON**（YAGNI：共享注入/落盘/降级逻辑故不拆三 py；落盘独立保持单一职责）：`reader_knowledge.json`（known 已揭/reveal_ch<=当前章、waiting 伏笔挂起 buried<=n<reveal、misbelief 反转章前 r-2..r-1 误认窗口）/ `foreshadow_commitment.json`（planted→due_soon 临期=reveal-lead<=n<reveal / overdue 超时=n>reveal 未回收 / revealed 回收）/ `payoff_quota.json`（题材 power=爽文 3/10/15、lite=非爽文 5/15/25，基于 outline emotion/tag/key_nodes 校验章级节奏，未达注入软提示）。
  2. **回收检测确定性**：reveal_ch<=当前章 且本章正文命中伏笔 name 或 content 子串 → revealed；检测不到保留原状态（注入层按 overdue 软提醒，**不强行标记 revealed 防假回收**）。`_fs_blob` 拼接空格不可用作整串子串，改为 name/content 分别命中。
  3. **注入体积分章命中 + 上限裁剪**（仿 setting_cards）：known/waiting≤6、misbelief≤2、临期/超时≤6、爽点缺口≤3 行；空 actionable 整块不出现（防 token 膨胀）。注入点 stages.write 拼 outline 块后追加 memory_context，场景级与整章两路径同吃。
  4. **接线**：manager 写章循环 `_update_reveal_status` 旁加 `_update_reader_ledger(no)` 章末推进；FEATURE_FLAGS 注册 `reader_ledger`(总开关)/`foreshadow_lead`(=3)/`payoff_quota`，NovelConfig 同名字段。题材配额表 GENRE_PROFILE 独立常量模块（§11.3 业务数据与代码分离），未命中题材回落 power。
  5. **降级**：总开关 False 不读不写不注入；任一账本异常 logger.exception 留痕、注入空串、不阻塞；回收/配额失败静默降级。
- 踩坑（命名冲突）：ChapterWriter 实例属性 `self._reader_ledger=None` 与同名懒加载方法 `_reader_ledger()` 互相遮蔽（属性 None 覆盖方法 → 调方法报 'NoneType not callable'）；仿 book_memory（属性 `_book_memory`/方法 `_memory` 不同名）修正为属性 `_reader_ledger` / 方法 `_reader_ledger_obj`。
- 验证：新增 tests/test_reader_ledger.py 17 用例（p0 端到端接线×2 / p1 三态+状态机+回收检测+配额×11 / p2 边界降级×4）全绿；相关回归 68 绿（test_setting_cards/test_book_memory/test_reveal_tracking/test_feature_flags/test_schemas/test_memory_direct_read/test_p0_wiring）；全量离线 554 过，13 失败均为缺实时 MySQL（pymysql root@localhost 拒绝）/webapp 依赖，与本次改动无关。停在 feat/reader-ledger 分支，不合并 devtest/master（三期并行，由组织者串行协调）。

## 2026-10-14 三期产品化：Beat 覆盖度门禁 / 场景体检 / 三分支续写 / 思维链可视化

- 背景：墨造已有 beats.py 节拍检测、quality.py 质检、StageGate 门禁，但都是生成内部防线，用户不可感知。本期把"检测能力"产品化（对标 Sudowrite Scene Inspector）。分支 feat/beat-gate（基于 devtest fe0342d）。
- 决策（F1 Beat 覆盖度门禁）：
  - 权威来源 = 大纲 `Outline.chapters[no-1].character_moves`（→/。；;切拍），空则回退 core_event；**不做 embedding 语义指纹**（P0-2 留待后续），用**字符 bigram 命中**（拍内有信息量 bigram ≥50% 出现在正文即视为覆盖），对代词替换/换写鲁棒。
  - 分级：coverage≥0.6 pass / [0.4,0.6) warn / <0.4 fail / 无计划拍 skip。纯确定性零 LLM，可复现。
  - **门禁只"检测+记录+提示"，不新起独立 LLM 重生成循环**——重写统一走既有 quality.ensure_quality（已≤3轮+best-version兜底），防 token 成本失控；结果落 `beat_gate.json`（按章幂等覆盖，唯一权威源）。mock provider/开关关/无拍静默跳过。
  - 接线点：`stages.ChapterWriter.write()` 在 `save_chapter` 之后调用 `beat_gate.assess_chapter`。
- 决策（F2 场景体检）：`scene_inspector.inspect_chapter` 读时实时计算、零新 LLM，复用 `beats._beat_cycle_issues` + scenes.json + 字数/承接/钩子确定性维度，输出 `[{dim,level,detail,suggestion}]`；LLM 补充建议本期不做（默认关，降频留后续）。单维度 try 隔离降级。
- 决策（F3 三分支续写）：一次 LLM 调用出 3 方向（escalate/foreshadow/twist）；`apply-branch` **默认预览模式**——只把选中草稿记 `continue_drafts.json`，绝不自动改写 chapters/*.json（防污染正文、防破坏既有链路），由用户复制粘贴进编辑器。
- 决策（F4 思维链可视化）：只读聚合接口，实时从 outline 计划 + scenes.json 产出 + beat_gate/review.json 校验拼装，零新 LLM；缺产物维度降级为空不报错。
- FEATURE_FLAGS 新注册（review 模块）：beat_gate(True)/beat_gate_min_coverage(0.6)/beat_gate_warn_coverage(0.4)/scene_inspector(True)/chain_of_thought(True)；writing 模块：branch_continue(True)。float 阈值经 `_coerce_feature` 原样透传。
- 新文件：`novel_gen/pipeline/beat_gate.py`、`novel_gen/pipeline/scene_inspector.py`；store 加 beat_gate/continue_drafts 读写；路由并入 webapp/routers/projects_chapters.py（4 个新接口，按域不回写 core/server）；前端 ToolsPanel 加 3 张卡片（场景体检/三分支续写/生成过程）+ api/tools.js 封装。
- 测试：新增 tests/test_beat_gate.py(8)+test_scene_inspector.py(4)+test_beat_gate_wiring.py(2)，p1/p2 marker；全量 647 passed（633 基线 + 14 新增）零回归。前端 npm install + build 通过，dist 引用一致、正确 gitignore。
- 边界：停在 feat/beat-gate 分支，不合并（三期并行，组织者串行协调）。
