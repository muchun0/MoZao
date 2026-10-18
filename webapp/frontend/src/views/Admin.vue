<template>
  <div v-loading="loading">
    <h2>管理后台</h2>

    <!-- 经营概览 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="s in stats" :key="s.label">
        <el-card class="stat-card">
          <div class="stat-val">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="tab">
      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <el-table :data="users" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户名" />
          <el-table-column prop="email" label="邮箱" />
          <el-table-column prop="balance" label="余额" width="80" />
          <el-table-column prop="is_admin" label="站长" width="70">
            <template #default="{ row }">{{ row.is_admin ? '✓' : '' }}</template>
          </el-table-column>
          <el-table-column prop="is_agent" label="代理" width="70">
            <template #default="{ row }">{{ row.is_agent ? '✓' : '' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="180">
            <template #default="{ row }">
              <el-button size="small" @click="showRecharge(row)">充值</el-button>
              <el-button size="small" @click="showAgentOpen(row)">开代理</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 退款管理 -->
      <el-tab-pane label="退款管理" name="refunds">
        <el-radio-group v-model="refundFilter" size="small" @change="loadRefunds">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="pending">待处理</el-radio-button>
          <el-radio-button value="approved">已退款</el-radio-button>
          <el-radio-button value="rejected">已拒绝</el-radio-button>
        </el-radio-group>
        <el-table :data="refunds" stripe size="small" style="margin-top:10px">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="username" label="用户" />
          <el-table-column prop="amount" label="金额" width="80" />
          <el-table-column prop="reason" label="原因" />
          <el-table-column prop="status" label="状态" width="80" />
          <el-table-column prop="created_at" label="时间" width="160" />
          <el-table-column label="操作" width="200">
            <template #default="{ row }">
              <template v-if="row.status === 'pending'">
                <el-button size="small" type="danger" @click="doRefund(row, false)">拒绝</el-button>
                <el-button size="small" type="primary" @click="doRefund(row, true)">退款</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 余额流水 -->
      <el-tab-pane label="余额流水" name="transactions">
        <el-table :data="transactions" stripe size="small">
          <el-table-column prop="username" label="用户" />
          <el-table-column prop="amount" label="变动" width="80" />
          <el-table-column prop="kind" label="类型" width="100" />
          <el-table-column prop="note" label="备注" />
          <el-table-column prop="created_at" label="时间" width="160" />
        </el-table>
      </el-tab-pane>

      <!-- 平台技能管理 -->
      <el-tab-pane label="平台技能" name="skills">
        <el-button type="primary" size="small" @click="skillDialog = true">+ 新建平台技能</el-button>
        <el-table :data="skills" stripe size="small" style="margin-top:10px">
          <el-table-column prop="name" label="名称" />
          <el-table-column prop="role" label="角色" width="90" />
          <el-table-column label="适用阶段" width="140">
            <template #default="{ row }">{{ stageLabel(row) }}</template>
          </el-table-column>
          <el-table-column prop="description" label="描述" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button size="small" link type="danger" @click="delSkill(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 质量报表 -->
      <el-tab-pane label="质量报表" name="quality">
        <el-form inline size="small" style="margin-bottom:10px">
          <el-form-item label="项目ID"><el-input v-model="qualityFilter.project_id" placeholder="留空=全部" style="width:200px" /></el-form-item>
          <el-form-item label="近N天"><el-input-number v-model="qualityFilter.days" :min="0" :max="365" /></el-form-item>
          <el-form-item><el-button type="primary" @click="loadQualityReport">查询</el-button></el-form-item>
        </el-form>
        <div v-if="qualityReport && qualityReport.report" class="stat-row">
          <el-row :gutter="16">
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.task_count ?? '-' }}</div><div class="stat-label">任务数</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.call_count ?? '-' }}</div><div class="stat-label">LLM 调用</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ (qualityReport.report.token_total ?? 0).toLocaleString() }}</div><div class="stat-label">总 Token</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.fail_count ?? 0 }}</div><div class="stat-label">失败次数</div></el-card></el-col>
          </el-row>
          <el-row :gutter="16" style="margin-top:10px">
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.quality_checks ?? 0 }}</div><div class="stat-label">质检次数</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.score_avg ?? '-' }}</div><div class="stat-label">平均质检分</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ Math.round((qualityReport.report.rewrite_rate ?? 0) * 100) }}%</div><div class="stat-label">重写率</div></el-card></el-col>
            <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ qualityReport.report.per_chapter_avg_tokens ?? 0 }}</div><div class="stat-label">单章平均 Token</div></el-card></el-col>
          </el-row>
        </div>
        <div v-if="qualityReport && qualityReport.report" style="margin-top:10px">
          <el-card>
            <template #header><b>质检得分分布</b></template>
            <el-progress v-for="(n, k) in (qualityReport.report.score_buckets||{})" :key="k" :percentage="pct(n)" :format="() => `${k}：${n} 章`" :stroke-width="14" style="margin-bottom:8px" />
          </el-card>
          <el-card style="margin-top:10px">
            <template #header>
              <b>每章状态（进度盘）</b>
              <span class="muted" style="font-size:12px;font-weight:normal;margin-left:8px">红=未过 / 绿=追更欲高分(hook≥4) / 黄=自动重写 / 灰=通过</span>
            </template>
            <div v-if="(qualityReport.report.chapter_status||[]).length" class="progress-dots">
              <el-tooltip v-for="c in qualityReport.report.chapter_status" :key="c.chapter" placement="top" effect="dark">
                <template #content>
                  第{{ c.chapter }}章 · 质检分 {{ c.score }}{{ c.passed ? ' · 通过' : ' · 未过' }}{{ c.auto_revised ? ' · 自动重写' : '' }}{{ c.hook_score != null ? ' · 追更欲 ' + c.hook_score : '' }}
                </template>
                <span class="dot" :class="'dot-' + (c.color || 'gray')">{{ c.chapter }}</span>
              </el-tooltip>
            </div>
            <div v-else class="muted">暂无每章质检数据（需质量校验留痕）</div>
          </el-card>
          <el-card style="margin-top:10px">
            <template #header><b>问题类型 TopN</b></template>
            <div v-if="(qualityReport.report.issue_top_types||[]).length">
              <el-tag v-for="it in qualityReport.report.issue_top_types" :key="it[0]" type="warning" style="margin:0 6px 6px 0">{{ it[0] }} × {{ it[1] }}</el-tag>
            </div>
            <div v-else class="muted">暂无质检问题数据</div>
          </el-card>
          <el-card style="margin-top:10px">
            <template #header><b>弃书风险分布（编辑审稿）</b></template>
            <el-tag v-for="(n, k) in (qualityReport.report.churn_risk_dist||{})" :key="k" :type="k === '高' ? 'danger' : (k === '中' ? 'warning' : 'success')" style="margin:0 6px 6px 0">{{ k }}：{{ n }}</el-tag>
          </el-card>
          <el-card style="margin-top:10px">
            <template #header><b>各阶段成本（Token）</b></template>
            <div v-for="(v, k) in (qualityReport.report.scenes||{})" :key="k" class="muted" style="margin:2px 0">
              {{ k }}：{{ v.tokens.toLocaleString() }} Token / {{ v.calls }} 次（均 {{ Math.round(v.avg_duration_ms) }}ms）
            </div>
          </el-card>
        </div>
        <div v-else class="muted" style="padding:16px 0">输入筛选条件后查询（无数据时显示空态，不报错）</div>
      </el-tab-pane>

      <!-- 系统日志 -->
      <el-tab-pane label="系统日志" name="logs">
        <el-button size="small" @click="loadLogs">刷新</el-button>
        <pre class="log-view">{{ logs }}</pre>
      </el-tab-pane>

      <!-- 系统设置（生成引擎开关） -->
      <el-tab-pane label="系统设置" name="config">
        <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
          title="生成引擎开关全局生效：以表中数据为准，保存后对所有项目生成实时生效（无需发版）；项目 config.json 快照不会被改写。" />
        <el-tabs v-model="configModule">
          <el-tab-pane v-for="g in configGroups" :key="g.module" :label="moduleLabel(g.module)" :name="g.module">
            <el-table :data="g.items" stripe size="small">
              <el-table-column prop="label" label="开关" width="200" show-overflow-tooltip />
              <el-table-column prop="description" label="说明" min-width="260" show-overflow-tooltip />
              <el-table-column label="当前值" width="220">
                <template #default="{ row }">
                  <el-switch v-if="row.config_type === 'bool'" v-model="row._val" @change="markDirty(row)" />
                  <el-input-number v-else-if="row.config_type === 'int'" v-model="row._val" :step="1" :min="0" controls-position="right" style="width:150px" @change="markDirty(row)" />
                  <el-input-number v-else-if="row.config_type === 'float'" v-model="row._val" :step="0.1" :min="0" controls-position="right" style="width:150px" @change="markDirty(row)" />
                  <el-select v-else-if="row.config_type === 'select'" v-model="row._val" style="width:150px" @change="markDirty(row)">
                    <el-option v-for="o in row.options || []" :key="o" :label="o" :value="o" />
                  </el-select>
                  <el-input v-else v-model="row._val" size="small" style="width:150px" @change="markDirty(row)" />
                </template>
              </el-table-column>
              <el-table-column label="默认" width="90">
                <template #default="{ row }"><span class="muted">{{ fmtDefault(row) }}</span></template>
              </el-table-column>
              <el-table-column label="最近修改" width="120">
                <template #default="{ row }">
                  <span class="muted" v-if="row.updated_by">{{ row.updated_by }}<br>{{ row.updated_at }}</span>
                  <span class="muted" v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="80">
                <template #default="{ row }">
                  <el-tag v-if="row._dirty" type="warning" size="small">未保存</el-tag>
                  <el-tag v-else type="success" size="small">已同步</el-tag>
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top:12px">
              <el-button type="primary" :disabled="!dirtyCount(g.module)" @click="saveGroup(g.module)">保存本组（{{ dirtyCount(g.module) }} 项）</el-button>
              <el-button :disabled="!dirtyCount(g.module)" @click="loadConfig">放弃修改</el-button>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-tab-pane>
    </el-tabs>

    <!-- 充值弹窗 -->
    <el-dialog v-model="rechargeDialog" title="手动充值" width="400px">
      <p>用户：{{ rechargeUser?.username }}</p>
      <el-input-number v-model="rechargeAmount" :min="1" placeholder="充值金额" />
      <template #footer>
        <el-button @click="rechargeDialog = false">取消</el-button>
        <el-button type="primary" @click="doRecharge">确认到账</el-button>
      </template>
    </el-dialog>

    <!-- 开代理弹窗 -->
    <el-dialog v-model="agentDialog" title="开通代理" width="400px">
      <p>用户：{{ agentUser?.username }}</p>
      <p class="muted">开通后该用户可获得下级消费返佣</p>
      <template #footer>
        <el-button @click="agentDialog = false">取消</el-button>
        <el-button type="primary" @click="doOpenAgent">确认开通</el-button>
      </template>
    </el-dialog>

    <!-- 平台技能编辑弹窗 -->
    <el-dialog v-model="skillDialog" title="新建平台技能" width="620px">
      <el-form :model="skillForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="skillForm.name" /></el-form-item>
        <el-form-item label="角色">
          <el-select v-model="skillForm.role" style="width:100%">
            <el-option label="全部角色" value="" />
            <el-option label="调研" value="researcher" />
            <el-option label="架构师" value="architect" />
            <el-option label="写手" value="writer" />
            <el-option label="审校" value="reviewer" />
            <el-option label="读者" value="reader" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用阶段">
          <el-select v-model="skillForm.stage_list" multiple clearable placeholder="不选=全部阶段" style="width:100%">
            <el-option label="调研(research)" value="research" />
            <el-option label="大纲(outline)" value="outline" />
            <el-option label="世界观(world)" value="world" />
            <el-option label="正文(chapter)" value="chapter" />
            <el-option label="审校(review)" value="review" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="skillForm.description" /></el-form-item>
        <el-form-item label="提示词"><el-input v-model="skillForm.prompt_append" type="textarea" :rows="8" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="skillDialog = false">取消</el-button>
        <el-button type="primary" @click="saveSkill">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import * as adminApi from '../api/admin'
