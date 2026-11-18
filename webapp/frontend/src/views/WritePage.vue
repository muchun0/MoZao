<template>
  <!-- R25 独立写作页：左章节目录 / 中正文内核 / 右 AI 与检索 -->
  <div class="write-page">
    <ChapterRail :model-value="currentNo" :chapters="chapters" @update:model-value="switchChapter" />
    <WritingEditor ref="editorRef" v-model="content" :server-content="serverContent"
                   :title="current?.title || ''" :version="current?.version || 0"
                   :summary-stale="!!current?.summary_stale" :saving="saving" :busy="generating"
                   :loading="loadingChapter"
                   @save="save" @back="$router.push(`/project/${pid}`)"
                   @versions="versionsDialog = true" @continue="generate('continue')"
                   @select="onSelect" />
    <WritingSidebar :pid="pid" :ch="currentNo" @jump="switchChapter" @open-search="openSearchHit" />
    <div class="right">
      <!-- R28：小墨段落浮层与右栏 tab 互斥（打开时暂隐） -->
      <AssistantDock v-if="dock.open" :pid="pid" :chapter-no="currentNo"
        :anchor="{ start: dock.start, end: dock.end }" :paragraph-text="dock.paragraph"
        :para-index="dock.paraIndex" :version="current?.version || 0"
        :base-hash="current?.base_hash || ''" :preset="dock.preset"
        @close="dock.open = false" @applied="onDockApplied" />
      <template v-else>
        <div class="tabs">
          <button class="tab" :class="{ on: tab === 'ai' }" @click="tab = 'ai'">AI 写作</button>
          <button class="tab" :class="{ on: tab === 'direction' }" @click="tab = 'direction'">
            走向<span v-if="dirNotes.length" class="tab-badge">{{ dirNotes.length }}</span>
          </button>
          <button class="tab" :class="{ on: tab === 'search' }" @click="tab = 'search'">检索</button>
        </div>
        <CandidatePanel v-if="tab === 'ai'"
          :mode="mode" :target-words="targetWords" :candidate-count="candidateCount"
          :instruction="instruction" :estimate="estimate" :generating="generating"
          :candidates="candidates" :gen-meta="genMeta" :drafts="drafts"
          :can-generate="canGenerate" :blocked-reason="blockedReason" :adopting="adoptingId"
          @update:mode="mode = $event" @update:target-words="onTargetWords"
          @update:candidate-count="onCandidateCount" @update:instruction="instruction = $event"
          @generate="generate(mode)" @adopt="adopt" @copy="copyCandidate" @restore="restoreDraft" />
        <DirectionPanel v-else-if="tab === 'direction'"
          :stuck-type="dirStuckType" :stuck-text="dirStuckText" :count="dirCount"
          :generating="dirGenerating" :batch="dirBatch" :notes="dirNotes"
          :adopting="dirAdoptingId" :can-generate="canGenerate" :blocked-reason="blockedReason"
          @update:stuck-type="dirStuckType = $event" @update:stuck-text="dirStuckText = $event"
          @update:count="dirCount = $event" @generate="generateDirections"
          @adopt="adoptDirection" @use="useDirection" @delete-note="deleteDirNote" />
        <StorySearch v-else :pid="pid" @open="openSearchHit" />
      </template>
    </div>

    <InlineFloatBar :show="floatBar.show" :x="floatBar.x" :y="floatBar.y"
                    @rewrite="generate('rewrite')" @expand="generate('expand')"
                    @ask="openDockFromSelection" />

    <VersionDialog v-model="versionsDialog" :pid="pid" kind="chapter" :chapter-no="currentNo"
                   @changed="reloadCurrent" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getChapters, getChapter, updateChapterContent } from '../api/novel'
import { inlineGenerate, inlineAdopt, getInlineDrafts, getInlineEstimate,
  contentHash, newClientToken } from '../api/inline'
import { generateDirections as apiGenerateDirections,
  adoptDirection as apiAdoptDirection, getBranchNotes, deleteBranchNote } from '../api/branches'
