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
            <div class="msg-content">{{ m.content }}</div>
            <AssistantCard v-for="(card, i) in msgCards(m)" :key="i" :card="card" />
            <div v-if="m.role === 'assistant' && m.extra && m.extra.notice" class="a-notice">
              {{ m.extra.notice }}
            </div>
            <div v-if="m.role === 'assistant'" class="msg-foot">
              <span v-if="m.extra && m.extra.token_in != null" class="usage">
                第{{ m.extra.chapter_no }}章 · 输入 {{ m.extra.token_in }} / 输出 {{ m.extra.token_out || 0 }} tokens
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
          <span class="hint">Ctrl+Enter 发送 · 不限次不扣费 · 模型 Key 需在「模型设置」自配</span>
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
.a-notice { margin-top: 8px; font-size: 12px; color: #b88230; background: #fdf6ec; border-radius: 6px;
  padding: 6px 8px; }
.msg-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 8px; }
.usage { font-size: 11px; color: #c0c4cc; }
.input-area { border-top: 1px solid #ebeef5; padding: 10px 12px; background: #fff; }
.input-tools { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; min-height: 22px; }
.err-text { font-size: 12px; color: #f56c6c; }
.send-row { display: flex; align-items: center; justify-content: space-between; margin-top: 8px; }
.hint { font-size: 11px; color: #c0c4cc; }
</style>