import { ElMessage, ElMessageBox } from 'element-plus'

const loading = ref(false)
const tab = ref('users')
const overview = ref({})
const users = ref([])
const refunds = ref([])
const refundFilter = ref('')
const transactions = ref([])
const skills = ref([])
const logs = ref('')
const qualityReport = ref(null)
const qualityFilter = ref({ project_id: '', days: 7 })

const rechargeDialog = ref(false)
const rechargeUser = ref(null)
const rechargeAmount = ref(10)

const agentDialog = ref(false)
const agentUser = ref(null)

const skillDialog = ref(false)
const skillForm = ref({ name: '', role: '', description: '', prompt_append: '', stage_list: [] })

// 系统设置（生成引擎开关）
const configGroups = ref([])
const configModule = ref('memory')
const MODULE_LABELS = { memory: '记忆与一致性', writing: '正文生成', review: '审校质检', guard: '成本防护' }
const moduleLabel = m => MODULE_LABELS[m] || m

function fmtDefault(row) {
  const d = row.default
  if (typeof d === 'boolean') return d ? '开' : '关'
  if (d !== null && typeof d === 'object') return JSON.stringify(d)
  return d ?? '-'
}

function normVal(row) {
  if (row.config_type === 'bool') return row.value === 'true'
  if (row.config_type === 'int' || row.config_type === 'float') return Number(row.value)
  if (row.config_type === 'json') { try { return JSON.parse(row.value) } catch { return {} } }
  return row.value
}

