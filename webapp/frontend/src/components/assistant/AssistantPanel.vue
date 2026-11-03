<template>
  <div class="assistant-panel">
    <!-- 左：会话列表 -->
    <aside class="conv-side">
      <el-button size="small" type="primary" class="new-btn" @click="newConversation">新建会话</el-button>
      <div class="conv-list">
        <div v-for="c in conversations" :key="c.id" class="conv-item"
             :class="{ active: c.id === activeId }" @click="openConversation(c.id)">
          <div class="conv-main">
            <div class="conv-title">{{ c.title || '未命名会话' }}</div>
            <div class="conv-meta">{{ c.message_count }} 条 · {{ fmtTime(c.updated_at) }}</div>
          </div>
          <el-button link size="small" type="danger" @click.stop="removeConversation(c)">删除</el-button>
        </div>
        <el-empty v-if="!conversations.length" description="还没有会话" :image-size="56" />
      </div>
    </aside>

    <!-- 右：消息区 -->
    <section class="msg-side">
      <div class="msg-toolbar">
        <el-radio-group v-model="mode" size="small">
          <el-radio-button value="chapter_eval">章节评估</el-radio-button>
          <el-radio-button value="revision_advice">改稿建议</el-radio-button>
        </el-radio-group>
        <el-select v-model="chapterNo" size="small" placeholder="选择章节" class="ch-select">
          <el-option v-for="c in chapters" :key="c.no" :value="c.no"
                     :label="`第${c.no}章 ${c.title || ''}`" />
        </el-select>
      </div>

      <div ref="msgBox" v-loading="loadingMsgs" class="msg-list">
        <div v-if="!messages.length && !loadingMsgs" class="empty-hint">
          {{ chapters.length ? '选好章节，问小墨：这一章的冲突、节奏、人物动机或文笔哪里有问题。' : '先完成章节生成，小墨才有素材可评估。' }}
        </div>
        <div v-for="m in messages" :key="m.id" class="msg-row" :class="m.role">
          <div class="bubble">
            <!-- 小墨回答渲染 Markdown；用户提问保持纯文本（white-space: pre-wrap） -->
            <div v-if="m.role === 'assistant'" class="msg-content md-body" v-html="renderMd(m.content)"></div>
            <div v-else class="msg-content">{{ m.content }}</div>
            <AssistantCard v-for="(card, i) in msgCards(m)" :key="i" :card="card" />
            <div v-if="m.role === 'assistant' && m.extra && m.extra.notice" class="a-notice">
              {{ m.extra.notice }}
            </div>
            <div v-if="m.role === 'assistant'" class="msg-foot">
              <span v-if="m.extra && m.extra.token_in != null" class="usage">
                第{{ m.extra.chapter_no }}章 · 输入 {{ m.extra.token_in }} / 输出 {{ m.extra.token_out || 0 }} tokens
              </span>
              <span v-if="m.extra && m.extra.billing_status" class="billing">
                <template v-if="m.extra.billing_status === 'billed'">
                  本次 {{ m.extra.chars || 0 }} 字 · 实扣 ¥{{ (m.extra.charged || 0).toFixed(2) }}
                  <span v-if="m.extra.owe > 0">（欠费 ¥{{ m.extra.owe.toFixed(2) }}）</span>
                </template>
                <template v-else-if="m.extra.billing_status === 'failed'">计费待补扣</template>
                <template v-else>本条不计费</template>
              </span>
              <el-button v-if="m.extra && m.extra.chapter_no" link size="small" type="primary"
                         @click="$emit('locate', m.extra.chapter_no)">
                定位到第 {{ m.extra.chapter_no }} 章
              </el-button>
            </div>
          </div>
        </div>
        <div v-if="sending" class="msg-row assistant">
          <div class="bubble typing">小墨正在阅读本章…</div>
        </div>
      </div>

      <div class="input-area">
        <div class="input-tools">
          <el-button link size="small" :disabled="!chapterNo" @click="quoteCurrent">引用当前章</el-button>
          <span v-if="errorText" class="err-text">{{ errorText }}</span>
        </div>
        <el-input v-model="draft" type="textarea" :rows="3" maxlength="4000" show-word-limit
                  :placeholder="placeholder" @keydown.ctrl.enter.prevent="submit" />
        <div class="send-row">
          <span class="hint">Ctrl+Enter 发送 · 按回答字数计费（3 元/万字）· 自备 Key 不扣费</span>
          <el-button type="primary" :loading="sending" :disabled="!chapters.length || !draft.trim()"
                     @click="submit">发送</el-button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listConversations, createConversation, getConversation, sendMessage, deleteConversation,
} from '../../api/assistant'
import AssistantCard from './AssistantCard.vue'
import { renderMarkdown as renderMd } from '../../utils/markdown'

