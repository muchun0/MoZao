<template>
  <div class="ft">
    <header class="ft-head">
      <div>
        <h3 class="ft-title">时间线</h3>
        <p class="ft-sub">
          只读视图：事件按章追加、伏笔按唯一源取数、冲突由确定性规则算出。这里只看不改——修正动作回到「章节」或「世界观」。
        </p>
      </div>
      <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
    </header>

    <!-- 开关未开：说清能力状态，不伪造数据 -->
    <el-empty v-if="off" description="时间线未启用（后台开关 timeline_view 关闭）">
      <p class="ft-tip">M1 的四个查询接口在开关关闭时一律不返回数据。开启后回到这里刷新即可。</p>
    </el-empty>

    <template v-else>
      <el-tabs v-model="tab" class="ft-tabs">
        <el-tab-pane label="章节时间线" name="chapter">
          <el-table v-if="chapterRows.length" :data="chapterRows" size="small" border>
            <el-table-column prop="ch" label="章" width="72" />
            <el-table-column label="事件" min-width="320">
              <template #default="{ row }">{{ row.summary || '（本章无可记录事件）' }}</template>
            </el-table-column>
            <el-table-column prop="kind" label="类型" width="96" />
          </el-table>
          <el-empty v-else description="还没有事件记录（写完一章并开启 event_stream 后会自动追加）" />
        </el-tab-pane>

        <el-tab-pane label="伏笔线索" name="thread">
          <div class="ft-filter">
            <el-radio-group v-model="threadStatus" size="small" @change="loadThreads">
              <el-radio-button value="">全部未回收</el-radio-button>
              <el-radio-button value="overdue">已逾期</el-radio-button>
              <el-radio-button value="due_soon">临期</el-radio-button>
              <el-radio-button value="stalled">长期未推进</el-radio-button>
            </el-radio-group>
          </div>
          <el-table v-if="threadRows.length" :data="threadRows" size="small" border>
            <el-table-column prop="name" label="伏笔" min-width="180" />
            <el-table-column prop="planted_ch" label="埋设章" width="90" />
            <el-table-column prop="due_ch" label="应收章" width="90" />
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="说明" min-width="240">
              <template #default="{ row }">{{ row.desc || '—' }}</template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="没有符合条件的伏笔（或本书还没登记伏笔）" />
        </el-tab-pane>

        <el-tab-pane label="人物脉络" name="character">
          <el-table v-if="characterRows.length" :data="characterRows" size="small" border>
            <el-table-column prop="name" label="人物" width="140" />
            <el-table-column label="当前状态" min-width="300">
              <template #default="{ row }">{{ row.summary || '—' }}</template>
            </el-table-column>
            <el-table-column prop="ch" label="最近章" width="90" />
          </el-table>
          <el-empty v-else description="还没有人物状态记录" />
        </el-tab-pane>

        <el-tab-pane label="冲突提醒" name="violation">
          <div class="ft-filter">
            <el-radio-group v-model="severity" size="small" @change="loadViolations">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="high">高</el-radio-button>
              <el-radio-button value="medium">中</el-radio-button>
              <el-radio-button value="low">低</el-radio-button>
            </el-radio-group>
          </div>
          <el-alert v-if="violationRows.length" type="warning" :closable="false" show-icon
                    title="以下冲突只提示，不会自动改稿；改哪一侧由你决定。" class="ft-alert" />
          <el-table v-if="violationRows.length" :data="violationRows" size="small" border>
            <el-table-column prop="ch" label="章" width="72" />
            <el-table-column label="规则" width="132">
              <template #default="{ row }">{{ ruleText(row.rule) }}</template>
            </el-table-column>
            <el-table-column prop="expected" label="应为" min-width="200" />
            <el-table-column prop="actual" label="本章" min-width="200" />
            <el-table-column prop="evidence_ch" label="依据章" width="90" />
          </el-table>
          <el-empty v-else description="没有命中确定性冲突规则" />
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { getFactsThreads, getFactsTimeline, getFactsViolations } from '../../api/facts'

const props = defineProps({
  pid: { type: String, required: true },
})

const tab = ref('chapter')
const loading = ref(false)
const off = ref(false)
const chapterRows = ref([])
const characterRows = ref([])
const threadRows = ref([])
const violationRows = ref([])
const threadStatus = ref('')
const severity = ref('')

const STATUS_TEXT = { overdue: '已逾期', due: '临期', due_soon: '临期', stalled: '长期未推进', buried: '未回收', resolved: '已回收' }
const RULE_TEXT = {
  mechanism_delta: '机制数字方向',
  death_state: '死亡后仍行动',
  item_owner: '物品归属',
  address_drift: '称谓漂移',
  time_reverse: '时间倒退',
}

const statusText = (s) => STATUS_TEXT[s] || s || '—'
const statusTag = (s) => (s === 'overdue' ? 'danger' : s === 'due_soon' ? 'warning' : s === 'stalled' ? 'info' : '')
const ruleText = (r) => RULE_TEXT[r] || r || '—'

// 404 = 开关未开（后端按契约统一返回 404，不返回半真数据），不当作错误弹窗
const isOff = (e) => e?.response?.status === 404

async function loadTimeline() {
  const data = await getFactsTimeline(props.pid, { view: 'chapter' })
  chapterRows.value = (data?.items || []).filter(r => r.summary)
  const chars = await getFactsTimeline(props.pid, { view: 'character' })
  characterRows.value = chars?.items || []
}

async function loadThreads() {
  const data = await getFactsThreads(props.pid, { status: threadStatus.value || undefined, limit: 200 })
  threadRows.value = data?.threads || []
}

async function loadViolations() {
  const data = await getFactsViolations(props.pid, { severity: severity.value || undefined, limit: 100 })
  violationRows.value = data?.violations || []
}

async function reload() {
  loading.value = true
  off.value = false
  try {
    await Promise.all([loadTimeline(), loadThreads(), loadViolations()])
  } catch (e) {
    if (isOff(e)) off.value = true
    // 其它错误保持静默：页面已展示「无数据」态，避免和体检页重复弹提示
  } finally {
    loading.value = false
  }
}

onMounted(reload)
watch(() => props.pid, reload)
</script>

<style scoped>
.ft { display: flex; flex-direction: column; gap: 12px; }
.ft-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.ft-title { font-size: 18px; margin: 0 0 4px; }
.ft-sub { margin: 0; font-size: 13px; color: var(--muted); max-width: 720px; line-height: 1.6; }
.ft-tip { font-size: 13px; color: var(--muted); }
.ft-filter { margin-bottom: 10px; }
.ft-alert { margin-bottom: 10px; }
.ft-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
</style>
