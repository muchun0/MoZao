import test from 'node:test'
import assert from 'node:assert/strict'

import {
  SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_STORAGE_KEY,
  clampSidebarWidth, contextParams, foreshadowBadge, isNarrowViewport,
  readSidebarState, revealInText, searchSelection, writeSidebarState,
} from '../src/utils/writingSidebar.js'

function fakeStorage(initial) {
  const map = new Map(Object.entries(initial || {}))
  return {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, v),
    dump: () => Object.fromEntries(map),
  }
}

test('clampSidebarWidth 收敛到 260–480', () => {
  assert.equal(clampSidebarWidth(100), SIDEBAR_MIN_WIDTH)
  assert.equal(clampSidebarWidth(9999), SIDEBAR_MAX_WIDTH)
  assert.equal(clampSidebarWidth('abc'), SIDEBAR_DEFAULT_WIDTH)
  assert.equal(clampSidebarWidth(333.6), 334)
})

test('侧栏状态记忆：非法值与损坏 JSON 回退默认', () => {
  const empty = readSidebarState(fakeStorage())
  assert.deepEqual(empty, { open: false, width: SIDEBAR_DEFAULT_WIDTH, tab: 'chapter' })

  const broken = readSidebarState(fakeStorage({ [SIDEBAR_STORAGE_KEY]: '{oops' }))
  assert.equal(broken.tab, 'chapter')

  const badTab = readSidebarState(fakeStorage({
    [SIDEBAR_STORAGE_KEY]: JSON.stringify({ open: true, width: 10, tab: 'nope' }),
  }))
  assert.deepEqual(badTab, { open: true, width: SIDEBAR_MIN_WIDTH, tab: 'chapter' })
})

test('侧栏状态写入后可原样读回', () => {
  const storage = fakeStorage()
  writeSidebarState(storage, { open: true, width: 400, tab: 'foreshadow' })
  assert.deepEqual(readSidebarState(storage), { open: true, width: 400, tab: 'foreshadow' })
})

test('切章后请求参数跟随当前章号（P0-2）', () => {
  assert.deepEqual(contextParams(3), { ch: 3 })
  assert.deepEqual(contextParams('7'), { ch: 7 })
  assert.deepEqual(contextParams(undefined), { ch: 0 })
})

test('伏笔角标只统计临期与逾期', () => {
  assert.deepEqual(foreshadowBadge({ due_soon: 2, overdue: 1, planted: 9 }), {
    due: 2, overdue: 1, total: 3,
  })
  assert.deepEqual(foreshadowBadge(null), { due: 0, overdue: 0, total: 0 })
})

test('窄屏阈值 1200', () => {
  assert.equal(isNarrowViewport(1199), true)
  assert.equal(isNarrowViewport(1200), false)
})

test('距回收章文案', () => {
  assert.equal(revealInText(0), '本章到期')
  assert.equal(revealInText(-2), '逾期 2 章')
  assert.equal(revealInText(3), '还有 3 章')
  assert.equal(revealInText(null), '未登记回收章')
})

test('搜索命中选区覆盖关键词长度', () => {
  assert.deepEqual(searchSelection({ offset: 12, q: '铜铃' }), { offset: 12, end: 14 })
  assert.deepEqual(searchSelection({ offset: -1 }), { offset: 0, end: 0 })
})