const props = defineProps({
  projectId: { type: String, required: true },
  chapters: { type: Array, default: () => [] },
  currentCh: { type: Object, default: null },
})
defineEmits(['locate'])

const conversations = ref([])
const activeId = ref(0)
const messages = ref([])
const mode = ref('chapter_eval')
const chapterNo = ref(0)
const draft = ref('')
const sending = ref(false)
const loadingMsgs = ref(false)
const errorText = ref('')
const msgBox = ref(null)

const placeholder = computed(() => mode.value === 'chapter_eval'
  ? '例：这一章的冲突推进够不够？读者会在哪里失去耐心？'
  : '例：我想改这一章，先改哪三处最有效？哪些是硬伤、哪些只是偏好？')
const msgCards = (m) => (m.extra && Array.isArray(m.extra.cards) ? m.extra.cards : [])

// 章节默认跟随阅读器当前章
watch(() => props.currentCh, (ch) => { if (ch && ch.no) chapterNo.value = ch.no }, { immediate: true })

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function errMsg(e, fallback) {
  const detail = e && e.response && e.response.data && e.response.data.detail
  if (typeof detail === 'string') return detail
  if (e && e.response && e.response.status === 403) return '小墨AI 当前未开启或无权访问'
  return fallback
}

function scrollBottom() {
  nextTick(() => { if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight })
}

async function reloadConversations() {
  try {
    const res = await listConversations(props.projectId)
    conversations.value = (res && res.items) || []
  } catch (e) {
    errorText.value = errMsg(e, '会话列表加载失败')
  }
}

async function openConversation(id) {
  if (!id) return
  activeId.value = id
  errorText.value = ''
  loadingMsgs.value = true
  try {
    const res = await getConversation(props.projectId, id, { limit: 100 })
    messages.value = (res && res.messages) || []
    const conv = (res && res.conversation) || {}
    if (conv.mode) mode.value = conv.mode
    scrollBottom()
  } catch (e) {
    messages.value = []
    errorText.value = errMsg(e, '会话加载失败')
  } finally {
    loadingMsgs.value = false
  }
}

async function ensureConversation() {
  if (activeId.value) return activeId.value
  const title = draft.value.trim().slice(0, 20) || '新的会话'
  const res = await createConversation(props.projectId, { title, mode: mode.value })
  activeId.value = res.id
  await reloadConversations()
  return res.id
}

async function newConversation() {
  errorText.value = ''
  try {
    const ch = chapterNo.value ? `第${chapterNo.value}章 · ` : ''
    const res = await createConversation(props.projectId, {
      title: ch + (mode.value === 'chapter_eval' ? '章节评估' : '改稿建议'),
      mode: mode.value,
    })
    await reloadConversations()
    await openConversation(res.id)
  } catch (e) {
    errorText.value = errMsg(e, '新建会话失败')
  }
}

async function removeConversation(c) {
  try {
    await ElMessageBox.confirm(`删除会话「${c.title || '未命名会话'}」？消息将一并删除。`,
                               '删除会话', { type: 'warning' })
  } catch { return }
  try {
    await deleteConversation(props.projectId, c.id)
    if (activeId.value === c.id) { activeId.value = 0; messages.value = [] }
    await reloadConversations()
    ElMessage.success('已删除')
  } catch (e) {
    errorText.value = errMsg(e, '删除失败')
  }
}

async function submit() {
  const text = draft.value.trim()
  if (!text || sending.value) return
  if (!props.chapters.length) { errorText.value = '先完成章节生成，小墨才有素材可评估'; return }
  sending.value = true
  errorText.value = ''
  try {
    const cid = await ensureConversation()
    draft.value = ''
    // 幂等键由 api 层统一生成（见 utils/requestId.js），调用方无需再传
    await sendMessage(props.projectId, cid, {
      content: text, mode: mode.value, chapter_no: chapterNo.value || 0,
    })
    await openConversation(cid)
    await reloadConversations()
  } catch (e) {
    draft.value = text  // 失败保留输入，便于重试
    errorText.value = errMsg(e, '发送失败')
  } finally {
    sending.value = false
  }
}

