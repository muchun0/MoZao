<template>
  <div class="plan-panel">
    <!-- 门 1：分章约束卡 —— 把「这章必须发生什么」变成可编辑卡片，文本大纲降为备注；
         门 1 的 diff 只是把「计划 vs 已写」摆出来，不阻断、不自动改稿。 -->
    <div class="panel-toolbar">
      <div class="panel-toolbar-left">
        <el-tag v-if="version" size="small" effect="plain">章节规划</el-tag>
        <el-tag v-if="!ready" size="small" type="info" effect="plain">尚未生成</el-tag>
        <span class="plan-hint">每章最多 5 条「必须发生」；改完立即生效，下一次生成本章时生效</span>
      </div>
      <div class="panel-toolbar-right">
        <el-button size="small" :loading="loading" @click="reload">刷新</el-button>
        <el-button size="small" :loading="compiling" @click="compile">按大纲重新生成</el-button>
        <el-button size="small" :loading="validating" @click="runValidate">检查结构</el-button>
      </div>
    </div>

    <el-empty v-if="!ready && !loading" description="还没有分章计划。按大纲生成一份后，可逐章编辑「必须发生 / 禁止发生」">
      <el-button type="primary" :loading="compiling" @click="compile">生成分章计划</el-button>
    </el-empty>

    <template v-else>
      <div class="plan-ch-bar">
        <span class="plan-ch-label">章节</span>
        <el-select v-model="ch" size="small" class="plan-ch-select" @change="onChChange">
          <el-option v-for="n in chapterOptions" :key="n" :label="`第${n}章`" :value="n" />
        </el-select>
        <el-button size="small" type="primary" plain @click="openCreate">+ 新增约束</el-button>
        <el-button size="small" :loading="diffing" @click="runDiff">对比本章已写内容</el-button>
      </div>

      <div v-if="validateResult" class="plan-validate" :class="{ bad: !validateResult.ok }">
        <el-tag size="small" :type="validateResult.ok ? 'success' : 'warning'" effect="plain">
          {{ validateResult.ok ? '结构校验通过' : `结构问题 ${validateResult.issues.length} 条` }}
        </el-tag>
        <span v-for="(it, i) in validateResult.issues.slice(0, 6)" :key="i" class="plan-issue">
          {{ it.code }}：{{ it.detail || it.id || '' }}
        </span>
      </div>

      <div v-if="!cards.length" class="plan-empty">
        <el-empty :description="`第${ch}章还没有约束卡。可以从大纲重编译，或手动新增`" />
      </div>

      <div v-else class="plan-cards">
        <div v-for="c in cards" :key="c.id" class="plan-card" :class="`st-${c.strength}`">
          <div class="plan-card-head">
            <el-tag size="small" effect="plain">{{ c.kind_label }}</el-tag>
            <el-tag size="small" :type="strengthType(c.strength)" effect="dark">{{ strengthLabel(c.strength) }}</el-tag>
            <el-tag v-if="c.source === 'manual'" size="small" type="warning" effect="plain">手改</el-tag>
            <span class="plan-card-id">{{ c.id }}</span>
            <span class="plan-card-actions">
              <el-button size="small" link type="primary" @click="openEdit(c)">编辑</el-button>
              <el-button size="small" link type="danger" @click="removeCard(c)">删除</el-button>
            </span>
          </div>
          <div class="plan-card-text">{{ c.text }}</div>
          <div class="plan-card-meta">
            <span v-if="c.subject">主体：{{ c.subject }}</span>
            <span v-if="c.object">对象：{{ c.object }}</span>
            <span v-if="c.evidence && c.evidence !== 'manual'">依据：{{ c.evidence }}</span>
          </div>
        </div>
      </div>

      <!-- diff：三档判定（missing 扣分不阻断 / violation 阻断 / unplanned 仅提示） -->
      <div v-if="diff" class="plan-diff">
        <div class="plan-diff-head">
          <strong>第{{ diff.ch }}章对比</strong>
          <template v-if="diff.has_observation">
            <el-tag size="small" type="success" effect="plain">满足 {{ diff.counts.satisfied }}</el-tag>
            <el-tag size="small" type="warning" effect="plain">未做到 {{ diff.counts.missing }}（{{ diff.score }} 分）</el-tag>
            <el-tag size="small" :type="diff.blocking ? 'danger' : 'info'" effect="plain">
              禁止项冲突 {{ diff.counts.violation }}
            </el-tag>
            <el-tag size="small" type="info" effect="plain">计划外 {{ diff.counts.unplanned }}</el-tag>
            <el-tag v-if="diff.counts.unverifiable" size="small" type="info" effect="plain">
              无法核实 {{ diff.counts.unverifiable }}
            </el-tag>
          </template>
          <el-tag v-else size="small" type="info" effect="plain">本章还没有人物状态记录，无法对比</el-tag>
        </div>
        <ul v-if="diff.violation.length" class="plan-diff-list danger">
          <li v-for="(v, i) in diff.violation" :key="`v${i}`">禁止项冲突：{{ v.detail }}</li>
        </ul>
        <ul v-if="diff.missing.length" class="plan-diff-list warn">
          <li v-for="(m, i) in diff.missing" :key="`m${i}`">
            {{ m.kind_label }}未做到：{{ m.label }}（{{ m.strength === 'must' ? '扣 5 分' : '仅提示' }}）
          </li>
        </ul>
        <ul v-if="diff.unplanned.length" class="plan-diff-list info">
          <li v-for="(u, i) in diff.unplanned" :key="`u${i}`">计划外：{{ u.text }}</li>
        </ul>
        <ul v-if="diff.unverifiable.length" class="plan-diff-list info">
          <li v-for="(u, i) in diff.unverifiable" :key="`uv${i}`">{{ u.label }}：{{ u.reason }}</li>
        </ul>
      </div>
    </template>

    <!-- 编辑/新增弹窗：只暴露人能理解的字段，id 由服务端分配 -->
    <el-dialog v-model="dialog" :title="form.id ? '编辑约束卡' : `新增第${form.ch}章约束`" width="520px">
      <el-form label-width="84px" size="small">
        <el-form-item label="类型">
          <el-select v-model="form.kind" class="w-full">
            <el-option v-for="k in kindOptions" :key="k.value" :label="k.label" :value="k.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="强度">
          <el-radio-group v-model="form.strength">
            <el-radio-button value="must">必须（违反扣 5 分）</el-radio-button>
            <el-radio-button value="should">应当</el-radio-button>
            <el-radio-button value="hint">提示</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.kind === 'must_hold'" label="物品">
          <el-input v-model="form.src_name" placeholder="例：铜匣" />
        </el-form-item>
        <el-form-item :label="dstLabel">
          <el-input v-model="form.dst_name" :placeholder="dstPlaceholder" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.text" type="textarea" :rows="2"
                    placeholder="留空则按类型自动生成一句" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="dialog = false">取消</el-button>
        <el-button size="small" type="primary" :loading="saving" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
