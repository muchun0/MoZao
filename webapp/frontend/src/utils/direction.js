// R26 剧情走向候选的纯函数（无网络依赖，供组件与 node 单测共用）

export const STUCK_TYPES = [
  { value: '', label: '不指定卡点' },
  { value: 'foreshadow', label: '伏笔该收了' },
  { value: 'commitment', label: '承诺要兑现' },
  { value: 'conflict', label: '冲突不够强' },
  { value: 'pace', label: '节奏拖了' },
  { value: 'custom', label: '自定义' },
]

export const SOURCE_TAG_LABELS = {
  foreshadow: '收伏笔',
  commitment: '兑承诺',
  conflict: '加冲突',
  twist: '反转',
  pacing: '调节奏',
}

export function sourceTagLabel(tag) {
  return SOURCE_TAG_LABELS[tag] || ''
}

// 与服务端 contract 对齐：总长 ≤500 字，超出时截断说明（方向标签优先保留）
export function directionInstruction(item, limit = 500) {
  const direction = String(item?.direction || '').trim()
  const summary = String(item?.summary || '').trim()
  const text = `按这个走向续写：${direction}。${summary}`
  if (text.length <= limit) return text
  const head = `按这个走向续写：${direction}。`
  const room = Math.max(0, limit - head.length - 1)
  return `${head}${summary.slice(0, room)}…`
}

// 本批是否偏通用（服务端不硬校验，前端仅提示）
export function isGenericBatch(items) {
  const rows = Array.isArray(items) ? items : []
  return rows.length > 0 && rows.every((i) => i?.source_tag === 'pacing')
}