import { directionInstruction } from '../utils/direction'
import { errorDetail } from '../utils/agentRun'
import ChapterRail from '../components/write/ChapterRail.vue'
import WritingEditor from '../components/write/WritingEditor.vue'
import CandidatePanel from '../components/write/CandidatePanel.vue'
import DirectionPanel from '../components/write/DirectionPanel.vue'
import InlineFloatBar from '../components/write/InlineFloatBar.vue'
import StorySearch from '../components/write/StorySearch.vue'
import WritingSidebar from '../components/write/WritingSidebar.vue'
import { searchSelection } from '../utils/writingSidebar'
import AssistantDock from '../components/write/AssistantDock.vue'
import VersionDialog from '../components/VersionDialog.vue'

const route = useRoute()
const pid = String(route.params.id)
const editorRef = ref(null)
const chapters = ref([])
const currentNo = ref(0)
const current = ref(null)
const content = ref('')
const serverContent = ref('')
const loadingChapter = ref(false)
const saving = ref(false)
const tab = ref('ai')
const versionsDialog = ref(false)

const mode = ref('continue')
const targetWords = ref(300)
const candidateCount = ref(2)
const instruction = ref('')
const estimate = ref(null)
const candidates = ref([])
const genMeta = ref(null)
const drafts = ref([])
const generating = ref(false)
const adoptingId = ref('')
const selection = ref({ start: 0, end: 0 })
const floatBar = ref({ show: false, x: 0, y: 0 })
const genToken = ref('')
// R28 段落浮层：anchor 为当前段落在正文中的区间（后端按同一偏移渲染段落窗口）
const dock = ref({ open: false, start: 0, end: 0, paraIndex: 1, paragraph: '', preset: '' })

// R26 剧情走向
const dirStuckType = ref('')
const dirStuckText = ref('')
const dirCount = ref(4)
const dirBatch = ref(null)
const dirNotes = ref([])
const dirGenerating = ref(false)
const dirAdoptingId = ref('')
const dirToken = ref('')

const dirty = computed(() => content.value !== serverContent.value)
const words = computed(() => content.value.replace(/\s/g, '').length)
const blockedReason = computed(() => {
  if (!current.value) return '请先选择章节'
  if (dirty.value) return '有未保存修改：请先保存，AI 需要以已保存正文为基准'
  return ''
})
const canGenerate = computed(() => !!current.value && !dirty.value && !generating.value)

// ---------- 载入 ----------
async function loadChapters() {
  const res = await getChapters(pid)
  chapters.value = res.chapters || []
}

async function loadChapter(no) {
  if (!no) return
  loadingChapter.value = true
  try {
    const res = await getChapter(pid, no)
    current.value = { ...res, version: res.version || 0, base_hash: res.base_hash || '' }
    content.value = res.content || ''
    serverContent.value = res.content || ''
    candidates.value = []
    genMeta.value = null
    genToken.value = ''
    dirBatch.value = null
    dirToken.value = ''
    floatBar.value.show = false
    dock.value.open = false
    await loadDrafts(no)
    await loadDirNotes(no)
    refreshEstimate()
  } catch (e) {
    ElMessage.error(errorDetail(e, '章节加载失败'))
  } finally {
    loadingChapter.value = false
  }
}

async function loadDrafts(no) {
  try {
    const res = await getInlineDrafts(pid, no)
    drafts.value = res.items || []
  } catch {
    drafts.value = []
  }
}

async function loadDirNotes(no) {
  try {
    const res = await getBranchNotes(pid, no)
    dirNotes.value = res.items || []
  } catch {
    dirNotes.value = []
  }
}

async function reloadCurrent() {
  if (currentNo.value) await loadChapter(currentNo.value)
}

async function switchChapter(no) {
  if (no === currentNo.value) return true
  if (dirty.value) {
    try {
      await ElMessageBox.confirm('当前章节有未保存修改，切换后会丢失。继续？', '未保存',
        { type: 'warning', confirmButtonText: '丢弃并切换', cancelButtonText: '取消' })
    } catch {
      currentNo.value = currentNo.value
      return false
    }
  }
  currentNo.value = no
  await loadChapter(no)
  return true
}