// 门 1 面板：Plan 计划层（可改）的编辑入口，与 Canon 账本（只增不改）严格分开。
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  compilePlanGraph, deletePlanConstraint, getPlanGraph, savePlanConstraint,
} from '../../api/novel'

const props = defineProps({
  pid: { type: String, required: true },
  // 已生成章数：章选择范围取 max(plan.ch_range[1], 已生成章数)
  chapterCount: { type: Number, default: 0 },
})

const loading = ref(false)
const compiling = ref(false)
const validating = ref(false)
const diffing = ref(false)
const saving = ref(false)
const ready = ref(false)
const version = ref(0)
const chRange = ref([1, 30])
const ch = ref(1)
const cards = ref([])
const diff = ref(null)
const validateResult = ref(null)
const dialog = ref(false)
const form = reactive({
  id: '', ch: 1, kind: 'must_present', strength: 'must',
  src_name: '', dst_name: '', text: '',
})

const KIND_OPTIONS = [
  { value: 'must_present', label: '必须出场（人物）' },
  { value: 'must_hold', label: '必须持有/转移（物品→人）' },
  { value: 'must_locate', label: '必须身处（地点）' },
  { value: 'forbidden_present', label: '禁止出现（人物）' },
  { value: 'must_resolve', label: '必须回收（伏笔）' },
  { value: 'must_advance', label: '必须推进（仅备注）' },
]
const kindOptions = KIND_OPTIONS
const kindLabel = (v) => (KIND_OPTIONS.find((k) => k.value === v) || {}).label || v

const dstLabel = computed(() => {
  if (form.kind === 'must_hold') return '持有者'
  if (form.kind === 'must_resolve') return '伏笔名'
  if (form.kind === 'must_locate') return '地点'
  return '人物'
})
const dstPlaceholder = computed(() => {
  if (form.kind === 'must_hold') return '例：陆沉'
  if (form.kind === 'must_resolve') return '例：铜匣的来历（与伏笔账本同名）'
  if (form.kind === 'must_locate') return '例：墟口'
  return '例：陆沉'
})

const chapterOptions = computed(() => {
  const end = Math.max(Number(chRange.value?.[1] || 30), props.chapterCount || 0, ch.value)
  return Array.from({ length: Math.max(end, 1) }, (_, i) => i + 1)
})

function strengthLabel(s) {
  return { must: '必须', should: '应当', hint: '提示' }[s] || s
}
function strengthType(s) {
  return { must: 'danger', should: 'warning', hint: 'info' }[s] || 'info'
}

