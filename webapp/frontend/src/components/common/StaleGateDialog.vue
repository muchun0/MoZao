<template>
  <!-- 档3 生成前一致性门禁：409 stale_gate 的二次确认（可确认继续，不是硬拒绝） -->
  <el-dialog :model-value="visible" title="生成前一致性检查" width="560px"
             :close-on-click-modal="false" @update:model-value="(v) => { if (!v) close() }">
    <!-- 复用 R13 过期提示视觉（StaleNotice），不新造一套视觉语言 -->
    <StaleNotice :notice="notice" />
    <ul v-if="causeLines.length" class="sg-causes">
      <li v-for="(line, i) in causeLines" :key="i">{{ line }}</li>
    </ul>
    <!-- 批量重写默认不勾选：避免用户顺手确认造成非预期批量扣费 -->
    <el-checkbox v-model="rewriteAck" class="sg-ack">{{ ackText }}</el-checkbox>
    <p v-if="estimateError" class="sg-err">{{ estimateError }}</p>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button :disabled="starting" @click="continueRun">仍然继续</el-button>
      <el-button type="warning" :disabled="!rewriteAck || starting" :loading="starting" @click="rewriteFrom">
        从第 {{ detail.regenerate_from }} 章起重写
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import StaleNotice from './StaleNotice.vue'
import { startAgentRun, estimateAgentRun } from '../../api/project'

const emit = defineEmits(['started'])

const visible = ref(false)
const detail = ref({})
const rewriteAck = ref(false)
const starting = ref(false)
const estimate = ref(null)
const estimateError = ref('')
let pid = ''
let pending = null  // { payload, after }

// 连续章号压缩：与 ProjectDetail 同一展示口径
function fmtNos(list) {
  const out = []
  let start = null
  let prev = null
  for (const n of list || []) {
    if (start === null) { start = prev = n; continue }
    if (n === prev + 1) { prev = n; continue }
    out.push(start === prev ? `第${start}章` : `第${start}–${prev}章`)
    start = prev = n
  }
  if (start !== null) out.push(start === prev ? `第${start}章` : `第${start}–${prev}章`)
  return out.join('、')
}

const notice = computed(() => ({
  level: 'warning',
  title: '目标章之前已有过期章节',
  text: `${fmtNos(detail.value.stale_chapters) || '部分章节'}可能已过期：这些章基于旧版上游生成，直接续写会和最新设定对不上。`,
  advice: `可以从第 ${detail.value.regenerate_from} 章起向后重写，或已知不一致继续（新章会标注"基于旧版"）。`,
}))

// 两种成因分开说：上游大纲/世界观升版 + 前文被重写
const causeLines = computed(() => {
  const lines = []
  for (const c of detail.value.causes || []) {
    lines.push(`${c.label}已升级，${fmtNos(c.chapters)}基于旧版${c.label}生成`)
  }
  for (const it of detail.value.prev_rewrites || []) {
    const act = it.direction === 'reverted' ? '已切回旧版' : '已重写'
    lines.push(`第${it.ch}章${act}（v${it.based_on}→v${it.current}），影响${fmtNos(it.affected)}`)
  }
  return lines
})

const rewriteCount = computed(() => {
  const from = Number(detail.value.regenerate_from) || 0
  const total = Number(detail.value.total_chapters) || 0
  if (from > 0 && total >= from) return total - from + 1
  return (detail.value.stale_chapters || []).length
})

const ackText = computed(() => {
  const from = detail.value.regenerate_from
  const total = Number(detail.value.total_chapters) || 0
  const to = total ? `第 ${from}–${total} 章` : '其后全部章节'
  const words = Number(detail.value.est_words) || 0
  const price = estimate.value && estimate.value.estimated_price
  const money = price != null ? `，预估 ¥${Number(price).toFixed(2)}` : ''
  const w = words ? `约 ${words} 字` : ''
  return `我已知晓：将从${to}起重写，共 ${rewriteCount.value} 章${w ? `（${w}）` : ''}${money}，按实际生成字数结算`
})

async function open(pidValue, d, payload, after) {
  // 非门禁错误返回 false，让调用方走原有的错误提示
  if (!d || typeof d !== 'object' || d.code !== 'stale_gate') return false
  pid = pidValue
  detail.value = d
  pending = { payload: payload || {}, after }
  rewriteAck.value = false
  estimate.value = null
  estimateError.value = ''
  visible.value = true
  try {
    estimate.value = await estimateAgentRun(pid, {
      agent: 'writer.chapter',
      params: { scope: 'range', from_ch: d.regenerate_from },
    })
  } catch {
    // 预估失败不阻断决策：按钮文案退化为「按实际生成字数结算」
    estimateError.value = '金额预估失败，重写仍会按实际生成字数结算'
  }
  return true
}

function close() {
  visible.value = false
}

async function start(payload) {
  if (!pending) return
  starting.value = true
  try {
    const body = await startAgentRun(pid, payload)
    const after = pending.after
    close()
    if (typeof after === 'function') after(body)
    emit('started', body)
  } catch (e) {
    const d = e?.response?.data?.detail
    if (d && typeof d === 'object' && d.code === 'stale_gate') {
      // 「从第X章起重写」仍被拦：刷新提示，让用户重新决策
      detail.value = d
      return
    }
    ElMessage.error(typeof d === 'string' ? d : (d?.message || '启动失败'))
  } finally {
    starting.value = false
  }
}

function continueRun() {
  // 已知不一致继续：放行并由后端给新章打 unstable 标注
  start({ ...(pending?.payload || {}), allow_inconsistent: true })
}

function rewriteFrom() {
  const base = { ...(pending?.payload || {}) }
  delete base.allow_inconsistent
  start({
    ...base,
    agent: 'writer.chapter',
    params: { scope: 'range', from_ch: detail.value.regenerate_from },
  })
}

defineExpose({ open })
</script>

<style scoped>
.sg-causes {
  margin: 4px 0 10px;
  padding-left: 18px; /* 与 StaleNotice 文案同一缩进节奏 */
  font-size: 13px;
  line-height: 1.8;
  color: #6b4a12;
}
.sg-ack {
  display: block;
  margin-top: 6px;
  height: auto;
  white-space: normal; /* 长文案自动换行，避免被 el-checkbox 单行截断 */
  font-size: 13px;
  line-height: 1.7;
}
.sg-err {
  margin: 6px 0 0;
  font-size: 12px;
  color: #c45656;
}
</style>