onMounted(async () => {
  try {
    await loadChapters()
  } catch (e) {
    ElMessage.error(errorDetail(e, '章节列表加载失败'))
    return
  }
  const fromQuery = Number(route.query.ch || 0)
  const target = chapters.value.find(c => c.no === fromQuery)?.no || chapters.value[0]?.no || 0
  currentNo.value = target
  await loadChapter(target)
  handleIssueJump()
})

// ---------- 编辑 / 保存 ----------
function onSelect(sel) {
  selection.value = { start: sel.start, end: sel.end }
  const hasSel = sel.end > sel.start
  floatBar.value = { show: hasSel, x: sel.x, y: sel.y }
  // 选中文字后自动切到改写，避免用户还要点一下模式
  if (hasSel && mode.value === 'continue') mode.value = 'rewrite'
}

async function save() {
  if (!current.value || !dirty.value) return
  saving.value = true
  try {
    await updateChapterContent(pid, currentNo.value, { content: content.value })
    await loadChapter(currentNo.value)
    await loadChapters()
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errorDetail(e, '保存失败'))
  } finally {
    saving.value = false
  }
}

// ---------- 预估 ----------
let estimateTimer = null
function refreshEstimate() {
  clearTimeout(estimateTimer)
  estimateTimer = setTimeout(async () => {
    if (!current.value) return
    try {
      estimate.value = await getInlineEstimate(pid, currentNo.value,
        { chars: targetWords.value, candidates: candidateCount.value })
    } catch {
      estimate.value = null
    }
  }, 250)
}
function onTargetWords(v) { targetWords.value = v || 300; refreshEstimate() }
function onCandidateCount(v) { candidateCount.value = v === 3 ? 3 : 2; refreshEstimate() }

// ---------- 生成 ----------
function anchorFor(writeMode) {
  if (writeMode === 'continue') {
    const cursor = editorRef.value?.getCursor() || selection.value
    // 初次进入未点过正文时 caret 为 0，默认续在全文末尾
    const pos = cursor.end || selection.value.end || content.value.length
    return { start: pos, end: pos }
  }
  return { start: selection.value.start, end: selection.value.end }
}

async function generate(writeMode) {
  if (!current.value || generating.value) return
  if (dirty.value) { ElMessage.warning('请先保存当前修改'); return }
  const anchor = anchorFor(writeMode)
  if (writeMode !== 'continue' && anchor.end - anchor.start < 5) {
    ElMessage.warning('请先选中至少 5 个字')
    return
  }
  const baseHash = await contentHash(content.value)
  if (!genToken.value) genToken.value = newClientToken()
  generating.value = true
  try {
    const res = await inlineGenerate(pid, currentNo.value, {
      mode: writeMode, anchor, base_version: current.value.version,
      base_hash: baseHash, client_token: genToken.value,
      candidates: candidateCount.value, target_words: targetWords.value,
      instruction: instruction.value,
    })
    candidates.value = res.candidates || []
    genMeta.value = { gen_id: res.gen_id, charged: res.charged, words: res.words,
      replayed: res.replayed, base_version: res.base_version, base_hash: res.base_hash }
    genToken.value = ''
    floatBar.value.show = false
    await loadDrafts(currentNo.value)
  } catch (e) {
    handleInlineError(e)
  } finally {
    generating.value = false
  }
}

function handleInlineError(e) {
  const status = e?.response?.status
  ElMessage.error(errorDetail(e, '操作失败'))
  // 409 固定语义：候选保留（STALE_BASE_KEEP_CANDIDATES），只刷新正文基准
  if (status === 409) refreshChapterKeepCandidates()
  if (status === 410) { candidates.value = []; genMeta.value = null; loadDrafts(currentNo.value) }
}

async function refreshChapterKeepCandidates() {
  try {
    const res = await getChapter(pid, currentNo.value)
    current.value = { ...res, version: res.version || 0, base_hash: res.base_hash || '' }
    content.value = res.content || ''
    serverContent.value = res.content || ''
  } catch {
    /* 刷新失败不覆盖用户当前编辑 */
  }
}

