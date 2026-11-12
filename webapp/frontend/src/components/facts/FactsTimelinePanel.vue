<template>
  <div class="tl">
    <FactsPanelHead
      title="时间线"
      sub="按章看「已经发生的事件」，以及「发展方向的事件」（埋线、伏笔推进）。虚线卡是作者排的计划事件——只影响写对应章时的约束，不会被当作已发生事实。人物、地点、状态在「世界事实」，检查结论在「全书体检」。"
      :stats="stats"
      :loading="loading"
      :readonly="false"
      @refresh="reload"
    />

    <el-empty v-if="off" description="时间线未启用（后台开关 timeline_view 关闭）">
      <p class="tl-tip">后台「开关管理」打开 timeline_view 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="tl-meta">
        <span class="tl-tag mono">版本 {{ meta.version }}</span>
        <span v-if="meta.sources?.stale" class="tl-tag warn">上游设定已更新，视图可能落后</span>
      </div>

      <!-- 一级页签 + 视图形态 + 筛选 -->
      <div class="tl-toolbar">
        <nav class="tl-tabs">
          <button type="button" class="tl-tab" :class="{ on: tab === 'happened' }" @click="tab = 'happened'">
            已经发生的事件<span class="tl-tab-n">{{ happened.length }}</span>
          </button>
          <button type="button" class="tl-tab" :class="{ on: tab === 'direction' }" @click="switchDirection">
            发展方向的事件<span class="tl-tab-n">{{ directionCount }}</span>
          </button>
        </nav>

        <div v-if="tab === 'happened'" class="tl-seg">
          <button type="button" class="tl-seg-btn" :class="{ on: mode === 'cards' }" @click="mode = 'cards'">卡片流</button>
          <button type="button" class="tl-seg-btn" :class="{ on: mode === 'lanes' }" @click="mode = 'lanes'">泳道</button>
        </div>
        <el-select v-if="tab === 'happened' && mode === 'lanes'" v-model="laneBy" size="small" class="tl-select-sm">
          <el-option value="people" label="按人物分行" />
          <el-option value="kind" label="按事件类型分行" />
        </el-select>

        <div class="tl-filters">
          <el-select v-model="kindFilter" size="small" clearable placeholder="全部类型" class="tl-select-sm">
            <el-option v-for="k in KIND_OPTIONS" :key="k.value" :value="k.value" :label="k.label" />
          </el-select>
          <el-select v-model="participant" size="small" clearable placeholder="按参与人" class="tl-select-sm">
            <el-option v-for="(n, name) in participants" :key="name" :value="name" :label="`${name}（${n}）`" />
          </el-select>
          <el-input v-model="keyword" size="small" clearable placeholder="搜事件内容" class="tl-input" />
          <el-button size="small" type="primary" plain @click="openPlanned()">＋ 计划事件</el-button>
        </div>
      </div>

      <!-- ============ 已经发生的事件 ============ -->
      <section v-show="tab === 'happened'" class="tl-card">
        <header class="tl-card-head">
          <h4 class="tl-h4">已经发生的事件</h4>
          <span class="tl-count">
            写到第 {{ atCh }} 章 · 共 {{ happened.length }} 条
            <template v-if="planned.length"> · 计划 {{ planned.length }} 条（虚线）</template>
          </span>
        </header>

        <!-- L1：卡片流 -->
        <div v-if="mode === 'cards'" class="tl-flow">
          <div v-if="cardGroups.length" class="tl-flow-inner">
            <div v-for="g in cardGroups" :key="g.ch" class="tl-flow-chapter"
                 @dragover.prevent @drop="dropOn(g.ch)">
              <div class="tl-rail">
                <span class="tl-dot"></span>
                <span class="tl-ch-no">第 {{ g.ch }} 章</span>
                <span class="tl-dim">{{ g.items.length }} 条</span>
              </div>
              <div class="tl-cards">
                <article v-for="it in g.items" :key="it.key" class="tl-ev-card"
                         :class="[it.kind, { planned: it.planned }]"
                         :draggable="it.planned"
                         @dragstart="startDrag(it, $event)"
                         @click="openCard(it)">
                  <div class="tl-ev-top">
                    <el-tag size="small" effect="plain" :type="kindTag(it.kind)">{{ it.planned ? '计划' : kindText(it.kind) }}</el-tag>
                    <span v-if="it.status === 'done'" class="tl-done">已完成</span>
                    <span v-if="it.planned" class="tl-drag-hint">可拖动改章</span>
                  </div>
                  <p class="tl-ev-title">{{ it.title }}</p>
                  <p v-if="it.desc && it.desc !== it.title" class="tl-ev-desc">{{ it.desc }}</p>
                  <div v-if="it.participants?.length" class="tl-ev-people">
                    <span v-for="p in it.participants" :key="p" class="tl-person">{{ p }}</span>
                  </div>
                </article>
              </div>
            </div>
          </div>
          <p v-else class="tl-hint">还没有事件记录。事件在写完一章后自动登记；早期章节若在事件流修复前生成，就不会有记录——从下一章开始才会出现。</p>
        </div>

        <!-- L2：泳道 -->
        <div v-else class="tl-board-wrap">
          <div class="tl-board">
            <div class="tl-board-line tl-board-head" :style="gridStyle">
              <div class="tl-lane-head">泳道</div>
              <div v-for="ch in chapters" :key="ch" class="tl-ch-cell">第 {{ ch }} 章</div>
            </div>
            <div v-for="lane in lanes" :key="lane.key" class="tl-board-line tl-board-row" :style="gridStyle">
              <div class="tl-lane-name" :title="lane.gapText">
                <span class="tl-lane-ico">{{ lane.icon }}</span>
                <span class="tl-lane-label">{{ lane.label }}</span>
                <span class="tl-lane-count">{{ lane.total }}</span>
                <span v-if="lane.gapText" class="tl-gap-badge">{{ lane.gapText }}</span>
              </div>
              <div v-for="ch in chapters" :key="ch" class="tl-cell"
                   @dragover.prevent @drop="dropOn(ch)">
                <article v-for="it in cellItems(lane, ch)" :key="it.key" class="tl-mini"
                         :class="[it.kind, { planned: it.planned }]"
                         :draggable="it.planned"
                         @dragstart="startDrag(it, $event)"
                         @click="openCard(it)">
                  <span class="tl-mini-title">{{ shortTitle(it) }}</span>
                  <span v-if="it.planned" class="tl-mini-tag">计划</span>
                </article>
              </div>
            </div>
            <!-- 计划泳道：把虚线卡拖到任意章格即可改落章 -->
            <div v-if="planned.length" class="tl-board-line tl-board-row tl-plan-row" :style="gridStyle">
              <div class="tl-lane-name"><span class="tl-lane-ico">✎</span><span class="tl-lane-label">作者计划</span><span class="tl-lane-count">{{ planned.length }}</span></div>
              <div v-for="ch in chapters" :key="ch" class="tl-cell tl-plan-cell" @dragover.prevent @drop="dropOn(ch)">
                <article v-for="it in plannedIn(ch)" :key="it.key" class="tl-mini planned"
                         draggable @dragstart="startDrag(it, $event)" @click="openCard(it)">
                  <span class="tl-mini-title">{{ shortTitle(it) }}</span>
                  <span class="tl-mini-tag">计划</span>
                </article>
              </div>
            </div>
          </div>
          <p class="tl-board-foot">
            泳道里的空白格＝那几章没有这条线上的事件；泳道名后的「断档」标出最长的一段空档。计划卡可直接拖到别的章格改排期。
          </p>
        </div>
      </section>

      <!-- ============ 发展方向的事件 ============ -->
      <section v-show="tab === 'direction'" class="tl-card">
        <header class="tl-card-head">
          <h4 class="tl-h4">发展方向的事件</h4>
          <span class="tl-count">指向后文的埋线：已埋未收的线索 {{ threads.length }} 条，伏笔类事件 {{ foreshadowEvents.length }} 条</span>
        </header>

        <div v-if="threads.length" class="tl-threads">
          <article v-for="t in threads" :key="t.key" class="tl-thread" :class="t.status">
            <div class="tl-thread-head">
              <strong>{{ t.name }}</strong>
              <el-tag size="small" effect="plain" :type="threadTag(t.status)">{{ threadText(t.status) }}</el-tag>
              <span class="tl-dim">第 {{ t.planted_ch }} 章埋 · 计划第 {{ t.due_ch }} 章收</span>
              <el-button link type="primary" size="small" @click="planPayoff(t)">排一张收束卡</el-button>
            </div>
            <p class="tl-thread-desc">{{ t.desc }}</p>
          </article>
        </div>
        <p v-else class="tl-hint">还没有已埋未收的线索。写章时埋下的伏笔会进这里；也可以先在「＋ 计划事件」里排一张埋线卡。</p>

        <div v-if="foreshadowEvents.length" class="tl-foreshadow">
          <h5 class="tl-h5">伏笔类事件</h5>
          <article v-for="e in foreshadowEvents" :key="e.key" class="tl-ev-card foreshadow" @click="openCard(e)">
            <div class="tl-ev-top"><el-tag size="small" effect="plain" type="warning">伏笔</el-tag><span class="tl-dim">第 {{ e.ch }} 章</span></div>
            <p class="tl-ev-title">{{ e.title }}</p>
            <div v-if="e.participants?.length" class="tl-ev-people">
              <span v-for="p in e.participants" :key="p" class="tl-person">{{ p }}</span>
            </div>
          </article>
        </div>
      </section>
    </template>

    <!-- 事件详情（只读） -->
    <el-dialog v-model="detailOpen" :title="detail?.title || '事件详情'" width="560px">
      <div v-if="detail" class="tl-detail">
        <div class="tl-detail-row"><span>位置</span><b>第 {{ detail.ch }} 章<template v-if="detail.scene"> · 场景 {{ detail.scene }}</template></b></div>
        <div class="tl-detail-row"><span>类型</span><b>{{ detail.planned ? '计划事件' : kindText(detail.kind) }}</b></div>
        <div v-if="detail.desc" class="tl-detail-row"><span>内容</span><b>{{ detail.desc }}</b></div>
        <div v-if="detail.participants?.length" class="tl-detail-row"><span>参与</span><b>{{ detail.participants.join('、') }}</b></div>
        <div v-if="detail.cause" class="tl-detail-row"><span>起因</span><b>{{ detail.cause }}</b></div>
        <div v-if="detail.effect?.length" class="tl-detail-row"><span>影响</span><b>{{ detail.effect.join('；') }}</b></div>
        <div v-if="detail.thread" class="tl-detail-row"><span>线索</span><b>{{ detail.thread }}</b></div>
      </div>
      <template #footer>
        <el-button v-if="detail?.planned" size="small" @click="editPlanned(detail)">编辑</el-button>
        <el-button size="small" type="primary" @click="detailOpen = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 计划事件编辑 -->
    <el-dialog v-model="editOpen" :title="editing.id ? '编辑计划事件' : '新建计划事件'" width="560px">
      <div class="tl-form">
        <label>标题</label>
        <el-input v-model="editing.title" maxlength="80" show-word-limit placeholder="这章要发生什么，一句话" />
        <label>要点</label>
        <el-input v-model="editing.desc" type="textarea" :rows="3" maxlength="600" placeholder="具体怎么写：谁做了什么、结果如何（可留空）" />
        <label>目标章</label>
        <el-input-number v-model="editing.ch" :min="1" :max="9999" size="small" />
        <label>参与人</label>
        <el-select v-model="editing.participants" multiple filterable allow-create default-first-option
                   size="small" placeholder="输入名字，回车添加">
          <el-option v-for="(n, name) in participants" :key="name" :value="name" :label="name" />
        </el-select>
        <label>关联线索</label>
        <el-input v-model="editing.thread" maxlength="80" placeholder="可选：对应某条伏笔的名字" />
        <label>状态</label>
        <el-select v-model="editing.status" size="small">
          <el-option value="planned" label="计划中" />
          <el-option value="done" label="已完成" />
        </el-select>
      </div>
      <template #footer>
        <el-button v-if="editing.id" size="small" type="danger" plain @click="removePlanned">删除</el-button>
        <el-button size="small" @click="editOpen = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="savePlanned">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getFactsGraph, saveFactsPlannedEvents } from '../../api/facts'
