// REQ-R23 前端入口收敛：术语与口径常量
// 改动点：同一概念只在多处出现时从这里取文案，改名只改一处；单测锁住，防止回退成旧名。

// 工具页导航名：从「工具」改为「工具中心」，明示它是诊断/参考中心而不是第二个生成台
export const TOOL_TAB_NAME = '工具中心'

// 写作约束（原「核心设定锁定」）。它与「防覆盖锁定」是两件事，旧名把两者混在一起
export const RULE_TERM = '写作铁律'
export const RULE_TERM_LEGACY = '核心设定锁定'
export const RULE_TERM_DESC = 'AI 写每一章都必须遵守的硬性约束；改/删立即影响后续生成'

// 内容保护锁（章节 / 角色 / 大纲）：只防重生成覆盖，不是写作约束
export const LOCK_TERM = '防覆盖锁定'
export const LOCK_TERM_HINT = '只保护已有内容不被重生成覆盖，不代表写作约束；写作约束见「写作铁律」'

// 计划事件：避免把作者排期误当作已发生事实
export const PLAN_EVENT_HINT = '计划 ≠ 已发生：这里只排期，事件写进正文后才算事实'

// 工具页三组职责（顺序即页面顺序）
export const TOOL_SECTIONS = [
  {
    key: 'quality',
    title: '质量中心',
    hint: '只做诊断与解读，不生成正文；检查结论统一去「全书体检」看。',
  },
  {
    key: 'reference',
    title: '参考与候选',
    hint: '产出候选供你挑选，不写入正文，选中后自己复制使用。',
  },
  {
    key: 'asset',
    title: '资产与约束',
    hint: '书级资产与写作约束，改动立即影响后续生成。',
  },
]
