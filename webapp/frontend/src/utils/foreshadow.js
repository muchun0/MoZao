// REQ-R10 A：伏笔进度卡片展示口径（纯函数，供 ToolsPanel 与前端用例共用）
// 说明：状态判定与距离文案的唯一来源是后端 ReaderLedger，本文件只做展示映射，
// 不重新推导「逾期/临期」，避免出现第二套口径。

export const FORESHADOW_STATE_TAG = {
  overdue: 'danger',
  due_soon: 'warning',
  stalled: 'warning',
  planted: 'info',
  revealed: 'success',
  not_buried: 'info',
}

const DEFAULT_SUMMARY = {
  total: 0, tracked: 0, unstructured: 0, baseline_ch: 0,
  revealed: 0, overdue: 0, due_soon: 0, stalled: 0, planted: 0, not_buried: 0,
}

// 筛选顺序按「作者最该处理的先出现」排列
export const FORESHADOW_FILTERS = [
  { key: 'all', label: '全部', field: 'tracked', type: 'info' },
  { key: 'overdue', label: '逾期', field: 'overdue', type: 'danger' },
  { key: 'due_soon', label: '临期', field: 'due_soon', type: 'warning' },
  { key: 'stalled', label: '停滞', field: 'stalled', type: 'warning' },
  { key: 'planted', label: '按计划', field: 'planted', type: 'info' },
  { key: 'revealed', label: '已回收', field: 'revealed', type: 'success' },
  { key: 'not_buried', label: '未到埋点', field: 'not_buried', type: 'info' },
]

export function normalizeForeshadowSummary(summary) {
  return { ...DEFAULT_SUMMARY, ...(summary || {}) }
}

export function buildForeshadowFilters(summary) {
  const s = normalizeForeshadowSummary(summary)
  return FORESHADOW_FILTERS.map(f => ({ ...f, count: Number(s[f.field] || 0) }))
}

export function filterForeshadowItems(items, key) {
  const list = Array.isArray(items) ? items : []
  return key === 'all' ? list : list.filter(i => i.state === key)
}

export function foreshadowStateTag(state) {
  return FORESHADOW_STATE_TAG[state] || 'info'
}

// 后端缺 distance_label 时的兜底，保持与后端口径一致
export function foreshadowProgressText(item) {
  if (!item) return '—'
  if (item.distance_label) return item.distance_label
  if (item.state === 'revealed') return item.revealed_ch ? `第 ${item.revealed_ch} 章已收` : '已回收'
  if (item.state === 'overdue' && item.reveal_ch) return `已逾期 ${Math.max(item.distance || 0, 0)} 章`
  if (item.reveal_ch) return `距到期还有 ${Math.max(item.distance || 0, 0)} 章`
  return '未登记回收章'
}

// 老格式纯字符串条目：如实说明，不猜章号
export function unstructuredNote(item) {
  return (item && item.note) || '未登记章号，补全后可纳入跟踪'
}
