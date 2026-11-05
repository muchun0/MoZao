/**
 * R13：派生记录标脏的前端口径。
 * 判定与文案全部由后端 `artifact_staleness.analyze` 产出，这里只做取值，
 * 保证体检页与「工具 → 生成过程」两处展示完全一致，不在前端各写一套。
 */

/** 取统一提示对象；无过期记录时返回 null。 */
export function staleNoticeOf(payload) {
  return payload?.staleness?.notice || null
}

/** 某条目是否被标为「记录可能过期」（兼容接口的 snake_case 与面板内部驼峰）。 */
export function isItemStale(item) {
  if (!item || typeof item !== 'object') return false
  return Boolean(item.record_stale || item.recordStale)
}

/** 覆盖视图里已过期的章号列表，保持后端顺序（升序）。 */
export function staleChaptersOf(row) {
  const list = row?.stale_chapters || row?.staleChapters
  return Array.isArray(list) ? list.filter((n) => Number(n) > 0) : []
}