async function reload() {
  loading.value = true
  try {
    const res = await getPlanGraph(props.pid, { view: 'cards', ch: ch.value })
    version.value = res.plan_version || 0
    chRange.value = res.ch_range || [1, 30]
    cards.value = res.cards || []
    ready.value = true
    if (!cards.value.length && ch.value > (chRange.value?.[1] || 0)) {
      // 超出范围不强改用户输入，只提示
      ElMessage.warning(`第${ch.value}章超出本书章范围`)
    }
  } catch (e) {
    if (e?.response?.status === 404) {
      // 404 = 未编译 或 开关未开：两者都用「未编译」态承接，点编译按钮会得到具体原因
      ready.value = false
      cards.value = []
      return
    }
    ElMessage.error(e?.response?.data?.detail || '读取分章计划失败')
  } finally {
    loading.value = false
  }
}

function onChChange() {
  diff.value = null
  reload()
}

async function compile() {
  compiling.value = true
  try {
    const res = await compilePlanGraph(props.pid, { keep_manual: true })
    version.value = res.plan_version || 0
    chRange.value = res.ch_range || [1, 30]
    ElMessage.success(`已编译：${res.counts?.constraints ?? 0} 条约束（保留手改卡）`)
    await reload()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '编译失败')
  } finally {
    compiling.value = false
  }
}

async function runValidate() {
  validating.value = true
  try {
    validateResult.value = await getPlanGraph(props.pid, { view: 'validate' })
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '校验失败')
  } finally {
    validating.value = false
  }
}

async function runDiff() {
  diffing.value = true
  try {
    diff.value = await getPlanGraph(props.pid, { view: 'diff', ch: ch.value })
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '对比失败')
  } finally {
    diffing.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    id: '', ch: ch.value, kind: 'must_present', strength: 'must',
    src_name: '', dst_name: '', text: '',
  })
  dialog.value = true
}

function openEdit(c) {
  Object.assign(form, {
    id: c.id, ch: c.ch, kind: c.kind, strength: c.strength,
    src_name: c.kind === 'must_hold' ? c.subject : '',
    dst_name: c.object, text: c.text,
  })
  dialog.value = true
}

async function submit() {
  saving.value = true
  try {
    const payload = {
      id: form.id || undefined,
      ch: form.ch,
      kind: form.kind,
      strength: form.strength,
      dst_name: form.dst_name || '',
      src_name: form.kind === 'must_hold' ? (form.src_name || '') : '',
      text: form.text || '',
    }
    const res = await savePlanConstraint(props.pid, payload)
    version.value = res.plan_version || version.value
    dialog.value = false
    ElMessage.success('已保存，下次生成本章时生效')
    await reload()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeCard(c) {
  try {
    await ElMessageBox.confirm(
      `删除这条约束：${c.text}？删除后本章不再要求它。`,
      '删除约束卡', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  try {
    const res = await deletePlanConstraint(props.pid, c.id)
    version.value = res.plan_version || version.value
    ElMessage.success('已删除')
    await reload()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(reload)
</script>

<style scoped>
.plan-panel { display: flex; flex-direction: column; gap: 12px; }
.plan-hint { font-size: 12px; color: var(--muted); }
.plan-ch-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.plan-ch-label { font-size: 13px; color: var(--ink-soft); }
.plan-ch-select { width: 120px; }
.plan-validate { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.plan-validate.bad { color: var(--ink-soft); }
.plan-issue { background: var(--indigo-soft); border-radius: 4px; padding: 1px 6px; }
.plan-empty { padding: 8px 0; }
.plan-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 10px; }
.plan-card {
  border: 1px solid var(--line, #e5e7eb); border-left-width: 3px; border-radius: 8px;
  padding: 10px 12px; background: #fff; display: flex; flex-direction: column; gap: 6px;
}
.plan-card.st-must { border-left-color: #b45309; }
.plan-card.st-should { border-left-color: #2563eb; }
.plan-card.st-hint { border-left-color: #9ca3af; }
.plan-card-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.plan-card-id { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.plan-card-actions { margin-left: auto; display: inline-flex; gap: 2px; }
.plan-card-text { font-size: 13px; line-height: 1.5; color: var(--ink); }
.plan-card-meta { display: flex; gap: 10px; flex-wrap: wrap; font-size: 11px; color: var(--muted); }
.plan-diff { border: 1px solid var(--line, #e5e7eb); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
.plan-diff-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 13px; }
.plan-diff-list { margin: 0; padding-left: 18px; font-size: 12px; line-height: 1.6; }
.plan-diff-list.danger { color: #b91c1c; }
.plan-diff-list.warn { color: #b45309; }
.plan-diff-list.info { color: var(--muted); }
.w-full { width: 100%; }
</style>
