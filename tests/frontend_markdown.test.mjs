// 小墨AI Markdown 渲染测试：验证实际生产渲染器（src/utils/markdown.js）的分段/标题/加粗，
// 以及原始 HTML 被转义（防 XSS）。运行：node --test tests/frontend_markdown.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { renderMarkdown } from '../webapp/frontend/src/utils/markdown.js'

test('标题/加粗/列表按 Markdown 渲染，不再露出 ## 与 **', () => {
  const html = renderMarkdown('## 评估\n\n**一句话结论**：开局钩子立得住。\n\n1. **故事性**：冲突清晰。\n2. **节奏**：推进到位。')
  assert.match(html, /<h2>评估<\/h2>/)
  assert.match(html, /<strong>一句话结论<\/strong>/)
  assert.match(html, /<ol>[\s\S]*<strong>故事性<\/strong>[\s\S]*<strong>节奏<\/strong>/)
  assert.equal(html.includes('##'), false)
  assert.equal(html.includes('**'), false)
})

test('单换行保留（breaks=true），段落之间有间距', () => {
  const html = renderMarkdown('第一行\n第二行\n\n新段落')
  assert.match(html, /第一行<br>\s*第二行/)
  assert.match(html, /<p>新段落<\/p>/)
})

test('原始 HTML 被转义，不产生可执行标签（XSS 防护）', () => {
  const html = renderMarkdown('<script>alert(1)</script><img src=x onerror=alert(2)>')
  assert.equal(html.includes('<script'), false)
  assert.equal(html.includes('<img'), false)
  assert.match(html, /&lt;script&gt;/)
})

test('Markdown 链接带 target=_blank 与 noopener/noreferrer', () => {
  const html = renderMarkdown('[参考](https://example.com)')
  assert.match(html, /target="_blank"/)
  assert.match(html, /rel="noopener noreferrer"/)
})
