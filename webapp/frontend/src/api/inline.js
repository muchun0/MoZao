import request from './request'

// R25 内联写作：生成（计费）/ 采纳（不计费）/ 未采纳记录 / 触发前预估
export const inlineGenerate = (pid, no, data) =>
  request.post(`/projects/${pid}/chapters/${no}/inline/generate`, data)
export const inlineAdopt = (pid, no, data) =>
  request.post(`/projects/${pid}/chapters/${no}/inline/adopt`, data)
export const getInlineDrafts = (pid, no) =>
  request.get(`/projects/${pid}/chapters/${no}/inline/drafts`)
export const getInlineEstimate = (pid, no, params) =>
  request.get(`/projects/${pid}/chapters/${no}/inline/estimate`, { params })

// 只读全文检索（R25 交付，R28/R29 共用）
export const searchProject = (pid, params) =>
  request.get(`/projects/${pid}/search`, { params })

// 与服务端 `novel_gen.store.inline_edit.normalize_text` 完全同口径
export function normalizeText(text) {
  return String(text ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
}

// 与服务端 `content_hash` 同口径：sha1: + sha1(utf8)
export async function contentHash(text) {
  const data = new TextEncoder().encode(normalizeText(text))
  if (!globalThis.crypto?.subtle) {
    throw new Error('当前浏览器不支持本地哈希（需 https 或 localhost），请刷新页面重试')
  }
  const buf = await globalThis.crypto.subtle.digest('SHA-1', data)
  const hex = Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('')
  return `sha1:${hex}`
}

// 每次生成/采纳的幂等令牌
export function newClientToken() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `t-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}
