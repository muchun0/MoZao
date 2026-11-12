<template>
  <div class="sg">
    <header class="sg-head">
      <div>
        <h3 class="sg-title">状态图谱</h3>
        <p class="sg-sub">
          只读投影：同一份账本给出四张视图（世界事实 / 角色认知 / 发展线 / 可能分支），
          带内容指纹与来源规模。这里只看不改——要改请回「章节」或「世界观」。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
    </header>

    <el-empty v-if="off" description="状态图谱未启用（后台开关 state_graph 关闭）">
      <p class="sg-tip">后台「开关管理」打开 state_graph 后回到这里刷新即可，无需重新生成。</p>
    </el-empty>

    <template v-else>
      <div v-if="meta" class="sg-meta">
        <span class="sg-tag">版本 {{ meta.version }}</span>
        <span class="sg-tag">写到第 {{ meta.sources?.at_ch || 0 }} 章</span>
        <span class="sg-tag">事件 {{ meta.sources?.events ?? 0 }}</span>
        <span class="sg-tag">伏笔 {{ meta.sources?.threads ?? 0 }}</span>
        <span class="sg-tag">术语 {{ meta.sources?.terms ?? 0 }}</span>
        <span class="sg-tag">纪律 {{ meta.sources?.disciplines ?? 0 }}</span>
        <el-tag v-if="meta.sources?.stale" type="warning" size="small" effect="plain">上游已更新</el-tag>
      </div>

      <el-tabs v-model="tab" class="sg-tabs">
        <!-- 世界事实 -->
        <el-tab-pane label="世界事实" name="canon">
          <div class="sg-block">
            <h4 class="sg-h4">术语 <span class="sg-count">{{ canon.terms?.length || 0 }}</span></h4>
            <el-table v-if="canon.terms?.length" :data="canon.terms" size="small" border>
              <el-table-column prop="term" label="词条" width="180" />
              <el-table-column prop="canonical" label="规范写法" width="180" />
              <el-table-column prop="category" label="分类" width="120" />
            </el-table>
            <el-empty v-else description="还没有术语（世界观生成或同步后出现）" :image-size="56" />
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">永久纪律 <span class="sg-count">{{ canon.disciplines?.length || 0 }}</span></h4>
            <ul v-if="canon.disciplines?.length" class="sg-list">
              <li v-for="(d, i) in canon.disciplines" :key="i">
                <span>{{ d.rule }}</span>
                <span v-if="d.example" class="sg-dim">（示例：{{ d.example }}）</span>
              </li>
            </ul>
            <el-empty v-else description="还没有登记永久纪律" :image-size="56" />
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">机制基线</h4>
            <div v-if="baselineRows.length" class="sg-kv">
              <div v-for="row in baselineRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">{{ row.k }}</span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <el-empty v-else description="还没有机制基线（面板数字类字段会从这里取口径）" :image-size="56" />
            <template v-if="registryRows.length">
              <p class="sg-hint">派生注册表（正文里出现的机制字段必须在这里登记，否则按违规回退整章）：</p>
              <el-table :data="registryRows" size="small" border>
                <el-table-column prop="field" label="字段" width="180" />
                <el-table-column prop="type" label="类型" width="110" />
              </el-table>
            </template>
          </div>

          <div class="sg-block">
            <h4 class="sg-h4">不变量与禁用词</h4>
            <div v-if="invariantRows.length" class="sg-kv">
              <div v-for="row in invariantRows" :key="row.k" class="sg-kv-row">
                <span class="sg-k">{{ row.k }}</span>
                <span class="sg-v">{{ row.v }}</span>
              </div>
            </div>
            <p v-else class="sg-hint">还没有不变量记录。</p>
            <div v-if="canon.forbidden?.length" class="sg-tags">
              <el-tag v-for="w in canon.forbidden" :key="w" type="danger" size="small" effect="plain">{{ w }}</el-tag>
            </div>
            <p v-else class="sg-hint">没有登记禁用词。</p>
          </div>
        </el-tab-pane>

        <!-- 角色认知 -->
        <el-tab-pane label="角色认知" name="characters">
          <el-table v-if="characters.length" :data="characters" size="small" border>
            <el-table-column prop="name" label="人物" width="130" />
            <el-table-column prop="state" label="当前状态" min-width="220" />
            <el-table-column prop="location" label="所在地" min-width="150" />
            <el-table-column prop="goal" label="当前目标" min-width="220" />
            <el-table-column prop="last_ch" label="最近章" width="90" />
            <el-table-column prop="source" label="来源" width="150" />
          </el-table>
          <el-empty v-else description="还没有角色状态记录（写章入账后出现）" />
        </el-tab-pane>

        <!-- 发展线 -->
        <el-tab-pane label="发展线" name="timeline">
          <div class="sg-filter">
            <el-select v-model="participant" size="small" clearable placeholder="按参与人筛选"
                       style="width: 200px">
              <el-option v-for="(n, name) in (timeline.participants || {})" :key="name"
                         :label="`${name}（${n}）`" :value="name" />
            </el-select>
            <span class="sg-hint">共 {{ filteredEvents.length }} 条事件，按章分组</span>
          </div>
          <div v-if="byCh.length" class="sg-tl">
            <div v-for="g in byCh" :key="g.ch" class="sg-tl-group">
              <div class="sg-tl-ch">第 {{ g.ch }} 章 <span class="sg-dim">{{ g.count }} 条</span></div>
              <ul class="sg-list">
                <li v-for="(d, i) in g.descs" :key="i">{{ d }}</li>
              </ul>
            </div>
          </div>
          <el-empty v-else description="还没有事件（开启 event_stream 并写完一章后会追加）" />
        </el-tab-pane>

        <!-- 可能分支 -->
        <el-tab-pane label="可能分支" name="threads">
          <el-table v-if="threads.length" :data="threads" size="small" border>
            <el-table-column prop="name" label="伏笔" min-width="180" />
            <el-table-column prop="planted_ch" label="埋设章" width="90" />
            <el-table-column prop="due_ch" label="应收章" width="90" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="level" label="层级" width="90" />
            <el-table-column prop="desc" label="说明" min-width="260" />
          </el-table>
          <el-empty v-else description="没有未回收伏笔（或本书还没登记伏笔）" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getFactsGraph } from '../../api/facts'

