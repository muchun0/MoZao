// 中间产物只读展示口径（纯函数，供「工具 → 生成过程」使用）
// 只做展示格式化，不产生任何写操作。

export const ARTIFACT_TEXT_LIMIT = 46

export const SCENE_EMPTY_TEXT = '本章走了整章生成，没有场景计划记录'
export const DESIGN_EMPTY_TEXT = '本章还没有设计卡（大纲生成后才会写入）'
export const SUMMARY_EMPTY_TEXT = '本章还没有摘要记录'
export const ANCHOR_LIMIT = 5

/** 长文本按字符截断，超出补「…」 */
export function clipText(text, limit = ARTIFACT_TEXT_LIMIT) {
  const s = String(text || '').trim()
  if (!s) return ''
  return s.length > limit ? `${s.slice(0, limit)}…` : s
}

/** 场景标题：地点 · 事件；缺字段时退回事件/地点/事件引用 */
export function sceneTitle(scene = {}) {
  const location = String(scene.location || '').trim()
  const event = String(scene.event || '').trim()
  if (location && event) return `${location} · ${event}`
  return event || location || String(scene.scene_event_ref || '').trim() || '未命名场景'
}

/** 场景展开后的明细（空字段自动过滤） */
export function sceneDetails(scene = {}) {
  const rows = [
    { label: '冲突', text: scene.conflict },
    { label: '动作', text: scene.action },
    { label: '结尾状态', text: scene.final_state },
    { label: '情绪', text: scene.emotion },
    { label: '时间', text: scene.time_marker },
  ]
  return rows.filter((r) => String(r.text || '').trim())
}

/** 细节锚点最多展示 5 条 */
export function sceneAnchors(scene = {}) {
  const list = Array.isArray(scene.anchors) ? scene.anchors : []
  return list.filter((a) => String(a || '').trim()).slice(0, ANCHOR_LIMIT)
}

/** 场景总览文案 */
export function sceneCountText(count = 0) {
  const n = Number(count) || 0
  return n > 0 ? `系统写本章时自动记下，共 ${n} 个场景` : ''
}

/** 设计卡明细（空字段自动过滤） */
export function designLines(design = {}) {
  const conflict = design.conflict || {}
  const rows = [
    { label: '本章目标', text: design.goal },
    { label: '核心冲突', text: conflict.core },
    { label: '转折点', text: (design.turning_point || {}).event },
    { label: '章末钩子', text: design.cliffhanger },
  ]
  return rows.filter((r) => String(r.text || '').trim())
}
