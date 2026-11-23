// R29 写作侧栏纯逻辑：宽度/状态记忆、本章请求参数、伏笔角标与命中选区。
// 抽成独立模块便于 node --test 覆盖（组件只做渲染与事件转发）。

export const SIDEBAR_STORAGE_KEY = 'mozao.writing.sidebar'
export const SIDEBAR_DEFAULT_WIDTH = 320
export const SIDEBAR_MIN_WIDTH = 260
export const SIDEBAR_MAX_WIDTH = 480
export const SIDEBAR_NARROW_WIDTH = 1200

export const SIDEBAR_TABS = [
  { key: 'chapter', label: '本章', short: '本' },
  { key: 'outline', label: '大纲', short: '纲' },
  { key: 'characters', label: '人物', short: '人' },
  { key: 'foreshadow', label: '伏笔', short: '伏' },
  { key: 'settings', label: '设定', short: '设' },
  { key: 'search', label: '搜索', short: '搜' },
]

const TAB_KEYS = SIDEBAR_TABS.map(t => t.key)

export function clampSidebarWidth(width) {
  const n = Number(width)
  if (!Number.isFinite(n)) return SIDEBAR_DEFAULT_WIDTH
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(n)))
}

export function readSidebarState(storage) {
  const fallback = { open: false, width: SIDEBAR_DEFAULT_WIDTH, tab: 'chapter' }
  if (!storage) return fallback
  try {
    const raw = JSON.parse(storage.getItem(SIDEBAR_STORAGE_KEY) || 'null')
    if (!raw || typeof raw !== 'object') return fallback
    return {
      open: typeof raw.open === 'boolean' ? raw.open : fallback.open,
      width: clampSidebarWidth(raw.width),
      tab: TAB_KEYS.includes(raw.tab) ? raw.tab : fallback.tab,
    }
  } catch {
    return fallback
  }
}

export function writeSidebarState(storage, state) {
  if (!storage) return
  try {
    storage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify({
      open: !!state?.open,
      width: clampSidebarWidth(state?.width),
      tab: TAB_KEYS.includes(state?.tab) ? state.tab : 'chapter',
    }))
  } catch {
    // 隐私模式/配额满：状态记忆失败不影响侧栏使用
  }
}

// 切章后按新章号重新请求（本章视图自动跟随）
export function contextParams(ch) {
  return { ch: Number(ch) || 0 }
}

// 角标只提示两类：临期（黄）/ 逾期（红）
export function foreshadowBadge(counts) {
  const due = Number(counts?.due_soon) || 0
  const overdue = Number(counts?.overdue) || 0
  return { due, overdue, total: due + overdue }
}

export function isNarrowViewport(width) {
  const n = Number(width)
  return Number.isFinite(n) && n < SIDEBAR_NARROW_WIDTH
}

// 距回收章数由后端计算，前端只做文案映射
export function revealInText(revealIn) {
  if (revealIn === null || revealIn === undefined || revealIn === '') return '未登记回收章'
  const n = Number(revealIn)
  if (!Number.isFinite(n)) return '未登记回收章'
  if (n < 0) return `逾期 ${-n} 章`
  if (n === 0) return '本章到期'
  return `还有 ${n} 章`
}

// 搜索命中 → textarea 选区：offset 起点 + 关键词长度（无 q 时退化为插入点）
export function searchSelection(hit) {
  const offset = Math.max(0, Number(hit?.offset) || 0)
  const length = String(hit?.q || '').length
  return { offset, end: offset + length }
}
