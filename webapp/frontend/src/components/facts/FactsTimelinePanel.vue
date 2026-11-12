<template>
  <div class="ft">
    <FactsPanelHead
      title="时间线"
      sub="只读视图：事件按章追加、冲突由确定性规则算出。人物状态与伏笔统一在「状态图谱」查看，本页不重复。修正动作回到「章节」或「世界观」。"
      :stats="stats"
      :loading="loading"
      @refresh="reload"
    />

    <!-- 开关未开：说清能力状态，不伪造数据 -->
    <el-empty v-if="off" description="时间线未启用（后台开关 timeline_view 关闭）">
      <p class="ft-tip">M1 的四个查询接口在开关关闭时一律不返回数据。开启后回到这里刷新即可。</p>
    </el-empty>

    <template v-else>
      <p class="ft-note">人物状态见「状态图谱 → 角色认知」，伏笔见「状态图谱 → 可能分支」；本页只保留时间线与冲突，不重复列同一份账本。</p>
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
import { getFactsTimeline, getFactsViolations } from '../../api/facts'
import FactsPanelHead from './FactsPanelHead.vue'

const props = defineProps({
  pid: { type: String, required: true },
})

const tab = ref('chapter')
const loading = ref(false)
const off = ref(false)
const chapterRows = ref([])
const allViolations = ref([])
const severity = ref('')

const RULE_TEXT = {
  mechanism_delta: '机制数字方向',
  death_state: '死亡后仍行动',
  item_owner: '物品归属',
  address_drift: '称谓漂移',
  time_reverse: '时间倒退',
}
const KIND_TEXT = { state: '状态', object: '物品', relationship: '关系', foreshadow: '伏笔' }
const KIND_TAG = { state: '', object: 'success', relationship: 'info', foreshadow: 'warning' }
const SEVERITY_TEXT = { high: '必须处理', medium: '建议处理', low: '了解一下', info: '了解一下' }
const SEVERITY_TAG = { high: 'danger', medium: 'warning', low: 'info', info: 'info' }

const ruleText = (r) => RULE_TEXT[r] || r || '—'
const kindText = (k) => KIND_TEXT[k] || '状态'
const kindTag = (k) => KIND_TAG[k] || ''
const severityText = (s) => SEVERITY_TEXT[s] || s || '—'
const severityTag = (s) => SEVERITY_TAG[s] || 'info'

// 筛选在本地做：切换不用再打接口，计数也稳定
const violationRows = computed(() => (severity.value
  ? allViolations.value.filter((v) => v.severity === severity.value)
  : allViolations.value))

const stats = computed(() => [
  { label: '有记录的事件章', value: chapterRows.value.length },
  { label: '冲突提醒', value: allViolations.value.length, tone: allViolations.value.some((v) => v.severity === 'high') ? 'danger' : '' },
])

// 404 = 开关未开（后端按契约统一返回 404，不返回半真数据），不当作错误弹窗
const isOff = (e) => e?.response?.status === 404

async function loadTimeline() {
  const chapter = await getFactsTimeline(props.pid, { view: 'chapter' })
  chapterRows.value = (chapter?.items || []).filter((r) => r.summary)
}

async function loadViolations() {
  const data = await getFactsViolations(props.pid, { limit: 100 })
  allViolations.value = data?.violations || []
}

async function reload() {
  loading.value = true
  off.value = false
  try {
    await Promise.all([loadTimeline(), loadViolations()])
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
.ft-note { font-size: 12px; color: var(--muted); line-height: 1.7; margin: 0; }
.ft-filter { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.ft-count-note { font-size: 12px; color: var(--muted); }
.ft-alert { margin-bottom: 10px; }
.ft-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
.ft-tab-count {
  display: inline-block; margin-left: 4px; font-size: 11px; color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.ft-table { width: 100%; }
.ft-empty-hint { max-width: 560px; margin: 6px auto 0; font-size: 12px; color: var(--muted); line-height: 1.8; text-align: left; }
.ft-ch { font-size: 12px; color: var(--ink-soft); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ft-ch-sm { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.ft-expect { color: var(--ink-soft); font-size: 12px; line-height: 1.6; }
.ft-actual { color: var(--gold-deep); font-size: 12px; line-height: 1.6; }
</style>
