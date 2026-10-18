# -*- coding: utf-8 -*-
"""
配置层：加载 config.yaml，支持环境变量覆盖 API Key。

设计要点
- 所有可调参数集中在 YAML 配置，不改代码即可调参
- API Key 不写死在代码里：支持配置文件 / 环境变量两级读取
- 章节数由「目标总字数 / 单章字数」自动换算，向上取整
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 内置题材列表（题材可选：允许自定义，不在此列表内也放行）
GENRES = ["玄幻", "仙侠", "都市", "科幻", "历史", "悬疑", "言情", "游戏", "奇幻", "体育", "军事", "自定义"]


# =====================================================================
# 功能开关注册表（唯一事实来源）
# ---------------------------------------------------------------------
# 所有功能开关/可调参数的默认值集中在这里，按功能模块分组。
# 新增开关：1) 在 FEATURE_FLAGS 注册一行；2) NovelConfig 加同名字段（默认值引用
# feature_default(key)）。save_config / load_config 会自动遍历注册表落盘与读取，
# 无需再手改 store 两处持久化逻辑。
# 模块约定：memory=记忆 / writing=正文生成 / review=审校质检 / guard=成本防护
#（guard 子项由 AgentGuardConfig 承载，落盘在 config.json 的 agent_guard 段，不进顶层）
# =====================================================================


@dataclass(frozen=True)
class FeatureFlag:
    module: str          # 功能模块：memory / writing / review / guard
    key: str             # NovelConfig 字段名
    default: object      # 默认值
    desc: str            # 说明


FEATURE_FLAGS: list[FeatureFlag] = [
    # ---- memory：记忆与长文一致性 ----
    FeatureFlag("memory", "memory_window", 5, "生成当前章时回填最近 N 章摘要，控制上下文成本"),
    FeatureFlag("memory", "memory_direct_read_chapters", 1, "前文直读：直读最近 N 章正文（0=关闭，保持纯摘要；P0-1）"),
    FeatureFlag("memory", "memory_direct_read_chars", 1200, "每章直读正文最大字符数（取章节尾部；P0-1；REQ-token优化 P0-5：2000→1200 降输入）"),
    FeatureFlag("memory", "character_state_tracking", True, "每章正文落盘后更新人物动态状态快照（P0-2）"),
    FeatureFlag("memory", "character_state_llm", True, "是否用 LLM 抽取状态细节（False=仅确定性出场记录，零 LLM 成本）"),
    FeatureFlag("memory", "chapter_state_tracking", True, "章间机制/世界状态账本：每章落盘后抽取章末状态（数字/生死/物品/时间），供下一章承接，防数字断链与死而复生（REQ-章内自洽与机制账本）"),
    FeatureFlag("memory", "chapter_state_llm", True, "是否用 LLM 抽取章末/场景状态（False=仅确定性面板数字正则，零 LLM 成本）"),
    FeatureFlag("memory", "lorebook_trim", True, "按章触发注入（Lorebook 式）：预测出场人物，只注入相关角色卡/伏笔，锁定项永不裁剪（P1-3）"),
    FeatureFlag("memory", "scene_state_mode", "auto", "场景间状态抽取模式（REQ-token优化 P0-1）：auto=高质量模式 LLM/普通模式确定性正则；llm=全部 LLM；deterministic=全部确定性正则（零 LLM）"),
    FeatureFlag("memory", "state_extract_frequency", 5, "章末/人物状态 LLM 抽取频率（REQ-token优化 P0-4；超长篇攻坚 S4.3 3→5）：每 N 章抽 1 次 LLM，其余章走确定性；1=每章"),
    FeatureFlag("memory", "setting_cards", True, "分章设定卡索引（REQ-token优化 P1-1）：world 后确定性构建 setting_cards.json，按章只注入相关设定子集，构建失败回退全量注入"),
    FeatureFlag("memory", "scene_delta_inject", True, "场景 prompt 分层注入（REQ-token优化 P1-2）：首场景注入全量记忆，后续场景只注入增量块（已推进状态+人物卡子集+场景大纲），降重复输入"),
    FeatureFlag("memory", "reveal_tracking", True, "揭示执行跟踪：每章检测 world_reveals 真相关键词是否按计划在正文兑现（提前剧透/到点未揭示），落 reveal_status.json，全书完成汇总报告；无关键词旧数据跳过检测"),
    FeatureFlag("memory", "book_memory", True, "小说级记忆系统（P0-7）：质检出问题→踩坑账本→重写/场景 prompt 注入本作历史错误+修复指令，同模式≥2次升级永久纪律，针对本书持续进化；False=关闭（管线行为与 P0-5 一致）"),
    FeatureFlag("memory", "book_memory_top_n", 5, "踩坑账本注入 Top-N 条数（P0-7）：按出现次数×最近频率排序取前 N 条注入重写/场景 prompt"),
    FeatureFlag("memory", "user_memory", True, "用户级记忆库（跨书创作偏好继承）：从用户全部作品蒸馏偏好/跨书纪律/文风锚，新书创建与生成时自动继承注入；False=关闭（不蒸馏不记录）"),
    FeatureFlag("memory", "user_memory_inject_top", 5, "用户级跨书纪律注入 Top-N 条数：按次数×跨书数排序取前 N 条注入正文 prompt"),
    FeatureFlag("memory", "reader_ledger", True, "读者认知账本+伏笔承诺状态机+爽点配额（REQ-reader-ledger）：写章时注入读者已知/在等/误认、临期/超时伏笔收束提醒、爽点节奏缺口提示；False=全部关闭（不读不写不注入）"),
    FeatureFlag("memory", "ledger_stalled", True, "伏笔 stalled 状态（超长篇攻坚 S1.4）：埋后超 N 章仍无推进（未到回收窗口）标记 stalled 并注入提醒，防长文伏笔冷却；False=不标不提醒"),
    FeatureFlag("memory", "ledger_stall_after", 8, "伏笔 stalled 判定阈值（超长篇攻坚 S1.4）：埋后超 N 章无推进即标记 stalled；0=关闭"),
    FeatureFlag("memory", "foreshadow_semantic_check", False, "伏笔语义双路回收判定（超长篇攻坚 S1.4）：字面命中之外的语义推进低频 LLM 核对（默认关，省成本；开启后按 state_extract_frequency 降频）；True=开启"),
    FeatureFlag("memory", "foreshadow_lead", 3, "伏笔临期窗口：距 reveal_ch 提前 N 章开始注入'本章应开始收束该伏笔'（REQ-reader-ledger）"),
    FeatureFlag("memory", "foreshadow_original_text", True, "伏笔原文片段存档（超长篇攻坚 S1.1）：埋设章落盘时确定性抽 1-3 句关键句存 original_excerpt（≤150 token/条），due_soon/overdue 章随收束提醒注入原文块；False=不抽不注入（向后兼容）"),
    FeatureFlag("memory", "chapter_commit", True, "章节原子提交+投影一致性（REQ-借鉴webnovel A/B）：每章收尾把章后入账（人物状态/章末机制/揭示执行/读者账本）收敛为一次统一提交——先落基线、失败留痕、同章幂等，并写 projection_log 供一致性体检；False=回退原分散调用（行为不变）"),
    # ---- writing：正文生成质量 ----
    FeatureFlag("writing", "de_ai", True, "正文生成时注入去AI味写作约束"),
    FeatureFlag("writing", "style_reanchor_every", 5, "周期轻量再锚定（超长篇攻坚 S2.2）：每 N 章把本作高分片段(book_memory goods)作为滚动风格锚注入 system 前段，治文风/语感漂移；0=关闭"),
    FeatureFlag("writing", "style_anchor_rolling", True, "滚动风格锚（超长篇攻坚 S2.2）：用 book_memory goods 最近高分片段做滚动风格源（每章每次生成都可用，周期性注入由 style_reanchor_every 控制）；False=关闭"),
    FeatureFlag("writing", "fixed_style_anchor", False, "固定风格锚（超长篇攻坚 S2.3）：开头1-2章原文+个人旧稿作风格原点、永不替换，与滚动锚双轨；默认False（双锚体系默认关防放大AI特征）；True=开启"),
    FeatureFlag("writing", "style_drift_check", True, "L1 确定性漂移检测（超长篇攻坚 S2.4）：每章量化句长波动系数/口头禅/词汇多样性 TTR/比喻密度，输出漂移分供与前期章基线对比，治几十万字后文风漂移；False=关闭"),
    FeatureFlag("writing", "style_drift_threshold", 60, "文风漂移 L1 检测阈值（超长篇攻坚 S2.4）：漂移分低于此值报'文风漂移-L1'小改提示并轻扣 5 分；0=关闭单章提示（检测仍跑）"),
    FeatureFlag("writing", "anti_similarity", True, "防同质化：常驻System提示词，拒绝套路/有代价/只展示不叙述"),
    FeatureFlag("writing", "character_depth", True, "人物立体化：角色卡一致性 + 选择代价 + 生活体感 + 对话辨识度"),
    FeatureFlag("writing", "fanqie_style", True, "番茄节奏：黄金三章 + 爽点闭环 + 断章 + 移动端排版"),
    FeatureFlag("writing", "high_quality_mode", False, "高质量模式：场景级生成 + 动态模块 + 参数分层，3元/万字"),
    FeatureFlag("writing", "pacing_density", "medium", "爽点密度：low(慢热)/medium(适中)/high(密集)"),
    FeatureFlag("writing", "plot_speed", "medium", "剧情推进速度：slow(细腻)/medium(适中)/fast(快节奏)"),
    FeatureFlag("writing", "action_plan_first", False, "正文前独立生成人物行动表（REQ-token优化 P0-2：默认并入拆场景输出，不再独立调用；True=恢复独立调用）"),
    FeatureFlag("writing", "scene_split", True, "场景级生成：单章目标≥2000 字自动拆场景逐段写（普通模式也可用；P1-2）"),
    FeatureFlag("writing", "scene_plan_align", True, "拆场景规划对齐大纲人物动线（REQ-scene-plan-align）：事件清单从大纲 character_moves 逐拍切分确定性派生，LLM 只做场景切分与细节补充；moves 缺失/派生失败自动回退原 LLM 规划路径"),
    FeatureFlag("writing", "chapter_design", True, "章级设计卡（REQ-chapter-design 一期）：从大纲章级 conflict/emotion + 世界观 conflict_matrix 确定性派生逐章设计卡（冲突核心与升级阶梯/情绪落点/出场角色关系/对白功能），写章前幂等生成并注入正文 prompt 作正向创作指引；False=不生成不注入"),
    FeatureFlag("writing", "polish_hq_only", True, "整章润色仅高质量模式启用（REQ-token优化 P0-3：普通模式跳过润色，省整章输入+输出；False=普通模式也润色）"),
    FeatureFlag("writing", "teardown_style_anchor", False, "拆书库题材文风锚（REQ-双锚风格锚定）：写手阶段注入同题材爆款文风与手法（弱约束，只借鉴语感严禁抄袭）；2026-10-14 朱雀归因实测为独立负面变量（无锚18.25% vs 有锚55.01% AI特征），决策默认关闭"),
    FeatureFlag("writing", "personal_style_anchor", False, "个人旧稿文风锚（REQ-双锚风格锚定 + 超长篇攻坚 S2.1）：有 user_edited 人工修订章节时抽取其风格注入（弱约束）；默认关闭（个人锚无条件注入会引入作者风格但可能放大模板化表达，需显式开启）"),
    FeatureFlag("writing", "payoff_quota", True, "爽点配额（REQ-reader-ledger）：按题材 A/B 校验章级节奏（爽文 3 章小爽/10 章大爽/15 章里程碑），近窗口未达成则注入写章提示（软约束不阻断）"),
    # ---- review：审校质检 ----
    FeatureFlag("review", "editor_review", True, "大纲生成后执行编辑视角质检，不通过则自动修正一次"),
    FeatureFlag("review", "ai_taste_check", True, "AI 痕迹量化检测：成文后按确定性规则给分，低于阈值扣分产出 issue（P1-4）"),
    FeatureFlag("review", "ai_taste_threshold", 60, "AI 痕迹分阈值（0-100），低于则触发扣分（P1-4）"),
    FeatureFlag("review", "final_review", False, "全书终审：整本书写完后的终审编辑（高质量模式默认开启；P2-2）"),
    FeatureFlag("review", "stage_gate", True, "多Agent阶段门禁：每阶段产出后准出校验，不合格自动重生成（REQ 多Agent协作分层优化）"),
    FeatureFlag("review", "stage_gate_retries", 2, "阶段门禁最大自动重生成轮次，超限由总控仲裁放行（REQ 多Agent协作分层优化）"),
    FeatureFlag("review", "consistency_llm_check", False, "章内前后矛盾 LLM 自检（REQ-token优化 P1-3）：默认仅高质量模式开启；普通模式按 consistency_llm_every 降频兜底"),
    FeatureFlag("review", "consistency_llm_every", 3, "章内矛盾 LLM 自检降频（REQ-章内自洽与机制账本·扩展）：普通模式每 N 章抽 1 次（1=每章；高质量模式不受此限）"),
    FeatureFlag("review", "targeted_rewrite", True, "定点重写（REQ-token优化 P1-4）：质检触发重写时只重写问题段落窗口，不整章重出；无定位点退回复核回整章"),
    FeatureFlag("review", "verify_combined", True, "一致性校验合并（REQ-token优化 P1-5）：每 N 章一次调用输出一致性/读者/编辑三类结果，替代 3 连调用"),
    FeatureFlag("review", "verify_every", 5, "一致性校验触发频率（REQ-token优化 P1-5）：每 N 章一次（原每 3 章）"),
    FeatureFlag("review", "style_judge_l3", True, "L3 风格裁判（超长篇攻坚 S2.5）：每 verify_every 章用 LLM 比对最近章 vs 风格原点(开头锚)，输出六维差异；漂移维度≥style_judge_drift_threshold 触发滚动锚刷新；False=关闭(省成本,仅L1确定性漂移)"),
    FeatureFlag("review", "style_judge_drift_threshold", 2, "L3 风格裁判触发阈值（超长篇攻坚 S2.5）：漂移维度数≥N 即判定需再锚定并触发滚动锚刷新"),
    FeatureFlag("review", "outline_rolling_detail", False, "远期大纲粗粒度（超长篇攻坚 S3.6）：beat_gate 分母只算窗口内节点，距当前>outline_far_skip 的远期节点不计入 fail 判定（方向约束）；默认False；True=开启(滚动细化大纲生成为后续项)"),
    FeatureFlag("review", "outline_far_skip", 30, "远期大纲窗口阈值（超长篇攻坚 S3.6）：距当前章>N 的节点视为远期，beat_gate 判定时排除"),
    FeatureFlag("review", "beat_fail_judge", False, "fail 章 LLM 二级复核（超长篇攻坚 S3.4）：仅对 beat_gate=fail 章跑 1 次 pointwise judge 确认未兑现清单（anti-length、judge 异模型族），防误报；返回未兑现清单才进 L3 定点重写；默认 False（启用待自建 20-50 章人工标注集验证一致率>80%）"),
    FeatureFlag("review", "editor_diff_revise", True, "编辑质检修正改 diff 式（REQ-token优化 P1-6）：只输出需改字段，Python 合并回大纲，不重吐全大纲 JSON"),
    FeatureFlag("review", "outline_multi_candidate", True, "P1-3-B 编辑质检多版本候选：审稿未通过时生成 2-3 个不同侧重(结构/人物/钩子)的修订候选存 store，由用户挑选(Sudowrite 式)，而非自动采用单一 diff；False=保持自动修正"),
    # ---- 三期：Beat 覆盖度门禁 / 场景体检 / 思维链可视化（产品化）----
    FeatureFlag("review", "beat_gate", True, "Beat 覆盖度门禁（三期 F1）：正文生成后自检对章计划（大纲 core_event/character_moves）拍序列的覆盖率，分级记录到 beat_gate.json（检测+提示，不独立重生成）"),
    FeatureFlag("review", "beat_gate_min_coverage", 0.6, "Beat 覆盖度通过阈值（三期 F1）：覆盖率≥此值=pass"),
    FeatureFlag("review", "beat_gate_warn_coverage", 0.4, "Beat 覆盖度警告阈值（三期 F1）：覆盖率<此值=fail，[warn,min)=warn"),
    FeatureFlag("review", "beat_loose_match", True, "Beat loose 匹配（REQ-超长篇生成攻坚 S3.2）：覆盖判定时先做去标点/空白归一再匹配，缓解近义改写/标点差异导致的假阴性"),
    FeatureFlag("review", "beat_chapter_type", True, "Beat 章型白名单（REQ-超长篇生成攻坚 S3.2）：正文确定性判定章型（对话/情绪/过渡），对话/情绪章自动降阈值或 skip，治'对话章 0% 被误判 fail'的误伤"),
    FeatureFlag("review", "beat_node_priority", True, "Beat 节点分级（REQ-超长篇生成攻坚 S3.2）：大纲节点 P0/P1/P2 分级，覆盖率分母只算 P0+P1（P2 氛围节点不计入，防'非关键节点拉低覆盖'误伤）；未提供分级数据时默认全 P1 向后兼容"),
    FeatureFlag("review", "pending_commitments", True, "章末承诺台账（REQ-超长篇生成攻坚 S3.1）：beat_gate 后确定性生成四类清单（已兑现/漏兑现/新埋/下章必补）落 pending_commitments.json，下章 chapter_design 注入补偿块；False=不读不写不注入"),
    FeatureFlag("review", "scene_inspector", True, "场景体检开关（三期 F2）：对已生成章节做字数/事件推进/重演/承接/钩子确定性体检并给修复建议"),
    FeatureFlag("review", "chain_of_thought", True, "思维链可视化开关（三期 F4）：聚合计划-产出-校验三段产物为只读视图"),
    FeatureFlag("writing", "branch_continue", True, "三分支续写选择开关（三期 F3）：一次生成 3 个方向（冲突升级/埋伏笔/转折）续写候选，用户选一条再写"),
    # ---- guard：成本防护（AgentGuardConfig 承载，环境变量可覆盖）----
    FeatureFlag("guard", "enabled", True, "AgentGuard 总开关"),
    FeatureFlag("guard", "max_loops", 200, "单任务最大 LLM 调用次数"),
    FeatureFlag("guard", "max_tokens", 2_000_000, "单任务累计 Token 上限"),
    FeatureFlag("guard", "input_check", True, "输入注入检测"),
    FeatureFlag("guard", "output_check", True, "输出基础合规校验"),
    FeatureFlag("guard", "quality_check", True, "章级 ReaderAgent 结果自校验（agent_guard.quality_check）"),
    FeatureFlag("guard", "json_schema_output", True, "JSON 输出约束（超长篇攻坚 S4.3）：chat_json 时 provider 支持则传 response_format json_object，消除解析重试；provider 不支持自动降级 parse_tip 路径"),
    FeatureFlag("guard", "prefix_cache_aligned", True, "前缀缓存对齐（超长篇攻坚 S4.1）：提示词稳定前缀（system+固定设定+风格锚）前置、变化内容置后，使跨调用前缀逐字节一致以命中 DeepSeek 前缀缓存价；并埋 cache_hit 验证命中率"),
    # ---- 门禁质检正向影响（REQ-门禁质检正向影响）：负向检查→正向闭环 ----
    FeatureFlag("review", "compliance_clean_rewrite", True, "P0-1 合规红线删旧稿重写：合规命中（basic_content_check 9 条硬词表）时强制整章重写、prompt 不携带【当前正文】、best 择优跳过合规稿、无健康旧版时不落盘；False=回落现有定点重写/落盘行为"),
    FeatureFlag("review", "compliance_education", True, "P0-2 合规首次教育：本书内某 rule_category 首次命中时，重写 prompt 注入「该内容违反平台合规要求+如何正面改写」教育块（重复命中只重写不重复教育）；False=仅删旧稿重写不教育"),
    FeatureFlag("review", "diag_feedback", True, "P0-3 质检诊断回喂：issue 补 fix_suggestion/expected_gain，重写 prompt 用带建议的 issue_block（reask 非盲重写）；False=回退旧 issue_block 格式（仅 type+detail）"),
    FeatureFlag("review", "good_pool_layer", True, "P0-4 good 账本分层入账：通过即入账（记录层无条件），注入层分示范池(demo)/候选池(cand)；hook>=4 或 score>=85 入 demo；False=回落原「score>=85 且无大改」门槛"),
    FeatureFlag("review", "good_src_isolation", True, "P0-4 good 来源隔离：外部/用户样例直接入 demo，本作自产片段只作风格锚且 demo 中限量；False=不区分来源"),
    FeatureFlag("review", "good_rotate", True, "P0-4 good 采样轮换：每类在近窗口内按 index 轮换选取 + 跨章相似度控制，防永远锁最近几章导致套路重复；False=回落原「每类取最新」"),
    FeatureFlag("review", "style_homogeneity_check", True, "P0-4 手法/句式层同质度度量：跨章某类手法/句式使用率持续上升时产出软提示 issue 并降该类注入权重（防古德哈特）；False=关闭同质度检测"),
    FeatureFlag("review", "hook_positive_anchor", True, "P0-5 追更欲正向锚：hook>=4 的章片段入 good 示范池 + hook_streak 账本累计；False=关闭正向锚与 streak"),
    FeatureFlag("review", "hook_streak_freeze", True, "P0-5 streak 容错：连续达标中断时允许 1 次 freeze（不重置）；False=断签即重置"),
    FeatureFlag("review", "reflexion_note", True, "P1-1 Reflexion 反思笔记：重写 >=2 轮仍未通过时，让 LLM 写一句「错在哪、下次怎么写对」存入 book_memory.reflections，后续章生成注入为先验；False=关闭反思入账（仅记录失败不留反思）"),
    FeatureFlag("review", "enhance_pass", False, "P1-2 StageGate 正向增强旁路：质检通过(无大改)但仍有 warn 级问题或追更欲未满时，可选触发一次 LLM 增强润色(钩子更强/爽点更密/伏笔呼应)，复检通过且分数不降才采纳，否则保留原稿；受 AgentGuard 成本账约束(与重写共用 revise 限额)；默认关控成本，开启才增强"),
    FeatureFlag("review", "suggestion_lib", True, "P1-3 EditorReviewer 建议指令库：编辑审稿建议按手法分类(对话/战斗/情绪/环境/叙事)沉淀进 book_memory.suggestion_lib，写章生成时注入复用；False=建议仅审稿时展示，不入库不注入"),
    FeatureFlag("review", "beat_positive_supply", True, "P1-4 BeatGate 正向供给：missing 拍不仅列「漏了」，同时给「该拍可往哪个方向写」的示范指引(零LLM规则模板)存入 beat_gate，下一章生成时注入兑现；False=beat_gate 仅负向报漏不供给方向"),
    FeatureFlag("review", "repetition_direction", True, "P2-1 RepetitionDetector 正向替换供给：重复类问题命中重写时，除「每事件只写一遍」禁令外，注入「新方向候选」（未兑现承诺/伏笔/场景多样化），让模型知道往哪走；False=重写仅禁重复不给新方向"),
]


def feature_default(key: str) -> object:
    """按 key 取开关默认值（NovelConfig 字段默认值引用，保证单一事实来源）。"""
    for f in FEATURE_FLAGS:
        if f.key == key:
            return f.default
    raise KeyError(f"FEATURE_FLAGS 中未注册开关: {key}（新增开关请先在 config.py 注册）")


def feature_modules() -> list[str]:
    """返回已注册的功能模块（保持注册顺序、去重）。"""
    seen: list[str] = []
    for f in FEATURE_FLAGS:
        if f.module not in seen:
            seen.append(f.module)
    return seen


def list_feature_flags(module: str | None = None) -> list[FeatureFlag]:
    """按模块筛选开关清单（module=None 返回全部）。"""
    return [f for f in FEATURE_FLAGS if module is None or f.module == module]


def _coerce_feature(raw, default):
    """把 config.json 里的原始值按默认值类型转换；缺失/None 用默认值。"""
    if raw is None:
        return default
    if isinstance(default, bool):
        return bool(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return str(raw) or default
    return raw


# 各 Provider 的默认模型与默认 base_url（OpenAI 兼容协议）
PROVIDER_DEFAULTS: dict[str, dict] = {
    "deepseek": {
        # 2026-10-18：deepseek-chat 已由官方停用（2026-07-24 弃用），当前模型名为 deepseek-flash（DeepSeek-V4.1-Flash-1017）
        "model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "doubao": {  # 火山方舟（豆包），模型名形如 ep-xxx 或 doubao-seed-1-6-250615
        "model": "doubao-seed-1-6-250615",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "env_key": "ARK_API_KEY",
    },
    "openai": {
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "mock": {  # 本地假 Provider：不联网，返回占位文本，用于无 Key 冒烟测试
        "model": "mock-model",
        "base_url": "",
        "env_key": "",
    },
}

# 调研阶段联网搜索源默认地址
SEARCH_PROVIDER_DEFAULTS: dict[str, dict] = {
    "bocha": {
        "api_url": "https://api.bochaai.com/v1/web-search",
        "env_key": "BOCHA_API_KEY",
        "header_key": "Authorization",
        "header_value": "Bearer {key}",
    },
    "serper": {
        "api_url": "https://google.serper.dev/search",
        "env_key": "SERPER_API_KEY",
        "header_key": "X-API-KEY",
        "header_value": "{key}",
    },
}


@dataclass
class LLMConfig:
    provider: str = "deepseek"      # deepseek / doubao / openai / mock
    model: str = ""                 # 留空则取 PROVIDER_DEFAULTS 的默认模型
    api_key: str = ""               # 留空则读环境变量
    base_url: str = ""              # 留空则取 PROVIDER_DEFAULTS 的默认地址
    temperature: float = 0.8        # 生成随机度：大纲/人物偏低，正文偏高
    top_p: float = 0.0              # 0 表示不设置（由 API 默认）；防同质化建议 0.75-0.85
    max_tokens: int = 4096          # 单次响应上限（单章输出建议 >= 4000）


@dataclass
class SearchConfig:
    """调研阶段的联网搜索源配置。留空 provider 则只使用内置知识库 + LLM 先验知识。"""
    provider: str = ""      # "" / "bocha"(博查) / "serper"(Google via serper)
    api_key: str = ""
    api_url: str = ""       # 留空取该 provider 默认地址
    count: int = 5          # 每个 query 返回的搜索结果条数


@dataclass
class AgentGuardConfig:
    """Agent 生产级加固配置（阶段1）。

    - enabled=False 时本模块全部防护关闭（等同改造前行为），用于紧急降级。
    - input_check / output_check / quality_check 独立开关，可分别关闭。
    - max_loops / max_tokens 为单任务级兜底阈值；scene_limits 按业务场景细化。
    - 阈值可从环境变量覆盖：AGENT_GUARD_MAX_LOOPS / AGENT_GUARD_MAX_TOKENS / AGENT_GUARD_ENABLED 等。
    """

    enabled: bool = True            # 总开关
    max_loops: int = 200            # 单任务最大 LLM 调用次数（全局兜底，防死循环）
    max_tokens: int = 2_000_000     # 单任务累计 Token 消耗上限（防成本爆炸）
    input_check: bool = True        # 模块3：输入注入检测
    output_check: bool = True       # 模块3：输出基础合规校验
    quality_check: bool = True      # 模块4：ReaderAgent 结果自校验
    scene_limits: dict = field(default_factory=lambda: {
        "research": 30,    # 调研
        "outline": 40,     # 大纲（含分批）
        "world": 25,       # 人物/世界观
        "chapter": 150,    # 单章正文（含扩写/摘要兜底）——11章高质量模式每章约4-6次调用，留重试余量
        "revise": 30,      # 单章改稿/重写（高质量模式按章触发）
        "review": 25,      # 单章审校/校验（高质量模式按章触发）
    })


@dataclass
class NovelConfig:
    title: str = "未命名小说"
    genre: str = "玄幻"             # 题材，可自定义
    total_words: int = 100_000      # 单本目标总字数
    chapter_words: int = 2500       # 单章目标字数
    style: str = ""                 # 文风要求（如「轻松诙谐」「冷峻写实」），留空由模型自由发挥
    premise: str = ""               # 核心创意/一句话简介，留空则仅按题材生成
    # ---- 用户自定义设定（扩展字段）----
    background: str = ""            # 故事背景/时代/世界观（强约束，必须严格遵循）
    hero: str = ""                  # 主角设定：姓名/身份/性格/目标（强约束）
    characters: list[str] = field(default_factory=list)  # 配角/反派预设（强约束，可留空让 AI 补）
    plot_direction: str = ""        # 剧情走向/关键情节/必须出现的桥段（参考方向，AI 可细化）
    forbidden: list[str] = field(default_factory=list)   # 避雷/不想出现的元素（强约束）
    # ---- 质量增强开关（默认值见 FEATURE_FLAGS 注册表；新增开关先注册）----
    editor_review: bool = feature_default("editor_review")       # 大纲生成后执行编辑视角质检，不通过则自动修正一次
    de_ai: bool = feature_default("de_ai")                       # 正文生成时注入去AI味写作约束
    style_reanchor_every: int = feature_default("style_reanchor_every")    # 周期轻量再锚定（S2.2）：每 N 章注入滚动风格锚；0=关
    style_anchor_rolling: bool = feature_default("style_anchor_rolling")    # 滚动风格锚（S2.2）：用 book_memory goods 高分片段作滚动风格源
    fixed_style_anchor: bool = feature_default("fixed_style_anchor")        # 固定风格锚（S2.3）：开头1-2章原文+个人旧稿作风格原点，默认关
    style_drift_check: bool = feature_default("style_drift_check")            # L1 漂移检测（S2.4）：每章量化句长波动/TTR/口头禅/比喻密度
    style_drift_threshold: int = feature_default("style_drift_threshold")      # L1 漂移检测阈值（S2.4）：漂移分低于此值报小改提示
    anti_similarity: bool = feature_default("anti_similarity")   # 防同质化：常驻System提示词，拒绝套路/人物内在矛盾/有代价/只展示不叙述
    character_depth: bool = feature_default("character_depth")   # 人物立体化：角色卡一致性 + 选择代价 + 生活体感细节 + 出场标签/对话辨识度
    fanqie_style: bool = feature_default("fanqie_style")         # 番茄节奏：黄金三章开篇纪律 + 爽点四环节 + 章末断章 + 移动端排版 + 轻量世界观/力量体系
    confirm: bool = False           # True：每阶段生成后暂停等待确认；False：自动推进
    # ---- memory：记忆与长文一致性（P0-1/P0-2，默认值见 FEATURE_FLAGS）----
    memory_window: int = feature_default("memory_window")        # 生成当前章时回填最近 N 章摘要，控制上下文成本
    memory_direct_read_chapters: int = feature_default("memory_direct_read_chapters")  # 前文直读：直读最近 N 章正文（0=关闭，保持纯摘要）
    memory_direct_read_chars: int = feature_default("memory_direct_read_chars")        # 每章直读正文最大字符数（取章节尾部）
    character_state_tracking: bool = feature_default("character_state_tracking")       # 每章正文落盘后更新人物动态状态快照
    character_state_llm: bool = feature_default("character_state_llm")                 # 是否用 LLM 抽取状态细节（False=仅确定性出场记录）
    chapter_state_tracking: bool = feature_default("chapter_state_tracking")           # 章间机制/世界状态账本：每章落盘后抽取章末状态供下一章承接
    chapter_state_llm: bool = feature_default("chapter_state_llm")                     # 是否用 LLM 抽取章末/场景状态（False=仅确定性面板数字正则）
    lorebook_trim: bool = feature_default("lorebook_trim")                             # 按章触发注入（Lorebook 式裁剪，P1-3）
    scene_state_mode: str = feature_default("scene_state_mode")                        # 场景间状态抽取模式：auto/llm/deterministic（REQ-token优化 P0-1）
    state_extract_frequency: int = feature_default("state_extract_frequency")          # 章末/人物状态 LLM 抽取频率：每 N 章 1 次（REQ-token优化 P0-4）
    setting_cards: bool = feature_default("setting_cards")                             # 分章设定卡索引（REQ-token优化 P1-1）
    scene_delta_inject: bool = feature_default("scene_delta_inject")                   # 场景 prompt 分层注入（REQ-token优化 P1-2）
    reveal_tracking: bool = feature_default("reveal_tracking")                         # 揭示执行跟踪（REQ-揭示执行跟踪）：每章检测真相锚定词兑现，落 reveal_status.json
    book_memory: bool = feature_default("book_memory")                                 # 小说级记忆系统（P0-7）：质检出问题→踩坑账本→注入本作历史错误
    book_memory_top_n: int = feature_default("book_memory_top_n")                      # 踩坑账本注入 Top-N 条数（P0-7）
    user_memory: bool = feature_default("user_memory")                                 # 用户级记忆库（跨书创作偏好继承）：新书创建与生成时继承注入
    user_memory_inject_top: int = feature_default("user_memory_inject_top")            # 用户级跨书纪律注入 Top-N 条数
    reader_ledger: bool = feature_default("reader_ledger")                             # 读者认知账本+伏笔承诺状态机+爽点配额（REQ-reader-ledger）
    ledger_stalled: bool = feature_default("ledger_stalled")                            # 伏笔 stalled 状态（S1.4）：超 N 章无推进标记并提醒
    ledger_stall_after: int = feature_default("ledger_stall_after")                     # 伏笔 stalled 判定阈值（S1.4）：埋后超 N 章无推进
    foreshadow_semantic_check: bool = feature_default("foreshadow_semantic_check")      # 伏笔语义双路回收判定（S1.4）：低频 LLM 语义核对（默认关）
    foreshadow_lead: int = feature_default("foreshadow_lead")                          # 伏笔临期窗口：距 reveal_ch 提前 N 章开始收束提醒
    foreshadow_original_text: bool = feature_default("foreshadow_original_text")        # 伏笔原文片段存档（S1.1）：埋设章抽原文句，回收章注入
    chapter_commit: bool = feature_default("chapter_commit")                           # 章节原子提交+投影一致性（REQ-借鉴webnovel）：每章收尾统一提交 + projection_log
    # ---- 模式选择 ----
    mode: str = "auto"              # auto=现有工作流（调研→大纲→人物→逐章）；expand=AI扩写（用用户提供的大纲/简要内容直接扩写）
    expand_input: str = ""          # 扩写模式的输入：用户提供的大纲/简要内容（mode=expand 时必填）
    content_type: str = ""          # expand 模式下的输入类型：""=大纲/梗概（纯扩写）；"chapters"=已有分章正文（续写）；"chapters_expand"=扩写已有章节
    expand_targets: list = field(default_factory=list)  # 扩写范围：章节号列表，空=全部已有章节
    with_research: bool = False     # expand 模式可选项：生成前先做一次爆款共性调研（产出 research.json + 沉淀拆书库，并作为扩写参考）；auto 模式始终调研，此字段忽略
    llm: LLMConfig = field(default_factory=LLMConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    skill_prompt_append: str = ""     # 管理员配置的写作风格 skill 追加内容
    style_role_id: int = 0            # 写作风格角色（style_roles.id），0=不选
    style_role_prompt: str = ""       # 风格角色人设+挂载技能组装后的提示词（创建项目时快照）
    style_role_reference: str = ""    # P2-1 few-shot 文风锚定：风格角色携带的参考文风片段（创建项目时快照，注入正文）
    # ---- 节奏控制参数（默认值见 FEATURE_FLAGS）----
    pacing_density: str = feature_default("pacing_density")      # 爽点密度：low(慢热)/medium(适中)/high(密集)
    plot_speed: str = feature_default("plot_speed")              # 剧情推进速度：slow(细腻)/medium(适中)/fast(快节奏)
    action_plan_first: bool = feature_default("action_plan_first")  # 正文前独立生成人物行动表（REQ-token优化 P0-2：默认并入拆场景输出）
    scene_split: bool = feature_default("scene_split")              # 场景级生成下沉普通模式（单章≥2000字自动拆；P1-2）
    scene_plan_align: bool = feature_default("scene_plan_align")    # 拆场景规划对齐大纲人物动线（REQ-scene-plan-align）
    chapter_design: bool = feature_default("chapter_design")        # 章级设计卡（REQ-chapter-design 一期）：确定性派生逐章设计卡注入正文 prompt
    polish_hq_only: bool = feature_default("polish_hq_only")        # 整章润色仅高质量模式（REQ-token优化 P0-3）
    teardown_style_anchor: bool = feature_default("teardown_style_anchor")  # 拆书库题材文风锚：写手阶段注入同题材爆款文风与手法（REQ-双锚风格锚定）
    personal_style_anchor: bool = feature_default("personal_style_anchor")  # 个人旧稿文风锚：有 user_edited 章则抽取其风格注入（S2.1 默认关）
    payoff_quota: bool = feature_default("payoff_quota")                    # 爽点配额：按题材校验章级节奏，未达成注入写章提示（REQ-reader-ledger）
    ai_taste_check: bool = feature_default("ai_taste_check")        # AI 痕迹量化检测开关（P1-4）
    ai_taste_threshold: int = feature_default("ai_taste_threshold") # AI 痕迹分阈值（P1-4）
    final_review: bool = feature_default("final_review")             # 全书终审：整本书写完后的终审编辑（高质量模式默认开启；P2-2）
    stage_gate: bool = feature_default("stage_gate")                 # 多Agent阶段门禁：每阶段产出后准出校验（REQ 多Agent协作分层优化）
    stage_gate_retries: int = feature_default("stage_gate_retries")  # 阶段门禁最大自动重生成轮次（REQ 多Agent协作分层优化）
    consistency_llm_check: bool = feature_default("consistency_llm_check")  # 章内矛盾 LLM 自检：默认仅高质量模式（REQ-token优化 P1-3）
    consistency_llm_every: int = feature_default("consistency_llm_every")   # 章内矛盾 LLM 自检降频：普通模式每 N 章 1 次（REQ-章内自洽与机制账本·扩展）
    targeted_rewrite: bool = feature_default("targeted_rewrite")     # 定点重写（REQ-token优化 P1-4）
    verify_combined: bool = feature_default("verify_combined")       # 一致性校验合并（REQ-token优化 P1-5）
    verify_every: int = feature_default("verify_every")              # 一致性校验频率：每 N 章一次（REQ-token优化 P1-5）
    style_judge_l3: bool = feature_default("style_judge_l3")         # L3 风格裁判（S2.5）：每 verify_every 章 LLM 比对风格，超阈值触发滚动锚刷新
    style_judge_drift_threshold: int = feature_default("style_judge_drift_threshold")  # L3 裁判触发阈值（S2.5）：漂移维度数阈值
    outline_rolling_detail: bool = feature_default("outline_rolling_detail")   # 远期大纲粗粒度（S3.6）：beat_gate 分母只算窗口内节点，默认关
    outline_far_skip: int = feature_default("outline_far_skip")                # 远期大纲窗口阈值（S3.6）：距当前章>N 视为远期排除
    beat_fail_judge: bool = feature_default("beat_fail_judge")                 # fail 章 LLM 二级复核（S3.4）：确认未兑现清单防误报，默认关
    editor_diff_revise: bool = feature_default("editor_diff_revise") # 编辑质检 diff 式修正（REQ-token优化 P1-6）
    outline_multi_candidate: bool = feature_default("outline_multi_candidate")  # P1-3-B 多版本候选
    # ---- 门禁质检正向影响（REQ-门禁质检正向影响，默认见 FEATURE_FLAGS）----
    compliance_clean_rewrite: bool = feature_default("compliance_clean_rewrite")  # P0-1 合规删旧稿重写
    compliance_education: bool = feature_default("compliance_education")          # P0-2 合规首次教育
    diag_feedback: bool = feature_default("diag_feedback")                        # P0-3 质检诊断回喂
    good_pool_layer: bool = feature_default("good_pool_layer")                    # P0-4 good 分层入账
    good_src_isolation: bool = feature_default("good_src_isolation")              # P0-4 good 来源隔离
    good_rotate: bool = feature_default("good_rotate")                            # P0-4 good 采样轮换
    style_homogeneity_check: bool = feature_default("style_homogeneity_check")    # P0-4 手法/句式同质度
    hook_positive_anchor: bool = feature_default("hook_positive_anchor")          # P0-5 追更欲正向锚+streak
    hook_streak_freeze: bool = feature_default("hook_streak_freeze")              # P0-5 streak 容错
    reflexion_note: bool = feature_default("reflexion_note")                      # P1-1 Reflexion 反思笔记
    enhance_pass: bool = feature_default("enhance_pass")                          # P1-2 StageGate 增强旁路
    suggestion_lib: bool = feature_default("suggestion_lib")                      # P1-3 建议指令库
    beat_positive_supply: bool = feature_default("beat_positive_supply")          # P1-4 BeatGate 正向供给
    repetition_direction: bool = feature_default("repetition_direction")          # P2-1 重复正向替换供给
    # ---- 三期产品化能力（Beat 门禁/场景体检/思维链/三分支续写）----
    beat_gate: bool = feature_default("beat_gate")                   # F1 正文对章计划 beat 覆盖自检门禁
    beat_gate_min_coverage: float = feature_default("beat_gate_min_coverage")   # F1 通过阈值
    beat_gate_warn_coverage: float = feature_default("beat_gate_warn_coverage") # F1 警告阈值
    beat_loose_match: bool = feature_default("beat_loose_match")     # F1 覆盖判定前做去标点归一（S3.2）
    beat_chapter_type: bool = feature_default("beat_chapter_type")   # F1 章型白名单：对话/情绪章降阈值或 skip（S3.2）
    beat_node_priority: bool = feature_default("beat_node_priority") # F1 节点分级：P2 不计入覆盖率分母（S3.2）
    pending_commitments: bool = feature_default("pending_commitments") # 章末承诺台账：漏兑现节点下章补偿注入（S3.1）
    scene_inspector: bool = feature_default("scene_inspector")       # F2 场景体检
    chain_of_thought: bool = feature_default("chain_of_thought")     # F4 思维链聚合
    branch_continue: bool = feature_default("branch_continue")     # F3 三分支续写
    high_quality_mode: bool = feature_default("high_quality_mode")  # 高质量模式：场景级生成 + 动态模块 + 参数分层，3元/万字
    custom_prompt: str = ""           # 用户自定义提示词模板（追加到system末尾）
    json_schema_output: bool = feature_default("json_schema_output")  # JSON 输出约束（S4.3）：chat_json 传 response_format json_object，provider 支持时
    prefix_cache_aligned: bool = feature_default("prefix_cache_aligned")  # 前缀缓存对齐（S4.1）：稳定前缀前置 + cache_hit 埋点
    agent_guard: AgentGuardConfig = field(default_factory=AgentGuardConfig)  # 生产级加固配置

    @property
    def num_chapters(self) -> int:
        """目标总字数 ÷ 单章字数，向上取整，至少 1 章。"""
        return max(1, math.ceil(self.total_words / self.chapter_words))

    def effective_title(self) -> str:
        return self.title.strip() or "未命名小说"


def _apply_provider_defaults(llm: LLMConfig) -> LLMConfig:
    """用 Provider 内置默认值补齐未填字段，并回填环境变量中的 API Key。"""
    defaults = PROVIDER_DEFAULTS.get(llm.provider, {})
    if not llm.model:
        llm.model = defaults.get("model", "")
    if not llm.base_url:
        llm.base_url = defaults.get("base_url", "")
    if not llm.api_key:
        env_key = defaults.get("env_key", "")
        if env_key:
            llm.api_key = os.environ.get(env_key, "") or ""
    return llm




def validate_config(cfg: NovelConfig) -> list[str]:
    """配置校验，返回问题列表；空列表表示通过。"""
    issues: list[str] = []
    # 字数完全由用户决定，不做下限建议（按字数收费即可）
    if cfg.chapter_words < 20:
        issues.append("chapter_words 过小（<20），单章字数不合理")
    if cfg.memory_window < 0:
        issues.append("memory_window 不能为负数")
    if cfg.llm.provider not in PROVIDER_DEFAULTS:
        issues.append(f"未知 provider: {cfg.llm.provider}，可选 {list(PROVIDER_DEFAULTS)}")
    if cfg.llm.provider != "mock" and not cfg.llm.api_key:
        issues.append(f"provider[{cfg.llm.provider}] 缺少 api_key：请设置环境变量 "
                      f"{PROVIDER_DEFAULTS.get(cfg.llm.provider, {}).get('env_key', '')}")
    guard = getattr(cfg, "agent_guard", None)
    if guard is not None:
        if guard.max_loops < 1:
            issues.append("agent_guard.max_loops 必须 >= 1")
        if guard.max_tokens < 1:
            issues.append("agent_guard.max_tokens 必须 >= 1")
    return issues


def apply_guard_env_overrides(guard: AgentGuardConfig) -> AgentGuardConfig:
    """用环境变量覆盖 AgentGuardConfig（生产可按 .env 动态调阈值，不改代码）。

    支持：AGENT_GUARD_ENABLED / AGENT_GUARD_MAX_LOOPS / AGENT_GUARD_MAX_TOKENS
        / AGENT_GUARD_INPUT_CHECK / AGENT_GUARD_OUTPUT_CHECK / AGENT_GUARD_QUALITY_CHECK
        / AGENT_GUARD_SCENE_<SCENE>（如 AGENT_GUARD_SCENE_CHAPTER=150，覆盖场景限额）
    """
    def _bool_env(name: str, default: bool) -> bool:
        v = os.environ.get(name, "")
        return {"1": True, "true": True, "0": False, "false": False}.get(v.strip().lower(), default)

    def _int_env(name: str, default: int) -> int:
        v = os.environ.get(name, "").strip()
        try:
            return int(v) if v else default
        except ValueError:
            return default

    guard.enabled = _bool_env("AGENT_GUARD_ENABLED", guard.enabled)
    guard.max_loops = _int_env("AGENT_GUARD_MAX_LOOPS", guard.max_loops)
    guard.max_tokens = _int_env("AGENT_GUARD_MAX_TOKENS", guard.max_tokens)
    guard.input_check = _bool_env("AGENT_GUARD_INPUT_CHECK", guard.input_check)
    guard.output_check = _bool_env("AGENT_GUARD_OUTPUT_CHECK", guard.output_check)
    guard.quality_check = _bool_env("AGENT_GUARD_QUALITY_CHECK", guard.quality_check)
    # 场景级限额：AGENT_GUARD_SCENE_<SCENE>，如 AGENT_GUARD_SCENE_CHAPTER=150
    for scene in list(guard.scene_limits):
        guard.scene_limits[scene] = _int_env(f"AGENT_GUARD_SCENE_{scene.upper()}", guard.scene_limits[scene])
    return guard


if __name__ == "__main__":
    # python -m novel_gen.config：按功能模块打印开关清单（集中查看/排查用）
    print("功能开关注册表（FEATURE_FLAGS）· 按模块分组\n")
    for _mod in feature_modules():
        print(f"[{_mod}]")
        for _f in list_feature_flags(_mod):
            print(f"  {_f.key:36s} = {_f.default!r:<8s} {_f.desc}")
        print()