import FactsPanelHead from './FactsPanelHead.vue'

const props = defineProps({ pid: { type: String, required: true } })

const loading = ref(false)
const saving = ref(false)
const off = ref(false)
const graph = ref(null)
const tab = ref('happened')
const mode = ref('lanes')
const laneBy = ref('people')
const kindFilter = ref('')
const participant = ref('')
const keyword = ref('')
const events = ref([])
const planned = ref([])
const participants = ref({})
const threads = ref([])
const threadsLoaded = ref(false)
const detailOpen = ref(false)
const detail = ref(null)
const editOpen = ref(false)
const editing = reactive({ id: '', title: '', desc: '', ch: 1, participants: [], thread: '', status: 'planned' })
let dragId = ''

const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔', planned: '计划' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning', planned: 'info' }
const KIND_OPTIONS = [
  { value: 'state', label: '状态变化' },
  { value: 'object', label: '物品流转' },
  { value: 'relationship', label: '关系变化' },
  { value: 'foreshadow', label: '伏笔埋设' },
]
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''

const THREAD_TEXT = { overdue: '逾期未收', due_soon: '该收了', open: '进行中', planted: '刚埋下' }
const THREAD_TAG = { overdue: 'danger', due_soon: 'warning', open: '', planted: 'info' }
const threadText = (s) => THREAD_TEXT[s] || '进行中'
const threadTag = (s) => THREAD_TAG[s] || ''