// ---------- 采纳 ----------
async function adopt(candidate) {
  if (!genMeta.value || adoptingId.value) return
  adoptingId.value = candidate.id
  try {
    const res = await inlineAdopt(pid, currentNo.value, {
      gen_id: genMeta.value.gen_id, candidate_id: candidate.id,
      base_version: genMeta.value.base_version, base_hash: genMeta.value.base_hash,
      client_token: newClientToken(),
    })
    const chapter = res.chapter || {}
    content.value = chapter.content || content.value
    serverContent.value = chapter.content || serverContent.value
    if (current.value) {
      current.value.version = res.version
      current.value.summary_stale = !!chapter.summary_stale
    }
    candidates.value = []
    genMeta.value = null
    floatBar.value.show = false
    await loadDrafts(currentNo.value)
    await loadChapters()
    ElMessage.success(`已采纳为 v${res.version}`)
  } catch (e) {
    handleInlineError(e)
  } finally {
    adoptingId.value = ''
  }
}

async function copyCandidate(c) {
  try {
    await navigator.clipboard.writeText(c.text)
    ElMessage.success('已复制候选')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}

function restoreDraft(draft) {
  candidates.value = draft.candidates || []
  genMeta.value = {
    gen_id: draft.gen_id, charged: draft.charged, words: draft.words, replayed: false,
    base_version: draft.base_version, base_hash: draft.base_hash,
  }
}

// ---------- 剧情走向（R26） ----------
async function generateDirections() {
  if (!current.value || dirGenerating.value) return
  if (dirty.value) { ElMessage.warning('请先保存当前修改'); return }
  if (dirBatch.value?.items?.length) {
    const note = dirBatch.value.billing || dirBatch.value.charged > 0
      ? '换一批会再次按候选合计字数计费（上一批已计费）。继续？'
      : '换一批会丢弃当前候选（不涉及计费）。继续？'
    try {
      await ElMessageBox.confirm(note, '换一批', { type: 'warning' })
    } catch {
      return
    }
  }
  if (!dirToken.value) dirToken.value = newClientToken()
  dirGenerating.value = true
  try {
    const res = await apiGenerateDirections(pid, currentNo.value, {
      count: dirCount.value,
      stuck_type: dirStuckType.value || null,
      stuck_text: dirStuckType.value === 'custom' ? dirStuckText.value : '',
      client_token: dirToken.value,
    })
    dirBatch.value = res
    dirToken.value = ''
  } catch (e) {
    ElMessage.error(errorDetail(e, '走向生成失败'))
  } finally {
    dirGenerating.value = false
  }
}

async function adoptDirection(item) {
  if (!dirBatch.value || dirAdoptingId.value) return
  dirAdoptingId.value = item.id
  try {
    const res = await apiAdoptDirection(pid, currentNo.value, {
      gen_id: dirBatch.value.gen_id, item_id: item.id, client_token: newClientToken(),
    })
    await loadDirNotes(currentNo.value)
    ElMessage.success(res.duplicated ? '便签已存在（未重复保存）' : '已存为写作便签')
  } catch (e) {
    ElMessage.error(errorDetail(e, '存便签失败'))
  } finally {
    dirAdoptingId.value = ''
  }
}

function useDirection(item) {
  // 「按此写」只预填 R25 续写指令并切回 AI 面板，不自动触发生成（避免意外扣费）
  mode.value = 'continue'
  instruction.value = directionInstruction(item)
  tab.value = 'ai'
  ElMessage.success('已带入走向，点「从光标处续写」生成正文')
}

async function deleteDirNote(note) {
  try {
    await ElMessageBox.confirm('删除这条便签？不影响正文，也不退费。', '删除便签',
      { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteBranchNote(pid, currentNo.value, note.draft_id)
    await loadDirNotes(currentNo.value)
  } catch (e) {
    ElMessage.error(errorDetail(e, '删除便签失败'))
  }
}

// ---------- 检索 ----------
async function openSearchHit(hit) {
  if (hit.kind === 'settings') {
    ElMessage.info(`设定命中：${hit.ch_title}（设定页签可查全文）`)
    return
  }
  if (hit.ch !== currentNo.value) {
    const ok = await switchChapter(hit.ch)
    if (!ok) return
  }
  // textarea 不支持高亮：定位到命中位置并选中关键词、滚动到可见
  const { offset, end } = searchSelection(hit)
  editorRef.value?.focusAt(offset, end)
}

// ---------- R28 段落浮层 ----------

/** 把选区扩展到所在自然段（与后端 `context.span_snapshot` 的段落口径一致）。 */
function paragraphRangeOf(text, start, end) {
  const body = text || ''
  const head = body.lastIndexOf('\n\n', Math.max(0, start - 1))
  const pStart = head < 0 ? 0 : head + 2
  let pEnd = body.indexOf('\n\n', end)
  if (pEnd < 0) pEnd = body.length
  return { start: pStart, end: Math.max(pStart, pEnd) }
}

function openDock(range, presetText = '') {
  const body = content.value || ''
  const start = Math.max(0, Math.min(range.start, body.length))
  const end = Math.max(start, Math.min(range.end, body.length))
  if (end <= start) { ElMessage.warning('请先选中一段文字'); return }
  dock.value = {
    open: true, start, end,
    paraIndex: body.slice(0, start).split('\n\n').length,
    paragraph: body.slice(start, end),
    preset: presetText,
  }
}

function openDockFromSelection() {
  if (!current.value) return
  if (dirty.value) { ElMessage.warning('请先保存当前修改，再问小墨'); return }
  const sel = selection.value
  if (sel.end <= sel.start) { ElMessage.warning('请先选中一段文字'); return }
  openDock(paragraphRangeOf(content.value, sel.start, sel.end))
  floatBar.value.show = false
}

/** 应用建议后刷新正文/版本（来源是同一个写通道，保留在写作页现场）。 */
async function onDockApplied() {
  dock.value.open = false
  await loadChapter(currentNo.value)
  await loadChapters()
}

/** 全书体检跳转：证据文本唯一命中才自动选段；0 处/多处一律提示手动选段（P0-8）。 */
function handleIssueJump() {
  const evidence = String(route.query.evidence || '')
  const issue = String(route.query.issue || '')
  const advice = String(route.query.advice || '')
  if (!evidence) return
  const body = content.value || ''
  const hits = []
  let pos = body.indexOf(evidence)
  while (pos >= 0) { hits.push(pos); pos = body.indexOf(evidence, pos + 1) }
  if (hits.length !== 1) {
    ElMessage.info(hits.length
      ? `证据原文在本章命中 ${hits.length} 处，请手动选段后再问小墨`
      : '证据原文在当前正文里找不到（可能已修改），请手动选段后再问小墨')
    return
  }
  const range = paragraphRangeOf(body, hits[0], hits[0] + evidence.length)
  editorRef.value?.focusAt(hits[0])
  const preset = `这条体检问题怎么改：${issue || '前后不一致'}\n证据：「${evidence}」`
    + (advice ? `\n已有建议：${advice}` : '')
  openDock(range, preset)
}

watch(dirty, (v) => { if (v) { genToken.value = ''; dirToken.value = '' } })
</script>

<style scoped>
.write-page {
  display: flex; height: calc(100vh - 130px); min-height: 560px;
  border: 1px solid var(--card-line); border-radius: var(--radius);
  overflow: hidden; background: var(--paper); box-shadow: var(--shadow);
  position: relative;
}
.right { width: 360px; flex: 0 0 360px; display: flex; flex-direction: column; background: var(--card); border-left: 1px solid var(--card-line); }
.tabs { display: flex; border-bottom: 1px solid var(--card-line); }
.tab {
  flex: 1; border: 0; background: transparent; padding: 10px 0; cursor: pointer;
  font-family: var(--sans); font-size: 13px; color: var(--muted);
}
.tab.on { color: var(--indigo); font-weight: 600; box-shadow: inset 0 -2px 0 var(--indigo); }
.tab-badge {
  display: inline-block; min-width: 15px; margin-left: 3px; padding: 0 4px;
  border-radius: 8px; background: var(--gold-soft); color: var(--gold-deep);
  font-size: 10px; line-height: 15px; text-align: center;
}
</style>
