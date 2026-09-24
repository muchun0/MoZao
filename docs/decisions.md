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

## 三、文档漂移记录

- **2026-09-21**：修复 `README.md` 目录结构中的过期条目——`novel_gen/cli.py`、`__main__.py` 实际不存在（当前产品入口为 Web 端 `run_web.py`），已删除该行；README 中「快速开始（命令行）/ 命令一览」两节仍描述 `python -m novel_gen ...` CLI 用法，**与现状不符，待确认是否清理**。
- **2026-09-21**：`.gitignore` 现状——`docs/`（需求分析文档）与 `AGENTS.md`（注释为 "AI agent rules (local only)"）均不入库。`docs/architecture.md` / `filemap.md` / `decisions.md` 三件套默认同受 `docs/` 忽略；**若要跨机器复用 AI 知识库，需在 .gitignore 中为其加白名单（如 `!docs/architecture.md` 等），由用户拍板，勿擅自改动**。

## 四、新增决策的写法

追加时按以下模板，保持一行一条、可回溯：

```
| YYYY-MM-DD | <决策一句话> | <原因/影响，含回归验证方式> |
```