const meta = computed(() => graph.value)
const atCh = computed(() => graph.value?.data?.at_ch ?? 0)

// 统一成同一种卡片形状，已发生/计划只靠 planned 区分
const happened = computed(() => events.value.filter(e => e.kind !== 'foreshadow' && !e.planned)
  .filter(e => !kindFilter.value || e.kind === kindFilter.value)
  .filter(e => !participant.value || (e.participants || []).includes(participant.value))
  .filter(e => !keyword.value || (e.desc || '').includes(keyword.value.trim()))
  .map(e => ({ ...e, key: e.id || `ev_${e.ch}_${(e.desc || '').slice(0, 8)}`, title: firstLine(e.desc), desc: e.desc })))

const foreshadowEvents = computed(() => events.value
  .filter(e => e.kind === 'foreshadow' && !e.planned)
  .filter(e => !participant.value || (e.participants || []).includes(participant.value))
  .map(e => ({ ...e, key: e.id || `fp_${e.ch}_${(e.desc || '').slice(0, 8)}`, title: firstLine(e.desc) })))

const directionCount = computed(() => (threads.value.length || 0) + foreshadowEvents.value.length)

const plannedItems = computed(() => planned.value
  .filter(p => !participant.value || (p.participants || []).includes(participant.value))
  .filter(p => !keyword.value || `${p.title}${p.desc}`.includes(keyword.value.trim()))
  .map(p => ({ ...p, key: p.id, title: p.title, planned: true })))

