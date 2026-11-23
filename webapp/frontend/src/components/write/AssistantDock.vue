<template>
  <!-- R28 小墨段落浮层：挂在写作页右侧，打开时替代 AI/检索 tab（两者互斥） -->
  <section class="dock">
    <header class="dk-head">
      <div class="dk-title">
        <span class="dk-ch">第 {{ chapterNo }} 章 · 第 {{ paraIndex }} 段</span>
        <span v-if="anchorStale" class="dk-stale">原文已变化，建议重新选段</span>
      </div>
      <el-button text size="small" @click="$emit('close')">收起</el-button>
    </header>

    <div class="dk-para" :class="{ open: paraOpen }" @click="paraOpen = !paraOpen">
      {{ paragraphText || '请先在正文里选中一段文字' }}
    </div>

    <div class="dk-presets">
      <button v-for="p in PRESETS" :key="p" class="dk-chip" :disabled="sending"
              @click="ask(p)">{{ p }}</button>
    </div>

    <div ref="msgBox" v-loading="loading" class="dk-msgs">
      <div v-if="historyHint" class="dk-history">{{ historyHint }}</div>
      <div v-for="m in messages" :key="m.key" class="dk-row" :class="m.role">
        <div class="dk-bubble">
          <div v-if="m.role === 'assistant'" class="md-body" v-html="renderMd(m.content)"></div>
          <div v-else class="dk-plain">{{ m.content }}</div>
          <div v-if="m.role === 'assistant' && canApply(m)" class="dk-sugg">
            <div class="dk-sugg-head">建议改写（单段）</div>
            <div class="dk-sugg-text">{{ suggestionOf(m) }}</div>
            <el-button size="small" type="primary" :disabled="!anchorReady"
                       @click="openApply(m)">应用到正文</el-button>
          </div>
          <div v-if="m.extra && m.extra.notice" class="dk-notice">{{ m.extra.notice }}</div>
        </div>
      </div>
      <div v-if="sending" class="dk-row assistant">
        <div class="dk-bubble dk-typing">小墨正在读这一段…</div>
      </div>
      <div v-if="errorText" class="dk-error">{{ errorText }}</div>
    </div>

    <div class="dk-input">
      <el-input v-model="draft" type="textarea" :rows="3" maxlength="4000"
                placeholder="问这一段的写法、矛盾或改法（回答按字数计费）"
                @keydown.ctrl.enter.prevent="ask()" />
      <div class="dk-foot">
        <span class="dk-hint">单次提问最多 4000 字 · 按回答字数计费 · 应用到正文另按写入字数计费</span>
        <el-button type="primary" size="small" :loading="sending"
                   :disabled="!anchorReady || !draft.trim()" @click="ask()">发送</el-button>
      </div>
    </div>

    <!-- 应用确认：原文 vs 建议对照 + 预计费用（写动作必须人拍板） -->
    <el-dialog v-model="applyOpen" title="应用到正文" width="640px" append-to-body>
      <div class="ap-grid">
        <div class="ap-col">
          <div class="ap-label">原段落</div>
          <div class="ap-text">{{ paragraphText }}</div>
        </div>
        <div class="ap-col">
          <div class="ap-label">建议段落</div>
          <div class="ap-text ap-new">{{ applyText }}</div>
        </div>
      </div>
      <p class="ap-foot">
        写入后新增一个版本（可在版本弹窗回退）；按写入 {{ applyWords }} 字计费，预计约
        ¥{{ applyPrice }}（自备 Key 不扣费）。你确认后才会写入正文。
      </p>
      <template #footer>
        <el-button @click="applyOpen = false">取消</el-button>
        <el-button type="primary" :loading="applying" @click="confirmApply">确认应用</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  listConversations, createConversation, sendMessage, listAnchorMessages,
  applyAssistantSuggestion,
} from '../../api/assistant'
import { contentHash, newClientToken } from '../../api/inline'
import { renderMarkdown as renderMd } from '../../utils/markdown'

const props = defineProps({
  pid: { type: String, required: true },
  chapterNo: { type: Number, required: true },
  anchor: { type: Object, default: () => ({ start: 0, end: 0 }) },
  paragraphText: { type: String, default: '' },
  paraIndex: { type: Number, default: 1 },
  version: { type: Number, default: 0 },
  baseHash: { type: String, default: '' },
  preset: { type: String, default: '' },
})
const emit = defineEmits(['close', 'applied'])

