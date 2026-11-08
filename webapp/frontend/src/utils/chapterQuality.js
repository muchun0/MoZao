// R12 章节质量总览展示口径（纯函数，便于 node:test 单测）
// 约定：等级词与严重度复用 R2 体检 / 版本弹窗口径，不在前端另造一套。

export const QUALITY_META = {
  high: { label: '大改', type: 'danger' },
  medium: { label: '中改', type: 'warning' },
  low: { label: '小改', type: 'primary' },
  info: { label: '无问题', type: 'success' },
  none: { label: '未质检', type: 'info' },
}

export function qualityMeta(severity) {
  return QUALITY_META[severity] || QUALITY_META.none
}

// 问题分桶文案的唯一出口：「大改 3 处、小改 2 处」；无分桶返回空串
export function countsText(counts = {}) {
  const labels = [['high', '大改'], ['medium', '中改'], ['low', '小改']]
  return labels
    .filter(([key]) => Number(counts[key] || 0) > 0)
    .map(([key, label]) => `${label} ${counts[key]} 处`)
    .join('、')
}

// 章行摘要的问题分桶文案；未质检返回空串
export function qualityCountsText(row) {
  if (!row || row.has_quality === false) return ''
  return countsText(row.counts || {})
}

// 排名最差优先：后端 rank 为唯一口径；未质检/无 rank 排最后并按章号
export function sortRowsByQuality(list = []) {
  return [...list].sort((a, b) => {
    const qa = a.quality || {}
    const qb = b.quality || {}
    const ra = qa.rank == null ? Number.MAX_SAFE_INTEGER : qa.rank
    const rb = qb.rank == null ? Number.MAX_SAFE_INTEGER : qb.rank
    if (ra !== rb) return ra - rb
    return (a.no || 0) - (b.no || 0)
  })
}

// 只看需要处理：后端已判定「质检未通过或存在大改」
export function needsAttention(row) {
  return !!(row && row.quality && row.quality.needs_attention)
}

export function filterRows(list = [], onlyNeedsAttention = false) {
  return onlyNeedsAttention ? list.filter(needsAttention) : list
}

// R15：列表行只做「索引」，不再渲染问题类型摘要；类型明细统一在版本弹窗查看。
const LEVEL_SEVERITY = { 大改: 'high', 中改: 'medium', 小改: 'low' }

// 按等级分桶统计明细条数（未知等级不计入分桶，但仍计入总数）
export function issueCounts(issues = []) {
  const out = { high: 0, medium: 0, low: 0 }
  for (const it of issues || []) {
    const sev = LEVEL_SEVERITY[it && it.level]
    if (sev) out[sev] += 1
  }
  return out
}

// 详情面标题文案：「大改 3 处、中改 5 处、小改 2 处，共 10 处」
// issueCount 可能大于明细条数（历史记录只存总数），此时仍如实显示总数
export function issueSummaryText(issues = [], issueCount) {
  const list = Array.isArray(issues) ? issues : []
  const recorded = Number(issueCount)
  const total = Number.isFinite(recorded) && recorded >= 0 ? Math.max(recorded, list.length) : list.length
  if (!total) return ''
  const parts = countsText(issueCounts(list))
  return parts ? `${parts}，共 ${total} 处` : `共 ${total} 处`
}

// 章行质量徽标的索引说明：只报等级/分数/条数，明确指向唯一详情面
export function qualityIndexTitle(row) {
  if (!row || !row.has_quality) return '这一章还没跑质检，点击查看版本与改稿建议'
  const score = row.score === null || row.score === undefined ? '未记录分数' : `${row.score} 分`
  return `质检 ${score} · ${Number(row.issue_count) || 0} 处改稿建议，点击查看证据与改法`
}

// 顶部「最需要处理」提示；没有 worst 时返回空串（由调用方展示空态文案）
export function qualityWorstText(overview, chapters = []) {
  const worst = overview && overview.worst
  if (!worst) return ''
  const title = (chapters.find((c) => c.no === worst.no) || {}).title || ''
  const score = worst.score == null ? '无评分' : `${worst.score}分`
  const counts = qualityCountsText(worst)
  return `最需要处理：第${worst.no}章${title ? `《${title}》` : ''} · ${score}${counts ? ` · ${counts}` : ''}`
}

// 数据缺口说明（未质检章、索引缺失等，由后端 data_gaps 给出）
export function qualityGapText(overview) {
  const gaps = (overview && overview.data_gaps) || []
  return gaps.join('；')
}