async function loadConfig() {
  try {
    const r = await adminApi.getConfig()
    configGroups.value = (r.groups || []).map(g => ({
      ...g,
      items: (g.items || []).map(it => ({ ...it, _val: normVal(it), _dirty: false })),
    }))
  } catch (e) { ElMessage.error('开关加载失败：' + (e?.response?.data?.detail || e.message)) }
}

function markDirty(row) { row._dirty = true }
function dirtyCount(mod) {
  return (configGroups.value.find(g => g.module === mod)?.items || []).filter(i => i._dirty).length
}

function serVal(row) {
  if (row.config_type === 'bool') return row._val ? 'true' : 'false'
  if (row.config_type === 'int' || row.config_type === 'float') return String(row._val)
  if (row.config_type === 'json') return JSON.stringify(row._val)
  return String(row._val ?? '')
}

async function saveGroup(mod) {
  const g = configGroups.value.find(x => x.module === mod)
  if (!g) return
  const items = g.items.filter(i => i._dirty).map(i => ({ key: i.key, value: serVal(i) }))
  if (!items.length) return
  try {
    const r = await adminApi.updateConfig(items)
    ElMessage.success(`已保存 ${r.updated || items.length} 项开关，全局生效`)
    await loadConfig()
  } catch (e) { ElMessage.error('保存失败：' + (e?.response?.data?.detail || e.message)) }
}