const props = defineProps({ pid: { type: String, required: true } })

const loading = ref(false)
const off = ref(false)
const graph = ref(null)
const tab = ref('canon')
const participant = ref('')

const STATUS_TEXT = { overdue: '已逾期', due: '临期', due_soon: '临期', stalled: '长期未推进', buried: '未回收', resolved: '已回收' }
const statusText = (s) => STATUS_TEXT[s] || s || '—'
const statusTag = (s) => (s === 'overdue' ? 'danger' : s === 'due_soon' ? 'warning' : s === 'stalled' ? 'info' : '')

const meta = computed(() => graph.value)
const canon = computed(() => graph.value?.views?.canon || {})
const characters = computed(() => graph.value?.views?.characters?.characters || [])
const timeline = computed(() => graph.value?.views?.timeline || {})
const filteredEvents = computed(() => {
  const rows = timeline.value.events || []
  if (!participant.value) return rows
  return rows.filter((r) => (r.participants || []).includes(participant.value))
})
const byCh = computed(() => {
  const groups = new Map()
  for (const r of filteredEvents.value) {
    const ch = Number(r.ch) || 0
    if (!groups.has(ch)) groups.set(ch, [])
    groups.get(ch).push(r.desc || '')
  }
  return [...groups.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([ch, descs]) => ({ ch, count: descs.length, descs }))
})
const threads = computed(() => graph.value?.views?.threads?.threads || [])

const baselineRows = computed(() =>
  Object.entries(canon.value.mechanism_baseline || {}).map(([k, v]) => (
    { k, v: typeof v === 'object' ? JSON.stringify(v) : String(v) })))
const registryRows = computed(() =>
  Object.entries(canon.value.registry || {}).map(([field, meta]) => (
    { field, type: typeof meta === 'string' ? meta : (meta?.type || '—') })))
const invariantRows = computed(() =>
  Object.entries(canon.value.invariants || {}).map(([k, v]) => (
    { k, v: typeof v === 'object' ? JSON.stringify(v) : String(v) })))

async function reload() {
  loading.value = true
  off.value = false
  try {
    const params = { view: 'all' }
    graph.value = await getFactsGraph(props.pid, params)
  } catch (e) {
    if (e?.response?.status === 404) off.value = true
    graph.value = null
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => props.pid, reload)
</script>

<style scoped>
.sg { display: flex; flex-direction: column; gap: 12px; }
.sg-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.sg-title { font-size: 18px; margin: 0 0 4px; }
.sg-sub { margin: 0; font-size: 13px; color: var(--muted); max-width: 760px; line-height: 1.6; }
.sg-tip { font-size: 13px; color: var(--muted); }
.sg-meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.sg-tag { font-size: 12px; color: var(--ink-soft); background: var(--indigo-soft); border-radius: 6px; padding: 2px 8px; font-variant-numeric: tabular-nums; }
.sg-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.sg-block { margin-bottom: 18px; }
.sg-h4 { font-size: 14px; margin: 0 0 8px; display: flex; align-items: center; gap: 8px; }
.sg-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sg-list { margin: 0; padding-left: 18px; line-height: 1.8; font-size: 13px; color: var(--ink-soft); }
.sg-dim { color: var(--muted); font-size: 12px; }
.sg-hint { font-size: 12px; color: var(--muted); margin: 8px 0; }
.sg-kv { display: flex; flex-direction: column; gap: 4px; }
.sg-kv-row { display: flex; gap: 12px; font-size: 13px; }
.sg-k { width: 180px; color: var(--muted); flex-shrink: 0; }
.sg-v { color: var(--ink-soft); word-break: break-all; }
.sg-tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.sg-filter { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.sg-tl { display: flex; flex-direction: column; gap: 12px; }
.sg-tl-group { border-left: 2px solid var(--indigo-soft); padding-left: 12px; }
.sg-tl-ch { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
</style>
