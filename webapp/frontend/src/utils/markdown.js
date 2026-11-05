// 小墨AI 回答的 Markdown 渲染器（与助手面板共用，便于单测）。
// html: false —— 原始 HTML 一律转义，杜绝 v-html 注入；breaks: true —— 保留单换行；
// linkify: false —— 不把正文里的裸串自动变链接，避免误判。
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({ html: false, linkify: false, breaks: true })

// 外链统一新窗口打开，并加 noopener/noreferrer 防止新页面拿到 window.opener
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return self.renderToken(tokens, idx, options)
}

export function renderMarkdown(text) {
  return md.render(String(text || ''))
}
