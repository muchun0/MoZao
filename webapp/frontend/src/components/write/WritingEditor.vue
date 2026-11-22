<template>
  <!-- 正文编辑内核：textarea + 选区/caret 坐标（供浮动条定位），不直接调后端 -->
  <section class="editor">
    <header class="ed-head">
      <div class="ed-left">
        <el-button text size="small" @click="$emit('back')">← 详情</el-button>
        <span class="ed-title">{{ title }}</span>
        <el-tag size="small" type="info" effect="plain">v{{ version }}</el-tag>
        <el-tag v-if="summaryStale" size="small" type="warning" effect="plain" title="内联采纳只改正文，摘要待下次生成刷新">
          摘要待刷新
        </el-tag>
      </div>
      <div class="ed-right">
        <span class="ed-words">{{ words }} 字</span>
        <span v-if="dirty" class="ed-dirty">未保存</span>
        <el-button size="small" :loading="saving" :disabled="!dirty" @click="$emit('save')">保存</el-button>
        <el-button size="small" @click="$emit('versions')">版本</el-button>
        <el-button size="small" type="primary" :loading="busy" @click="$emit('continue')">
          续写 <span class="kbd">Ctrl+Enter</span>
        </el-button>
      </div>
    </header>
    <div class="ed-wrap">
      <textarea ref="taRef" class="ed-area" :value="modelValue"
                :disabled="loading"
                placeholder="在这里写作。选中一段文字可改写/扩写，Ctrl+Enter 从光标处续写。"
                @input="onInput" @select="onSelect" @mouseup="onSelect"
                @keyup="onSelect" @scroll="onScroll" @keydown="onKeydown" />
      <div v-if="loading" class="ed-loading">加载中…</div>
    </div>
  </section>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  serverContent: { type: String, default: '' },
  title: { type: String, default: '' },
  version: { type: Number, default: 0 },
  summaryStale: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'select', 'save', 'back', 'versions', 'continue'])

const taRef = ref(null)
const dirty = computed(() => props.modelValue !== props.serverContent)

function onInput(e) {
  emit('update:modelValue', e.target.value)
  onSelect()
}

function onKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
    e.preventDefault()
    emit('continue')
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    emit('save')
  }
}

/** 粗略计算 caret 在页面上的坐标（镜像 div 方案，兼容中文换行）。 */
function caretPoint(pos) {
  const ta = taRef.value
  if (!ta) return null
  const rect = ta.getBoundingClientRect()
  const style = getComputedStyle(ta)
  const mirror = document.createElement('div')
  const keys = ['fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
    'paddingTop', 'paddingLeft', 'paddingRight', 'paddingBottom', 'borderTopWidth',
    'borderLeftWidth', 'width', 'boxSizing', 'whiteSpace', 'wordWrap', 'overflowWrap']
  keys.forEach(k => { mirror.style[k] = style[k] })
  mirror.style.position = 'absolute'
  mirror.style.visibility = 'hidden'
  mirror.style.height = 'auto'
  mirror.style.whiteSpace = 'pre-wrap'
  mirror.style.wordWrap = 'break-word'
  mirror.textContent = ta.value.slice(0, pos)
  const marker = document.createElement('span')
  marker.textContent = '\u200b'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const top = marker.offsetTop
  const left = marker.offsetLeft
  const lineHeight = parseInt(style.lineHeight, 10) || 28
  document.body.removeChild(mirror)
  return {
    x: rect.left + left - ta.scrollLeft,
    y: rect.top + top - ta.scrollTop + lineHeight + 6,
  }
}

function onSelect() {
  const ta = taRef.value
  if (!ta) return
  const start = ta.selectionStart
  const end = ta.selectionEnd
  const point = caretPoint(end)
  emit('select', { start, end, x: point?.x || 0, y: point?.y || 0 })
}

function onScroll() { emit('scroll') }

function getCursor() {
  const ta = taRef.value
  return ta ? { start: ta.selectionStart, end: ta.selectionEnd } : { start: 0, end: 0 }
}

function focusAt(pos, end) {
  const ta = taRef.value
  if (!ta) return
  ta.focus()
  // 搜索命中时选中整个关键词区间，仅光标定位时 end 可省略
  ta.setSelectionRange(pos, Number.isFinite(end) ? end : pos)
  // 让目标位置尽量滚入视野（按字符比例估算行高）
  const ratio = ta.value.length ? pos / ta.value.length : 0
  ta.scrollTop = Math.max(0, ta.scrollHeight * ratio - ta.clientHeight / 2)
  onSelect()
}

defineExpose({ getCursor, focusAt })
</script>

<style scoped>
.editor { flex: 1; min-width: 0; display: flex; flex-direction: column; height: 100%; }
.ed-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 20px; border-bottom: 1px solid var(--card-line); background: var(--card);
}
.ed-left, .ed-right { display: flex; align-items: center; gap: 10px; min-width: 0; }
.ed-left { flex: 1 1 auto; overflow: hidden; }
.ed-right { flex: 0 0 auto; }
.ed-title {
  font-family: var(--serif); font-size: 16px; color: var(--ink);
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ed-words { color: var(--muted); font-size: 12px; white-space: nowrap; flex-shrink: 0; }
.ed-dirty { color: var(--gold-deep); font-size: 12px; }
.kbd { font-size: 10px; opacity: .65; margin-left: 4px; }
.ed-wrap { position: relative; flex: 1; min-height: 0; background: var(--paper); }
.ed-area {
  width: 100%; height: 100%; box-sizing: border-box; resize: none; border: 0; outline: none;
  padding: 32px 48px 80px; background: transparent; color: var(--ink);
  font-family: var(--serif); font-size: 17px; line-height: 2; letter-spacing: .3px;
  text-indent: 2em;
}
.ed-area:disabled { opacity: .6; }
@media (max-width: 1280px) {
  .ed-area { padding: 24px 28px 64px; }
  .ed-title { font-size: 14px; }
  .kbd { display: none; }
}
.ed-loading {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--muted); font-size: 13px; background: rgba(246,243,234,.6);
}
</style>
