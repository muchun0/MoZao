<template>
  <div class="ft">
    <FactsPanelHead
      title="时间线"
      sub="只读视图：事件按章追加、伏笔按唯一源取数、冲突由确定性规则算出。这里只看不改——修正动作回到「章节」或「世界观」。"
      :stats="stats"
      :loading="loading"
      @refresh="reload"
    />

    <!-- 开关未开：说清能力状态，不伪造数据 -->
    <el-empty v-if="off" description="时间线未启用（后台开关 timeline_view 关闭）">
      <p class="ft-tip">M1 的四个查询接口在开关关闭时一律不返回数据。开启后回到这里刷新即可。</p>
    </el-empty>

    <template v-else>
      <el-tabs v-model="tab" class="ft-tabs">
        <el-tab-pane name="chapter">
          <template #label>章节时间线 <span class="ft-tab-count">{{ chapterRows.length }}</span></template>
          <el-table v-if="chapterRows.length" :data="chapterRows" size="small" class="ft-table">
            <el-table-column label="章" width="88">
              <template #default="{ row }"><span class="ft-ch">第 {{ row.ch }} 章</span></template>
            </el-table-column>
            <el-table-column label="发生了什么" min-width="340">
              <template #default="{ row }">{{ row.summary || '（本章无可记录事件）' }}</template>
            </el-table-column>
            <el-table-column label="类型" width="96">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="kindTag(row.kind)">{{ kindText(row.kind) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="还没有事件记录">
            <p class="ft-empty-hint">
              事件在写完一章后自动追加。早期章节如果是在事件流修复前生成的，就不会有记录——从下一章开始才会出现。
            </p>
          </el-empty>
        </el-tab-pane>

        <el-tab-pane name="thread">
          <template #label>伏笔线索 <span class="ft-tab-count">{{ threadRows.length }}</span></template>
          <div class="ft-filter">
            <el-radio-group v-model="threadStatus" size="small">
              <el-radio-button value="">全部未回收</el-radio-button>
              <el-radio-button value="overdue">已逾期</el-radio-button>
              <el-radio-button value="due_soon">临期</el-radio-button>
              <el-radio-button value="stalled">长期未推进</el-radio-button>
            </el-radio-group>
            <span class="ft-count-note">当前筛选 {{ threadRows.length }} 条</span>
          </div>
          <el-table v-if="threadRows.length" :data="threadRows" size="small" class="ft-table"
                   :row-class-name="threadRowClass">
            <el-table-column label="伏笔" min-width="200">
              <template #default="{ row }">
                <div class="ft-main">{{ row.name }}</div>
                <div class="ft-sub">{{ row.desc || '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="埋设 / 应收" width="150">
              <template #default="{ row }">
                <span class="ft-ch-sm">第 {{ row.planted_ch }} 章</span>
                <span class="ft-arrow">→</span>
                <span class="ft-ch-sm" :class="{ 'ft-overdue': row.status === 'overdue' }">第 {{ row.due_ch }} 章</span>
              </template>
            </el-table-column>
            <el-table-column label="层级" width="80">
              <template #default="{ row }">{{ levelText(row.level) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <el-tag :type="statusTag(row.status)" size="small" effect="plain">{{ statusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else :description="threadStatus ? '这个状态下暂时没有伏笔' : '没有未回收的伏笔'">
            <p class="ft-empty-hint">
              伏笔来自世界观：给线索写上埋在第几章、打算第几章收，这里就会显示进度和逾期情况。
            </p>
          </el-empty>
        </el-tab-pane>

        <el-tab-pane name="character">
          <template #label>人物脉络 <span class="ft-tab-count">{{ characterRows.length }}</span></template>
          <el-table v-if="characterRows.length" :data="characterRows" size="small" class="ft-table">
            <el-table-column label="人物" width="140">
              <template #default="{ row }">
                <span class="ft-avatar">{{ (row.name || '?').slice(0, 1) }}</span>
                <strong class="ft-name">{{ row.name }}</strong>
              </template>
            </el-table-column>
            <el-table-column label="状态 / 所在地 / 目标" min-width="380">
              <template #default="{ row }">{{ row.summary || '—' }}</template>
            </el-table-column>
            <el-table-column label="最近章" width="96">
              <template #default="{ row }"><span class="ft-ch-sm">第 {{ row.ch }} 章</span></template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="还没有人物状态记录">
            <p class="ft-empty-hint">写章时会把人物状态、所在地、当前目标记进账本，这里按最近一次记录汇总。</p>
          </el-empty>
        </el-tab-pane>

        <el-tab-pane name="violation">
          <template #label>冲突提醒 <span class="ft-tab-count">{{ violationRows.length }}</span></template>
          <div class="ft-filter">
            <el-radio-group v-model="severity" size="small">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="high">必须处理</el-radio-button>
              <el-radio-button value="medium">建议处理</el-radio-button>
              <el-radio-button value="low">了解一下</el-radio-button>
            </el-radio-group>
            <span class="ft-count-note">当前筛选 {{ violationRows.length }} 条</span>
          </div>
          <el-alert v-if="violationRows.length" type="warning" :closable="false" show-icon
                    title="以下冲突只提示，不会自动改稿；改哪一侧由你决定。" class="ft-alert" />
          <el-table v-if="violationRows.length" :data="violationRows" size="small" class="ft-table">
            <el-table-column label="章" width="82">
              <template #default="{ row }"><span class="ft-ch">第 {{ row.ch }} 章</span></template>
            </el-table-column>
            <el-table-column label="规则" width="120">
              <template #default="{ row }">{{ ruleText(row.rule) }}</template>
            </el-table-column>
            <el-table-column label="轻重" width="96">
              <template #default="{ row }">
                <el-tag :type="severityTag(row.severity)" size="small" effect="plain">{{ severityText(row.severity) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="账本应为" min-width="190">
              <template #default="{ row }"><span class="ft-expect">{{ row.expected }}</span></template>
            </el-table-column>
            <el-table-column label="本章写成" min-width="190">
              <template #default="{ row }"><span class="ft-actual">{{ row.actual }}</span></template>
            </el-table-column>
            <el-table-column label="依据章" width="88">
              <template #default="{ row }">
                <span class="ft-ch-sm">{{ row.evidence_ch ? `第 ${row.evidence_ch} 章` : '—' }}</span>
              </template>
            </el-table-column>
          </el-table>
          <el-empty v-else description="没有命中确定性冲突规则">
            <p class="ft-empty-hint">
              冲突规则只比对账本里已登记的事实（数字方向、死亡后行动、物品归属、称谓、时间）。
              没报不等于没问题，只是这几类没有对不上的地方。
            </p>
          </el-empty>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getFactsThreads, getFactsTimeline, getFactsViolations } from '../../api/facts'
import FactsPanelHead from './FactsPanelHead.vue'

const props = defineProps({
  pid: { type: String, required: true },
})

const tab = ref('chapter')
const loading = ref(false)
const off = ref(false)
const chapterRows = ref([])
const characterRows = ref([])
const allThreads = ref([])
const allViolations = ref([])
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
const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning' }
const LEVEL_TEXT = { main: '主线', sub: '支线', minor: '次线' }
const SEVERITY_TEXT = { high: '必须处理', medium: '建议处理', low: '了解一下', info: '了解一下' }
const SEVERITY_TAG = { high: 'danger', medium: 'warning', low: 'info', info: 'info' }

const statusText = (s) => STATUS_TEXT[s] || s || '—'
const statusTag = (s) => (s === 'overdue' ? 'danger' : s === 'due_soon' ? 'warning' : s === 'stalled' ? 'info' : '')
const ruleText = (r) => RULE_TEXT[r] || r || '—'
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''
const levelText = (l) => LEVEL_TEXT[l] || l || '主线'
const severityText = (s) => SEVERITY_TEXT[s] || s || '—'
const severityTag = (s) => SEVERITY_TAG[s] || 'info'
const threadRowClass = ({ row }) => (row.status === 'overdue' ? 'ft-row-overdue' : '')

// 筛选在本地做：切换不用再打接口，计数也稳定
const threadRows = computed(() => (threadStatus.value
  ? allThreads.value.filter((t) => t.status === threadStatus.value)
  : allThreads.value))
const violationRows = computed(() => (severity.value
  ? allViolations.value.filter((v) => v.severity === severity.value)
  : allViolations.value))

const stats = computed(() => [
  { label: '有记录的事件章', value: chapterRows.value.length },
  { label: '未回收伏笔', value: allThreads.value.length, tone: allThreads.value.some((t) => t.status === 'overdue') ? 'warn' : '' },
  { label: '人物', value: characterRows.value.length },
  { label: '冲突提醒', value: allViolations.value.length, tone: allViolations.value.some((v) => v.severity === 'high') ? 'danger' : '' },
])

// 404 = 开关未开（后端按契约统一返回 404，不返回半真数据），不当作错误弹窗
const isOff = (e) => e?.response?.status === 404

async function loadTimeline() {
  const chapter = await getFactsTimeline(props.pid, { view: 'chapter' })
  chapterRows.value = (chapter?.items || []).filter((r) => r.summary)
  const chars = await getFactsTimeline(props.pid, { view: 'character' })
  characterRows.value = chars?.items || []
}

async function loadThreads() {
  const data = await getFactsThreads(props.pid, { limit: 200 })
  allThreads.value = data?.threads || []
}

async function loadViolations() {
  const data = await getFactsViolations(props.pid, { limit: 100 })
  allViolations.value = data?.violations || []
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
.ft-tip { font-size: 13px; color: var(--muted); }
.ft-filter { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.ft-count-note { font-size: 12px; color: var(--muted); }
.ft-alert { margin-bottom: 10px; }
.ft-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.ft-tab-count {
  display: inline-block; margin-left: 4px; font-size: 11px; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.ft-table { width: 100%; }
.ft-table :deep(.el-table__row.ft-row-overdue) { background: #fdf3e3; }
.ft-empty-hint { max-width: 560px; margin: 6px auto 0; font-size: 12px; color: var(--muted); line-height: 1.8; text-align: left; }
.ft-ch { font-size: 12px; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ft-ch-sm { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ft-overdue { color: var(--danger); font-weight: 600; }
.ft-arrow { margin: 0 6px; color: var(--muted); }
.ft-main { font-size: 13px; color: var(--ink); }
.ft-sub { margin-top: 2px; font-size: 12px; color: var(--muted); line-height: 1.6; }
.ft-avatar {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; margin-right: 6px; border-radius: 50%;
  background: var(--indigo-soft); color: var(--indigo); font-size: 12px;
}
.ft-name { font-size: 13px; }
.ft-expect { color: var(--ink-soft); font-size: 12px; line-height: 1.6; }
.ft-actual { color: var(--gold-deep); font-size: 12px; line-height: 1.6; }
</style>