const STAGE_LABELS = { research: '调研', outline: '大纲', world: '世界观', chapter: '正文', review: '审校' }
const stageLabel = s => {
  const list = (s.applicable_stages || '').split(/[,，]/).map(x => x.trim()).filter(Boolean)
  return list.length ? list.map(x => STAGE_LABELS[x] || x).join('、') : '全部阶段'
}

const stats = computed(() => [
  { label: '总用户', value: overview.value.user_count ?? '-' },
  { label: '总项目', value: overview.value.project_count ?? '-' },
  { label: '总收入', value: '¥' + (overview.value.total_revenue ?? 0).toFixed(2) },
  { label: '今日生成', value: overview.value.today_words ?? '-' },
])

async function loadAll() {
  loading.value = true
  try {
    const [ov, us] = await Promise.all([adminApi.getOverview(), adminApi.listUsers()])
    overview.value = ov
    users.value = us.users || us || []
  } catch {} finally { loading.value = false }
}

async function loadRefunds() {
  const r = await adminApi.listRefunds({ status: refundFilter.value })
  refunds.value = r.refunds || []
}

async function loadLogs() {
  const r = await adminApi.getLogs({ lines: 200 })
  logs.value = r.logs || ''
}

async function loadQualityReport() {
  qualityReport.value = null
  try {
    const r = await adminApi.getQualityReport({ project_id: qualityFilter.value.project_id, days: qualityFilter.value.days })
    qualityReport.value = r
  } catch (e) { ElMessage.error('报表加载失败：' + (e?.response?.data?.detail || e.message)) }
}

