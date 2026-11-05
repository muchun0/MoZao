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

// 问题分桶文案：「大改 3 处、小改 2 处」；无问题返回空串
export function qualityCountsText(row) {
  if (!row || row.has_quality === false) return ''
  const counts = row.counts || {}
  const labels = [['high', '大改'], ['medium', '中改'], ['low', '小改']]
  return labels
    .filter(([key]) => Number(counts[key] || 0) > 0)
    .map(([key, label]) => `${label} ${counts[key]} 处`)
    .join('、')
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

// 章行下方的「差在哪类问题」：最多 2 类，避免窄屏挤压
export function qualityIssueText(row, limit = 2) {
  if (!row || !row.has_quality) return ''
  return (row.top_issues || [])
    .slice(0, limit)
    .map((it) => (it.count > 1 ? `${it.type} ×${it.count}` : it.type))
    .filter(Boolean)
    .join(' · ')
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