const PRESETS = ['这段哪里不好', '帮我改生动', '这段和前文矛盾吗']
const SUGGEST_RE = /<suggestion>([\s\S]*?)<\/suggestion>/i

const conversationId = ref(0)
const messages = ref([])
const draft = ref('')
const sending = ref(false)
const loading = ref(false)
const errorText = ref('')
const paraOpen = ref(false)
const anchorStale = ref(false)
const historyHint = ref('')
const msgBox = ref(null)

const applyOpen = ref(false)
const applying = ref(false)
const applyText = ref('')
const applyMessage = ref(null)

const anchorReady = computed(() => !!props.paragraphText && props.anchor.end > props.anchor.start)
const applyWords = computed(() => (applyText.value || '').replace(/\s/g, '').length)
const applyPrice = computed(() => (applyWords.value * 0.0003).toFixed(2))

function errText(e, fallback) {
  const detail = e && e.response && e.response.data && e.response.data.detail
  return typeof detail === 'string' ? detail : fallback
}

function suggestionOf(m) {
  const hit = SUGGEST_RE.exec(m.content || '')
  if (!hit) return ''
  return hit[1].replace(/^```[a-z]*\s*/i, '').replace(/```\s*$/, '').trim()
}

/** 只有「当前选段」对应的建议才显示应用按钮（历史消息锚点不同会 409，不误导）。 */
function canApply(m) {
  if (!suggestionOf(m)) return false
  const a = (m.extra && m.extra.anchor) || m.anchor || null
  if (!a) return false
  return Number(a.start) === Number(props.anchor.start)
    && Number(a.end) === Number(props.anchor.end)
}

function scrollBottom() {
  nextTick(() => { if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight })
}