const chapters = computed(() => {
  const maxCh = Math.max(atCh.value, ...plannedItems.value.map(p => Number(p.ch) || 0), 1) + 1
  return Array.from({ length: maxCh }, (_, i) => i + 1)
})
const gridStyle = computed(() => ({
  gridTemplateColumns: `160px repeat(${chapters.value.length}, minmax(96px, 1fr))`,
}))

// L1 卡片流：按章分组，已发生 + 计划合并，计划卡排在后面
const cardGroups = computed(() => {
  const map = new Map()
  for (const e of [...happened.value, ...plannedItems.value]) {
    const ch = Number(e.ch) || 0
    if (!map.has(ch)) map.set(ch, [])
    map.get(ch).push(e)
  }
  return [...map.entries()].sort((a, b) => a[0] - b[0]).map(([ch, items]) => ({
    ch, items: items.sort((a, b) => Number(a.planned) - Number(b.planned)),
  }))
})

// L2 泳道：按人物或类型分行，计划卡单独一行
const lanes = computed(() => {
  if (laneBy.value === 'kind') {
    return ['state', 'object', 'relationship', 'foreshadow'].map(k => {
      const items = happened.value.filter(e => e.kind === k)
      return { key: k, label: kindText(k), icon: '•', total: items.length, items, gapText: gapText(items) }
    }).filter(l => l.total)
  }
  const names = [...new Set(happened.value.flatMap(e => e.participants || []))]
  return names.map(name => {
    const items = happened.value.filter(e => (e.participants || []).includes(name))
    return { key: name, label: name, icon: name.slice(0, 1), total: items.length, items, gapText: gapText(items) }
  })
})

const stats = computed(() => [
  { label: '写到第几章', value: atCh.value },
  { label: '已发生', value: happened.value.length },
  { label: '发展方向', value: directionCount.value },
  { label: '计划', value: planned.value.length },
])

