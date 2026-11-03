// 请求级幂等键（D1=1A）：后端约定 8-64 位 [A-Za-z0-9_-]，本函数统一产出合规值。
// 逐级降级，保证任何环境（含非安全上下文/旧浏览器）都不会产出短串或含 . : 等非法字符：
//   1) randomUUID：36 位，含连字符，合规且碰撞率极低；
//   2) getRandomValues：16 字节转 32 位十六进制，兼容只支持 Web Crypto 基础的浏览器；
//   3) 最后兜底：时间戳 + 随机串拼足 32 位小写字母/数字。
export function newRequestId() {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  if (c && typeof c.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    c.getRandomValues(bytes)
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  }
  let out = Date.now().toString(36)
  while (out.length < 32) out += Math.random().toString(36).slice(2)
  return out.slice(0, 32)
}

// 与后端 webapp/assistant/service.py 的 _REQUEST_ID_RE 保持一致，供前端/测试提前校验
export const REQUEST_ID_RE = /^[A-Za-z0-9_-]{8,64}$/