function quoteCurrent() {
  if (!chapterNo.value) return
  draft.value = `@第${chapterNo.value}章 ` + draft.value
}

onMounted(async () => {
  await reloadConversations()
  if (conversations.value.length) await openConversation(conversations.value[0].id)
})
</script>

<style scoped>
.assistant-panel { display: flex; gap: 12px; min-height: 560px; }
.conv-side { width: 220px; flex-shrink: 0; display: flex; flex-direction: column; gap: 8px; }
.new-btn { width: 100%; }
.conv-list { flex: 1; overflow-y: auto; max-height: 560px; display: flex; flex-direction: column; gap: 6px; }
.conv-item { display: flex; align-items: center; gap: 6px; padding: 8px 10px; border: 1px solid #e4e7ed;
  border-radius: 8px; cursor: pointer; background: #fff; }
.conv-item:hover { background: #f5f7fa; }
.conv-item.active { border-color: #c9a961; background: #fdf9ef; }
.conv-main { flex: 1; min-width: 0; }
.conv-title { font-size: 13px; color: #1f3a5f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.conv-meta { font-size: 11px; color: #909399; margin-top: 2px; }
.msg-side { flex: 1; min-width: 0; display: flex; flex-direction: column; border: 1px solid #e4e7ed;
  border-radius: 8px; background: #fff; overflow: hidden; }
.msg-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 10px 12px; border-bottom: 1px solid #ebeef5; }
.ch-select { width: 160px; }
.msg-list { flex: 1; overflow-y: auto; padding: 12px; background: #fafafa; max-height: 440px; }
.empty-hint { color: #909399; font-size: 13px; text-align: center; padding: 40px 20px; line-height: 1.8; }
.msg-row { display: flex; margin-bottom: 10px; }
.msg-row.user { justify-content: flex-end; }
.msg-row.assistant { justify-content: flex-start; }
.bubble { max-width: 86%; padding: 10px 12px; border-radius: 10px; font-size: 13px; line-height: 1.75; }
.msg-row.user .bubble { background: #1f3a5f; color: #fff; border-top-right-radius: 2px; }
.msg-row.assistant .bubble { background: #fff; color: #303133; border: 1px solid #e4e7ed;
  border-top-left-radius: 2px; }
.bubble.typing { color: #909399; }
.msg-content { white-space: pre-wrap; word-break: break-word; }
/* 小墨回答的 Markdown 样式：v-html 生成的子元素不在 scoped 作用域内，需 :deep 命中 */
.md-body { white-space: normal; }
.md-body :deep(p) { margin: 0 0 8px; }
.md-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(h1), .md-body :deep(h2), .md-body :deep(h3) {
  margin: 10px 0 6px; font-size: 13.5px; font-weight: 600; color: #1f3a5f;
}
.md-body :deep(h1:first-child), .md-body :deep(h2:first-child), .md-body :deep(h3:first-child) { margin-top: 0; }
.md-body :deep(strong) { font-weight: 600; color: #1f3a5f; }
.md-body :deep(ul), .md-body :deep(ol) { margin: 4px 0 8px; padding-left: 18px; }
.md-body :deep(li) { margin: 2px 0; }
.md-body :deep(code) { background: #f5f7fa; border-radius: 4px; padding: 1px 4px; font-size: 12px; }
.md-body :deep(pre) { background: #f5f7fa; border-radius: 6px; padding: 8px; overflow-x: auto; }
.md-body :deep(blockquote) {
  margin: 6px 0; padding: 4px 10px; border-left: 2px solid #c9a961; color: #606266; background: #fdf9ef;
}
.md-body :deep(a) { color: #1f3a5f; text-decoration: underline; }
.a-notice { margin-top: 8px; font-size: 12px; color: #b88230; background: #fdf6ec; border-radius: 6px;
  padding: 6px 8px; }
.msg-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
.usage { font-size: 11px; color: #c0c4cc; }
.billing { font-size: 11px; color: #909399; }
.input-area { border-top: 1px solid #ebeef5; padding: 10px 12px; background: #fff; }
.input-tools { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; min-height: 22px; }
.err-text { font-size: 12px; color: #f56c6c; }
.send-row { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.hint { font-size: 11px; color: #c0c4cc; }
</style>