function firstLine(s) {
  const t = String(s || '').trim()
  return t.length > 26 ? t.slice(0, 26) + '…' : (t || '（无描述）')
}
function shortTitle(it) {
  const t = String(it.title || it.desc || '').trim()
  return t.length > 14 ? t.slice(0, 14) + '…' : t
}
// 断档：该泳道相邻卡片之间跨了几章（只报最长一段）
function gapText(items) {
  const chs = [...new Set(items.map(e => Number(e.ch)).filter(Boolean))].sort((a, b) => a - b)
  let best = null
  for (let i = 1; i < chs.length; i++) {
    const gap = chs[i] - chs[i - 1] - 1
    if (gap >= 2 && (!best || gap > best.gap)) best = { gap, from: chs[i - 1] + 1, to: chs[i] - 1 }
  }
  return best ? `断档 ${best.gap} 章（${best.from}–${best.to}）` : ''
}
function cellItems(lane, ch) {
  return lane.items.filter(e => Number(e.ch) === Number(ch))
}
function plannedIn(ch) {
  return plannedItems.value.filter(p => Number(p.ch) === Number(ch))
}

function switchDirection() {
  tab.value = 'direction'
  if (!threadsLoaded.value) loadThreads()
}

function openCard(it) {
  detail.value = it
  detailOpen.value = true
}

function startDrag(it, ev) {
  if (!it.planned) return
  dragId = it.id
  try {
    ev.dataTransfer.setData('text/plain', it.id)
    ev.dataTransfer.effectAllowed = 'move'
  } catch (e) { /* 浏览器差异，忽略 */ }
}
async function dropOn(ch) {
  if (!dragId) return
  const item = planned.value.find(p => p.id === dragId)
  dragId = ''
  if (!item || Number(item.ch) === Number(ch)) return
  const next = planned.value.map(p => p.id === item.id ? { ...p, ch: Number(ch) } : p)
  await persist(next, `已移到第 ${ch} 章`)
}

function openPlanned(seed = {}) {
  Object.assign(editing, {
    id: '', title: seed.title || '', desc: seed.desc || '', ch: seed.ch || atCh.value + 1,
    participants: seed.participants ? [...seed.participants] : [], thread: seed.thread || '', status: 'planned',
  })
  editOpen.value = true
}
function editPlanned(it) {
  detailOpen.value = false
  Object.assign(editing, {
    id: it.id, title: it.title, desc: it.desc || '', ch: Number(it.ch) || 1,
    participants: [...(it.participants || [])], thread: it.thread || '', status: it.status || 'planned',
  })
  editOpen.value = true
}
function planPayoff(t) {
  openPlanned({ title: `收束：${t.name}`, desc: t.desc || '', ch: t.due_ch || atCh.value + 1, thread: t.name })
}

async function savePlanned() {
  if (!editing.title.trim()) { ElMessage.warning('标题不能为空'); return }
  if (!Number(editing.ch)) { ElMessage.warning('目标章必须是数字'); return }
  const one = {
    id: editing.id || undefined, title: editing.title.trim(), desc: editing.desc.trim(),
    ch: Number(editing.ch), participants: editing.participants, thread: editing.thread.trim(),
    status: editing.status, kind: 'planned',
  }
  const next = editing.id
    ? planned.value.map(p => p.id === editing.id ? { ...p, ...one } : p)
    : [...planned.value, one]
  const ok = await persist(next, editing.id ? '已保存' : '已新增计划事件')
  if (ok) editOpen.value = false
}
async function removePlanned() {
  const next = planned.value.filter(p => p.id !== editing.id)
  const ok = await persist(next, '已删除')
  if (ok) editOpen.value = false
}

async function persist(next, okMsg) {
  saving.value = true
  try {
    const resp = await saveFactsPlannedEvents(props.pid, next.map(p => ({
      id: p.id, title: p.title, desc: p.desc, ch: p.ch,
      participants: p.participants || [], thread: p.thread || '',
      status: p.status || 'planned', kind: p.kind || 'planned',
    })))
    planned.value = (resp?.items || []).map(p => ({ ...p, key: p.id, planned: true }))
    if (okMsg) ElMessage.success(okMsg)
    return true
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败，稍后再试一次')
    return false
  } finally {
    saving.value = false
  }
}

