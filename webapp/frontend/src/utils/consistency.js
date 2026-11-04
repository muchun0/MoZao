// 全书体检展示口径（纯函数，前端与后端 severity 约定保持一致：high/medium/low/info）

export const SEVERITY_ORDER = { high: 3, medium: 2, low: 1, info: 0 }

// 严重度 → Element Plus 标签样式与文案
export const SEVERITY_META = {
  high: { label: '高', type: 'danger' },
  medium: { label: '中', type: 'warning' },
  low: { label: '低', type: 'primary' },
  info: { label: '提示', type: 'info' },
}

// 伏笔状态筛选（与后端 FS_STATE_LABELS 对应）
export const FORESHADOW_STATES = [
  { value: 'overdue', label: '逾期未回收' },
  { value: 'stalled', label: '停滞无推进' },
  { value: 'due_soon', label: '临期' },
  { value: 'planted', label: '已埋设' },
  { value: 'revealed', label: '已回收' },
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

// 章节跳转按钮文案：未写到的章节不假装可跳转
export function chapterJumpLabel(no) {
  return no ? `第 ${no} 章` : '未记录'
}
