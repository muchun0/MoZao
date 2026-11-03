// 前端幂等键生成器测试（node --test 直接跑，无需额外依赖）
// 覆盖：主路径 UUID、降级 getRandomValues、无 Web Crypto 兜底、以及字符集/长度边界。
import test from 'node:test'
import assert from 'node:assert/strict'
import { webcrypto } from 'node:crypto'
import { newRequestId, REQUEST_ID_RE } from '../webapp/frontend/src/utils/requestId.js'

const withCrypto = (value) => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', { value, configurable: true })
  return () => Object.defineProperty(globalThis, 'crypto', saved)
}

test('主路径：randomUUID 生成的键合规且基本不重复', () => {
  const ids = new Set()
  for (let i = 0; i < 50; i += 1) {
    const id = newRequestId()
    assert.match(id, REQUEST_ID_RE, `不合规: ${id}`)
    ids.add(id)
  }
  assert.ok(ids.size >= 49, '幂等键重复率异常')
})

test('降级：无 randomUUID 时用 getRandomValues 产出 32 位十六进制', () => {
  const restore = withCrypto({ getRandomValues: webcrypto.getRandomValues.bind(webcrypto) })
  try {
    const id = newRequestId()
    assert.match(id, /^[0-9a-f]{32}$/)
    assert.match(id, REQUEST_ID_RE)
  } finally {
    restore()
  }
})

test('兜底：无 Web Crypto 时仍为 32 位小写字母/数字，长度不低于 8', () => {
  const restore = withCrypto({})
  try {
    for (let i = 0; i < 10; i += 1) {
      const id = newRequestId()
      assert.match(id, /^[a-z0-9]{32}$/, `兜底不合规: ${id}`)
      assert.match(id, REQUEST_ID_RE)
    }
  } finally {
    restore()
  }
})

test('校验正则与后端一致：合法/缺失/过短/超长/非法字符', () => {
  for (const ok of ['3f2a1c4e-5b6d-4e7f-8a9b-0c1d2e3f4a5b', 'a'.repeat(8), 'a'.repeat(64), 'req_2026-11-03_test-1']) {
    assert.equal(REQUEST_ID_RE.test(ok), true, `应通过: ${ok}`)
  }
  for (const bad of ['', 'a'.repeat(7), 'a'.repeat(65), 'bad.key', 'bad:key', 'bad key', '评分键']) {
    assert.equal(REQUEST_ID_RE.test(bad), false, `应拒绝: ${bad}`)
  }
})