async function loadThreads() {
  try {
    const d = await getFactsGraph(props.pid, { view: 'threads' })
    threads.value = d?.data?.threads || []
    threadsLoaded.value = true
  } catch (e) {
    threads.value = []
  }
}

async function reload() {
  loading.value = true
  off.value = false
  try {
    graph.value = await getFactsGraph(props.pid, { view: 'timeline' })
    events.value = graph.value?.data?.events || []
    planned.value = (graph.value?.data?.planned || []).map(p => ({ ...p, key: p.id, planned: true }))
    participants.value = graph.value?.data?.participants || {}
    threadsLoaded.value = false
  } catch (e) {
    if (e?.response?.status === 404) off.value = true
    graph.value = null
    events.value = []
    planned.value = []
    participants.value = {}
  } finally {
    loading.value = false
  }
  if (tab.value === 'direction') loadThreads()
}

onMounted(reload)
watch(() => props.pid, reload)
</script>

<style scoped>
.tl { display: flex; flex-direction: column; gap: 12px; }
.tl-tip { font-size: 13px; color: var(--muted); }
.tl-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.tl-tag {
  font-size: 12px; color: var(--ink-soft); background: var(--chip, rgba(0, 0, 0, .04));
  border-radius: 6px; padding: 2px 8px;
}
.tl-tag.mono { font-family: var(--mono, ui-monospace, monospace); }
.tl-tag.warn { color: var(--gold-deep); }

