// 全书体检展示口径（纯函数，前端与后端 severity 约定保持一致：high/medium/low/info）
// 原则：用户可见文案一律用作者能懂的中文口语，不暴露内部视图代号 / 字段名 / 状态英文键。

export const SEVERITY_ORDER = { high: 3, medium: 2, low: 1, info: 0 }

// 严重度 → 标签样式与作者视角文案
export const SEVERITY_META = {
  high: { label: '必须处理', type: 'danger' },
  medium: { label: '建议处理', type: 'warning' },
  low: { label: '了解一下', type: 'primary' },
  info: { label: '提示', type: 'info' },
}

// 严重度 → 界面分组（high=必须先处理 / medium=建议处理 / 其余=了解一下）
export const SEVERITY_GROUP = { high: 'must', medium: 'suggest', low: 'later', info: 'later' }

export function severityGroup(severity) {
  return SEVERITY_GROUP[severity] || 'later'
}

// 账本覆盖：内部视图代号 → 作者能理解的「设定小抄」名称
export const PROJECTION_VIEW_LABELS = {
  chapter_commit: '章节要点记录',
  chapter_state: '全书进度记录',
  reveal_status: '伏笔回收记录',
  foreshadow_commitment: '伏笔回收计划',
  payoff_quota: '爽点节奏记录',
  summaries: '章节摘要',
  scenes: '场景清单',
  beat_gate: '关键剧情节点',
  character_states: '人物当前状态',
  book_memory: '全书前情提要',
}

export function projectionViewLabel(view) {
  return PROJECTION_VIEW_LABELS[view] || '这项记录'
}

// 覆盖状态 → 作者口语（不再直接显示「同步 / 落后 / 超前 / 未知」）
export const COVERAGE_STATE_LABELS = {
  synced: '跟上了', lagging: '落后了', ahead: '超前了', unknown: '还没记录',
}
export const COVERAGE_STATE_TYPES = {
  synced: 'success', lagging: 'danger', ahead: 'warning', unknown: 'info',
}

export function coverageStateLabel(status) {
  return COVERAGE_STATE_LABELS[status] || '还没记录'
}

export function coverageStateType(status) {
  return COVERAGE_STATE_TYPES[status] || 'info'
}

// 伏笔状态 → 作者口语（后端仍返回 state / state_label，前端只换展示口径）
export const FORESHADOW_STATE_LABELS = {
  overdue: '早该收了',
  stalled: '很久没推进',
  due_soon: '近期该收',
  planted: '已埋下',
  revealed: '已经收了',
}

export function foreshadowStateLabel(state) {
  return FORESHADOW_STATE_LABELS[state] || '已埋下'
}

// 伏笔筛选选项（顺序即「先处理要紧的」）
export const FORESHADOW_STATES = [
  { value: 'overdue', label: '早该收了' },
  { value: 'stalled', label: '很久没推进' },
  { value: 'due_soon', label: '近期该收' },
  { value: 'planted', label: '已埋下' },
  { value: 'revealed', label: '已经收了' },
]

export const ENTITY_KIND_OPTIONS = [
  { value: 'character', label: '人物' },
  { value: 'location', label: '地点' },
  { value: 'faction', label: '势力' },
  { value: 'item', label: '物件' },
  { value: 'enemy', label: '生物' },
  { value: 'term', label: '术语' },
]

export function severityMeta(severity) {
  return SEVERITY_META[severity] || SEVERITY_META.info
}

// 伏笔：一句话问题（界面三要素之一「问题是什么」）
export function foreshadowProblem(item = {}) {
  const name = item.name || '这条线索'
  switch (item.state) {
    case 'overdue':
      return `「${name}」埋下后一直没交代，已经超过原计划回收的时间`
    case 'stalled':
      return `「${name}」埋下后很久没再提到，读者可能已经忘了`
    case 'due_soon':
      return `「${name}」快到该收的时候了，别再往后拖`
    case 'revealed':
      return `「${name}」已经在正文里收掉了`
    default:
      return `「${name}」已埋下，目前按计划推进`
  }
}

// 伏笔：位置（三要素之二）
export function foreshadowWhere(item = {}) {
  const parts = []
  if (item.buried_ch) parts.push(`埋在第 ${item.buried_ch} 章`)
  if (item.reveal_ch) parts.push(`原计划第 ${item.reveal_ch} 章收`)
  if (item.revealed_ch) parts.push(`第 ${item.revealed_ch} 章已收`)
  return parts.join(' · ') || '埋设位置还没记录'
}

// 伏笔：建议怎么处理（三要素之三）
export function foreshadowAdvice(item = {}) {
  switch (item.state) {
    case 'overdue':
      return '翻回最早埋下它的那一章，找机会收掉；如果这条线本来就不打算用了，把回收计划改掉或删掉，免得越拖越像烂尾。'
    case 'stalled':
      return '在接下来一两章里让它再露一次面，哪怕只提一句，读者才不会把它忘掉。'
    case 'due_soon':
      return '接下来两三章里找个时机收掉，别拖过原计划。'
    case 'revealed':
      return '已经收掉了，不用再管。'
    default:
      return '不用动，按计划收就行。'
  }
}

// 一致性冲突：来源换成作者能理解的部门，不显示内部 source 键
export function sourceLabel(source) {
  return source === 'verify' ? '全书通读检查' : '单章质检'
}

export const CONFLICT_ADVICE_FALLBACK =
  '打开这一章核对，把说法统一成你现在的设定；如果设定本身改了，记得顺手更新世界观。'

export function conflictAdvice(item = {}) {
  return (item.suggestion || '').trim() || CONFLICT_ADVICE_FALLBACK
}

// 人物/设定索引：一句话摘要（出现次数 + 覆盖区间；零命中明确说是「还没用上」）
export function entitySummary(row = {}) {
  const chapters = row.chapters || []
  if (!chapters.length) {
    return '正文里还没出现过——设定写了但还没用上，后续可以留意'
  }
  const first = row.first_ch ?? chapters[0].no
  const last = row.last_ch ?? chapters[chapters.length - 1].no
  const span = first === last ? `只在第 ${first} 章出现` : `第 ${first}–${last} 章都有`
  return `共出现 ${row.total_mentions || 0} 次，${span}`
}

// 后端 data_gaps 措辞偏系统：按内容改写成作者能懂的一句话；未知内容用兜底，不原样回显
export function humanizeGap(gap = '') {
  if (gap.includes('语义级')) {
    return '这里只汇总已有的通读检查和单章质检记录，不会逐句重读全书；没被记下来的矛盾，这里看不到。'
  }
  if (gap.includes('未记录')) {
    return '某一项显示「还没记录」时，说明这个环节还没生成数据，不代表全书没问题。'
  }
  return '有一小部分数据暂时没取到，结果可能不完整。'
}

// 按严重度降序排序（不改原数组；同严重度保持后端顺序，便于稳定浏览）
export function sortBySeverity(items = []) {
  return [...items].sort(
    (a, b) => (SEVERITY_ORDER[b?.severity] || 0) - (SEVERITY_ORDER[a?.severity] || 0)
  )
}

// 统计某一严重度的条目数（首屏徽标用）
export function countSeverity(items = []) {
  const out = { high: 0, medium: 0, low: 0, info: 0 }
  for (const it of items) {
    const key = SEVERITY_ORDER[it?.severity] ? it.severity : 'info'
    out[key] += 1
  }
  return out
}

// 章节跳转文案：未写到的章节不假装可跳转
export function chapterJumpLabel(no) {
  return no ? `第 ${no} 章` : '未记录'
}