function pct(n) {
  const total = Object.values(qualityReport.value?.report?.score_buckets || {}).reduce((a, b) => a + b, 0)
  return total ? Math.round((n / total) * 100) : 0
}

async function loadSkills() {
  const r = await adminApi.listSkills()
  skills.value = r.skills || []
}

function showRecharge(row) { rechargeUser.value = row; rechargeDialog.value = true }
async function doRecharge() {
  await adminApi.manualRecharge({ user_id: rechargeUser.value.id, amount: rechargeAmount.value })
  ElMessage.success('充值成功')
  rechargeDialog.value = false
  loadAll()
}

function showAgentOpen(row) { agentUser.value = row; agentDialog.value = true }
async function doOpenAgent() {
  await adminApi.openAgent({ user_id: agentUser.value.id })
  ElMessage.success('已开通代理')
  agentDialog.value = false
  loadAll()
}

async function doRefund(row, approve) {
  await ElMessageBox.confirm(approve ? '确认退款？' : '拒绝申请？', '确认')
  try {
    if (approve) await adminApi.approveRefund(row.id, {})
    else await adminApi.rejectRefund(row.id, {})
    ElMessage.success(approve ? '已退款' : '已拒绝')
    loadRefunds()
  } catch (e) { ElMessage.error('操作失败') }
}

async function saveSkill() {
  const payload = { ...skillForm.value }
  payload.applicable_stages = (payload.stage_list || []).join(',')
  delete payload.stage_list
  await adminApi.saveSkill(payload)
  ElMessage.success('已保存')
  skillDialog.value = false
  skillForm.value = { name: '', role: '', description: '', prompt_append: '', stage_list: [] }
  loadSkills()
}

async function delSkill(row) {
  await ElMessageBox.confirm(`删除风格「${row.name}」？`, '确认', { type: 'warning' })
  await adminApi.deleteSkill(row.id)
  ElMessage.success('已删除')
  loadSkills()
}

onMounted(() => {
  loadAll()
  loadRefunds()
  adminApi.listTransactions().then(r => { transactions.value = r.transactions || [] }).catch(() => {})
  loadSkills()
  loadLogs()
  loadConfig()
})
</script>

<style scoped>
.stat-row { margin-bottom: 20px; }
.stat-card { text-align: center; }
.stat-val { font-size: 28px; font-weight: 700; color: #303133; }
.stat-label { font-size: 13px; color: #909399; margin-top: 4px; }
.log-view { background: #1e1e1e; color: #d4d4d4; padding: 12px; border-radius: 6px; max-height: 500px; overflow-y: auto; font-size: 12px; }
.muted { color: #909399; font-size: 13px; }
.progress-dots { display: flex; flex-wrap: wrap; gap: 4px; }
.dot { display: inline-flex; align-items: center; justify-content: center; min-width: 26px; height: 26px; padding: 0 4px; border-radius: 4px; font-size: 11px; color: #fff; cursor: default; }
.dot-red { background: #f56c6c; }
.dot-green { background: #67c23a; }
.dot-yellow { background: #e6a23c; }
.dot-gray { background: #909399; }
</style>