.tl-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.tl-tabs { display: flex; flex-wrap: wrap; gap: 6px; }
.tl-tab {
  border: 1px solid var(--card-line); background: transparent; color: var(--ink-soft);
  border-radius: 999px; padding: 4px 12px; font-size: 13px; cursor: pointer;
  display: inline-flex; align-items: center; gap: 6px;
}
.tl-tab.on { border-color: var(--indigo, #5b5bd6); color: var(--indigo, #5b5bd6); font-weight: 600; }
.tl-tab-n { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.tl-seg { display: inline-flex; border: 1px solid var(--card-line); border-radius: 6px; overflow: hidden; }
.tl-seg-btn {
  border: none; background: transparent; padding: 4px 10px; font-size: 12px; cursor: pointer; color: var(--ink-soft);
}
.tl-seg-btn.on { background: var(--indigo, #5b5bd6); color: #fff; }
.tl-filters { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-left: auto; }
.tl-select-sm { width: 130px; }
.tl-input { width: 160px; }

.tl-card {
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 12px 14px;
}
.tl-card-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
  flex-wrap: wrap; margin-bottom: 10px;
}
.tl-h4 { font-size: 14px; margin: 0; }
.tl-h5 { font-size: 13px; margin: 14px 0 8px; color: var(--ink-soft); }
.tl-count { font-size: 12px; color: var(--muted); }
.tl-dim { color: var(--muted); font-size: 12px; }
.tl-hint { font-size: 12px; color: var(--muted); line-height: 1.8; margin: 6px 0 0; }

/* L1 卡片流 */
.tl-flow-inner { display: flex; flex-direction: column; gap: 14px; }
.tl-flow-chapter { display: flex; gap: 12px; align-items: flex-start; }
.tl-rail { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 120px; flex: none; }
.tl-dot {
  width: 10px; height: 10px; border-radius: 50%; background: var(--indigo, #5b5bd6);
  box-shadow: 0 0 0 3px rgba(91, 91, 214, .12);
}
.tl-ch-no { font-size: 13px; font-weight: 600; }
.tl-cards { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 0; }
.tl-ev-card {
  border: 1px solid var(--card-line); border-left: 3px solid var(--indigo, #5b5bd6);
  border-radius: 8px; padding: 8px 10px; background: var(--card); cursor: pointer;
  display: flex; flex-direction: column; gap: 4px;
}
.tl-ev-card:hover { border-color: var(--indigo, #5b5bd6); }
.tl-ev-card.object { border-left-color: #2f9e6f; }
.tl-ev-card.relationship { border-left-color: #3a7bd5; }
.tl-ev-card.foreshadow { border-left-color: #d9a441; }
.tl-ev-card.planned, .tl-mini.planned {
  border-style: dashed; border-left-style: dashed;
  background: repeating-linear-gradient(135deg, rgba(91, 91, 214, .04) 0 8px, transparent 8px 16px);
}
.tl-ev-top { display: flex; align-items: center; gap: 8px; }
.tl-ev-title { margin: 0; font-size: 13px; font-weight: 600; line-height: 1.6; color: var(--ink); }
.tl-ev-desc { margin: 0; font-size: 12px; line-height: 1.7; color: var(--ink-soft); }
.tl-ev-people { display: flex; flex-wrap: wrap; gap: 4px; }
.tl-person {
  font-size: 11px; color: var(--ink-soft); border: 1px solid var(--card-line);
  border-radius: 999px; padding: 1px 8px;
}
.tl-done { font-size: 11px; color: #2f9e6f; }
.tl-drag-hint { font-size: 11px; color: var(--muted); }

/* L2 泳道 */
.tl-board-wrap { overflow-x: auto; padding-bottom: 4px; }
.tl-board { min-width: 720px; }
.tl-board-line { display: grid; }
.tl-lane-head, .tl-ch-cell {
  font-size: 11px; color: var(--muted); padding: 4px 6px; border-bottom: 1px solid var(--card-line);
  position: sticky; top: 0; background: var(--card); z-index: 1;
}
.tl-lane-head { position: sticky; left: 0; z-index: 2; }
.tl-ch-cell { text-align: center; }
.tl-board-row { border-bottom: 1px dashed var(--card-line); }
.tl-lane-name {
  display: flex; align-items: center; gap: 6px; padding: 6px; font-size: 12px;
  position: sticky; left: 0; background: var(--card); z-index: 1;
}
.tl-lane-ico {
  width: 20px; height: 20px; border-radius: 50%; flex: none; font-size: 11px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--chip, rgba(0, 0, 0, .05)); color: var(--ink-soft);
}
.tl-lane-label { font-weight: 600; max-width: 66px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tl-lane-count { font-size: 11px; color: var(--muted); }
.tl-gap-badge { font-size: 10px; color: var(--gold-deep); border: 1px solid var(--card-line); border-radius: 999px; padding: 1px 6px; }
.tl-cell { min-height: 52px; padding: 4px; display: flex; flex-direction: column; gap: 4px; border-left: 1px dotted var(--card-line); }
.tl-mini {
  border: 1px solid var(--card-line); border-left: 2px solid var(--indigo, #5b5bd6);
  border-radius: 6px; padding: 4px 6px; font-size: 11px; line-height: 1.45; cursor: pointer;
  display: flex; flex-direction: column; gap: 2px; background: var(--card);
}
.tl-mini:hover { border-color: var(--indigo, #5b5bd6); }
.tl-mini.object { border-left-color: #2f9e6f; }
.tl-mini.relationship { border-left-color: #3a7bd5; }
.tl-mini.foreshadow { border-left-color: #d9a441; }
.tl-mini-title { color: var(--ink-soft); }
.tl-mini-tag { font-size: 10px; color: var(--indigo, #5b5bd6); }
.tl-plan-row { background: rgba(91, 91, 214, .03); }
.tl-board-foot { font-size: 11px; color: var(--muted); line-height: 1.7; margin: 8px 0 0; }

/* 发展方向 */
.tl-threads { display: flex; flex-direction: column; gap: 8px; }
.tl-thread { border: 1px solid var(--card-line); border-left: 3px solid var(--indigo, #5b5bd6); border-radius: 8px; padding: 8px 10px; }
.tl-thread.overdue { border-left-color: #d9534f; }
.tl-thread.due_soon { border-left-color: #d9a441; }
.tl-thread-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tl-thread-head strong { font-size: 13px; }
.tl-thread-desc { margin: 4px 0 0; font-size: 12px; line-height: 1.7; color: var(--ink-soft); }
.tl-foreshadow { display: flex; flex-direction: column; gap: 8px; }

/* 详情 / 表单 */
.tl-detail { display: flex; flex-direction: column; gap: 8px; }
.tl-detail-row { display: flex; gap: 14px; font-size: 13px; line-height: 1.7; }
.tl-detail-row span { width: 56px; flex: none; color: var(--muted); }
.tl-form { display: flex; flex-direction: column; gap: 6px; }
.tl-form label { font-size: 12px; color: var(--muted); margin-top: 6px; }
</style>