function localMsg(role, content, extra = {}) {
  return { key: `${role}-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
    role, content, extra }
}

async function ensureConversation() {
  if (conversationId.value) return conversationId.value
  const res = await listConversations(props.pid)
  const items = (res && res.items) || []
  const hit = items.find((c) => c.mode === 'inline_span')
  if (hit) { conversationId.value = hit.id; return hit.id }
  const created = await createConversation(props.pid, {
    title: `第${props.chapterNo}章 · 段落问小墨`, mode: 'inline_span',
  })
  conversationId.value = created.id
  return created.id
}

async function loadHistory(cid) {
  // P1-2：按 anchor.ch 过滤的历史问答（同一章多个段落共用，仅作现场恢复）
  try {
    const res = await listAnchorMessages(props.pid, cid, { ch: props.chapterNo, limit: 20 })
    const items = (res && res.items) || []
    if (!items.length) return
    messages.value = items.map((m) => ({
      key: `h-${m.id}`, role: m.role, content: m.content, extra: m.extra || {},
      messageId: m.role === 'assistant' ? m.id : undefined,
    }))
    historyHint.value = `已带出本章段落问答历史 ${items.length} 条（用于接着问，不重复计费）`
  } catch {
    historyHint.value = ''
  }
}

async function ask(presetText = '') {
  const text = String(presetText || draft.value || '').trim()
  if (!text || sending.value) return
  if (!anchorReady.value) { errorText.value = '请先在正文里选中一段文字'; return }
  sending.value = true
  errorText.value = ''
  try {
    const cid = await ensureConversation()
    const textSha = await contentHash(props.paragraphText)
    draft.value = ''
    messages.value.push(localMsg('user', text))
    scrollBottom()
    const res = await sendMessage(props.pid, cid, {
      content: text, mode: 'inline_span', chapter_no: props.chapterNo,
      anchor: { ch: props.chapterNo, start: props.anchor.start, end: props.anchor.end,
        text_sha: textSha },
    })
    anchorStale.value = !!res.anchor_stale
    messages.value.push(localMsg('assistant', res.content || '', {
      notice: res.notice || '', billing_status: res.billing_status,
      charged: res.charged, chars: res.chars,
      message_id: res.message_id, anchor: res.anchor,
    }))
    // 用服务端消息 id 回填，供「应用到正文」按 message_id 回读校验
    const last = messages.value[messages.value.length - 1]
    last.messageId = res.message_id
    if (res.degraded) errorText.value = '模型本次未给出有效回答（本条不计费），可重试'
    scrollBottom()
  } catch (e) {
    errorText.value = errText(e, '提问失败，请稍后重试')
  } finally {
    sending.value = false
  }
}

function openApply(m) {
  applyMessage.value = m
  applyText.value = suggestionOf(m)
  applyOpen.value = true
}

async function confirmApply() {
  const m = applyMessage.value
  if (!m || applying.value) return
  if (!m.messageId) { errorText.value = '缺少建议消息标识，请重新提问后再应用'; applyOpen.value = false; return }
  applying.value = true
  try {
    const res = await applyAssistantSuggestion(props.pid, props.chapterNo, {
      conversation_id: conversationId.value,
      message_id: m.messageId,
      anchor: { start: props.anchor.start, end: props.anchor.end },
      base_version: props.version,
      base_hash: props.baseHash,
      text: applyText.value,
      edited: false,
      client_token: newClientToken(),
    })
    applyOpen.value = false
    ElMessage.success(res.replayed ? '该建议已应用过，未重复写入'
      : `已应用为 v${res.version}（写入 ${res.words} 字）`)
    emit('applied', res)
  } catch (e) {
    const msg = errText(e, '应用失败，正文未改动')
    errorText.value = msg
    ElMessage.error(msg)
    applyOpen.value = false
  } finally {
    applying.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    const cid = await ensureConversation()
    await loadHistory(cid)
    if (props.preset) draft.value = props.preset
    scrollBottom()
  } catch (e) {
    errorText.value = errText(e, '小墨会话加载失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.dock { display: flex; flex-direction: column; height: 100%; background: var(--card); }
.dk-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 12px;
  border-bottom: 1px solid var(--card-line); }
.dk-title { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.dk-ch { font-family: var(--sans); font-size: 13px; color: var(--ink); font-weight: 600; }
.dk-stale { font-size: 11px; color: var(--danger); }
.dk-para { margin: 8px 12px 6px; padding: 8px 10px; border-radius: 8px; background: var(--paper);
  color: var(--ink-soft); font-size: 12px; line-height: 1.7; cursor: pointer;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.dk-para.open { -webkit-line-clamp: unset; max-height: 180px; overflow: auto; }
.dk-presets { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 12px 8px; }
.dk-chip { border: 1px solid var(--card-line); background: transparent; color: var(--muted);
  border-radius: 999px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.dk-chip:hover { color: var(--indigo); border-color: var(--indigo); }
.dk-msgs { flex: 1; min-height: 0; overflow-y: auto; padding: 8px 12px; }
.dk-history { font-size: 11px; color: var(--muted); text-align: center; padding: 4px 0 10px; }
.dk-row { display: flex; margin-bottom: 10px; }
.dk-row.user { justify-content: flex-end; }
.dk-bubble { max-width: 92%; padding: 9px 11px; border-radius: 10px; font-size: 13px;
  line-height: 1.7; border: 1px solid var(--card-line); background: var(--paper); }
.dk-row.user .dk-bubble { background: var(--indigo-deep); border-color: var(--indigo-deep);
  color: #f3eee0; border-top-right-radius: 2px; }
.dk-plain { white-space: pre-wrap; word-break: break-word; }
.dk-typing { color: var(--muted); }
.md-body :deep(p) { margin: 0 0 6px; }
.md-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(strong) { color: var(--indigo-deep); }
.md-body :deep(ul), .md-body :deep(ol) { margin: 4px 0 6px; padding-left: 18px; }
.dk-sugg { margin-top: 8px; border-top: 1px dashed var(--card-line); padding-top: 8px; }
.dk-sugg-head { font-size: 11px; color: var(--gold-deep); margin-bottom: 4px; }
.dk-sugg-text { font-size: 12.5px; line-height: 1.8; color: var(--ink); background: var(--card);
  border: 1px solid var(--card-line); border-radius: 6px; padding: 6px 8px; margin-bottom: 6px;
  white-space: pre-wrap; }
.dk-notice { margin-top: 6px; font-size: 11px; color: var(--gold-deep); }
.dk-error { color: var(--danger); font-size: 12px; padding: 6px 0; }
.dk-input { border-top: 1px solid var(--card-line); padding: 10px 12px; }
.dk-foot { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; gap: 8px; }
.dk-hint { font-size: 11px; color: var(--muted); line-height: 1.5; }
.ap-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.ap-col { min-width: 0; }
.ap-label { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.ap-text { font-size: 13px; line-height: 1.8; color: var(--ink); background: var(--paper);
  border: 1px solid var(--card-line); border-radius: 8px; padding: 8px 10px;
  max-height: 220px; overflow: auto; white-space: pre-wrap; }
.ap-new { border-color: var(--indigo); }
.ap-foot { margin-top: 12px; font-size: 12px; color: var(--muted); line-height: 1.7; }
</style>
