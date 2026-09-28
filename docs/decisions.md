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
| 2026-09-28 | pymysql 参数化 SQL 中 LIKE 字面量 `%` 未转义（如 `LIKE '管理员手动充值%'`），被误当作参数占位符，抛 `TypeError: not enough arguments for format string`，导致退款/质量报表接口 500（master 5f532c5 修复） | 字面量 `%` 必须写 `%%`（mogrify 时还原为单 `%`）：`projects.py` refundable_amount/create_refund_request、`traces.py` quality_report churn 查询 3 处已修；新增 test_refund.py（P0）+ quality_report 2 个 P2 回归（模拟 mogrify 验证），全量 190 全绿。**同类隐患**：`users.py` admin_stats、`base.py` BYOK 迁移的 LIKE 目前无参数执行（pymysql 不传参不格式化 `%`）安全，将来加参数前必须转义 |
| 2026-09-28 | 大纲/世界观生成 JSON 被 max_tokens=4096 截断导致 `Unterminated string` 解析失败 | 根因：`chat_json` 调用未传 max_tokens，走 client 默认 4096，但完整大纲 JSON（总纲+分卷+每章 8 字段）轻松超限，重试 3 次每次都在同一 token 上限处截断。修复：① 大纲/世界观/扩写/续写共 7 处 `chat_json` 调用显式传 `max_tokens=8192`；② `chat_json` 重试逻辑增加截断检测（`_is_json_truncation`：pos>0 且错误消息含 Unterminated/Expecting 等信号），自动 +4096 增大 token 上限（封顶 16384）并重试；③ pos==0 的自然语言开头错误不增大 token（属格式问题非截断）。新增 test_chat_json_truncation.py 15 用例，全量 205 全绿 |
| 2026-09-28 | server.py 拆 core+routers（e1c68d1）把 `REGEN_DAILY_LIMIT = 10` 常量定义弄丢、仅保留使用处 → `_check_regen_limit` 每次调用抛 NameError（位于 try 块外、无日志），单章重写 rewrite / 从第 N 章重生成 regenerate-from 两个接口稳定 500，且 regen_count.json 永不落盘 | 已在 `webapp/core.py` 恢复常量定义；新增 tests/test_regen_limit.py 5 个 P1 用例（正常计数/超限 429/损坏容错/跨天重置），全量 210 pytest 全绿 + 真实接口重写 200。**教训：路由拆分回归不能只比对 openapi 路由集合，必须覆盖受影响接口的真实调用路径** |
| 2026-09-28 | 章节生成章节间硬切、无铺垫转折（实测《万墟之上》第1章结尾在墟底石门，第2章开头直接切登记大厅）——场景级生成路径缺「承接上一章结尾」约束：split_scenes_prompt 不知道上一章结尾、scene_prompt 无承接指令（普通整章路径反而有）、记忆上下文只注入近章 goal 从不注入大纲 transition（架构师生成的转场衔接被丢弃）、_prev_tail 仅截 120 字 | 修复：① scene_prompt 首场景必须从【上一章节结尾】直接续写、后续场景承接上一场景结尾；② split_scenes_prompt 新增 prev_tail 参数并要求首场景承接；③ memory 新增 L3.5 注入本章 transition（写手必须按架构师转场续写）；④ _prev_tail 截 300 字；⑤ writer.md 新增「章首」通用承接规则。新增 test_chapter_flow.py 5 个 P1 用例，全量 215 全绿，真实接口重写第2章验证承接成功。**决策：所有生成路径（整章/场景级）都必须有明确的章首承接约束；大纲 transition 是衔接设计的唯一权威来源，必须注入写手** |
| 2026-09-28 | 章节正文偏离大纲 core_event 导致时间线错乱（实测《万墟之上》第1章：大纲设计'跟随临时队下墟→灰卒→死而复生→墟口外醒来→混入逃出人群回城'，正文却写成'进入石门阶梯往墟底深处走'；读者看到第1章像下墟中途、第2章（登记被拒）像下墟前，时间线倒置）——写手只收到抽象的 chapter_goal，核心事件 core_event（本章具体发生什么）从未注入，模型自由发挥 | 修复：write() 内部从 outline 取本章 core_event，注入全部三条写手路径（chapter_prompt/split_scenes_prompt/scene_prompt），提示词统一标注【本章核心事件（正文必须完整覆盖，不得偏离或遗漏）】。新增 3 个 P1 用例，全量 218 全绿；真实接口重写第1、2章验证时间线理顺。**决策：写手必须同时收到 goal （方向）与 core_event（事件主线）；正文偏离大纲核心事件的场景视为生成缺陷** |

## 三、文档漂移记录

- **2026-09-21**：修复 `README.md` 目录结构中的过期条目——`novel_gen/cli.py`、`__main__.py` 实际不存在（当前产品入口为 Web 端 `run_web.py`），已删除该行；README 中「快速开始（命令行）/ 命令一览」两节仍描述 `python -m novel_gen ...` CLI 用法，**与现状不符，待确认是否清理**。
- **2026-09-21**：`.gitignore` 现状——`docs/`（需求分析文档）与 `AGENTS.md`（注释为 "AI agent rules (local only)"）均不入库。`docs/architecture.md` / `filemap.md` / `decisions.md` 三件套默认同受 `docs/` 忽略；**若要跨机器复用 AI 知识库，需在 .gitignore 中为其加白名单（如 `!docs/architecture.md` 等），由用户拍板，勿擅自改动**。

## 四、新增决策的写法

追加时按以下模板，保持一行一条、可回溯：

```
| YYYY-MM-DD | <决策一句话> | <原因/影响，含回归验证方式> |
```
