<template>
  <!-- 档3 生成前一致性门禁：409 stale_gate 的二次确认（可确认继续，不是硬拒绝） -->
  <el-dialog :model-value="visible" title="生成前一致性检查" width="560px"
             :close-on-click-modal="false" @update:model-value="(v) => { if (!v) close() }">
    <!-- 复用 R13 过期提示视觉（StaleNotice），不新造一套视觉语言 -->
    <StaleNotice :notice="notice" />
    <ul v-if="causeLines.length" class="sg-causes">
      <li v-for="(line, i) in causeLines" :key="i">{{ line }}</li>
    </ul>
    <!-- REQ-R23：重写不再在本弹窗二次提交，统一到 Agent 页（章节写手 · 区间）发起 -->
    <p class="sg-note">重写前文请到 Agent 页用「章节写手 · 区间」发起，这里只决定是否继续往下写。</p>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button :disabled="starting" :loading="starting" @click="continueRun">仍然继续</el-button>
      <el-button type="warning" @click="goAgent">
        去 Agent 页从第 {{ detail.regenerate_from }} 章起重写
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import StaleNotice from './StaleNotice.vue'
import { runAgent, errorDetail } from '../../utils/agentRunClient'

const emit = defineEmits(['started', 'open-agent'])

const visible = ref(false)
const detail = ref({})
const starting = ref(false)
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
  advice: `可以到 Agent 页从第 ${detail.value.regenerate_from} 章起向后重写，或已知不一致继续（新章会标注"基于旧版"）。`,
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

async function open(pidValue, d, payload, after) {
  // 非门禁错误返回 false，让调用方走原有的错误提示
  if (!d || typeof d !== 'object' || d.code !== 'stale_gate') return false
  pid = pidValue
  detail.value = d
  pending = { payload: payload || {}, after }
  starting.value = false
  visible.value = true
  return true
}

function close() {
  visible.value = false
}

async function start(payload) {
  if (!pending) return
  starting.value = true
  try {
    const { body } = await runAgent(pid, payload)
    const after = pending.after
    close()
    if (typeof after === 'function') after(body)
    emit('started', body)
  } catch (e) {
    const d = e?.response?.data?.detail
    if (d && typeof d === 'object' && d.code === 'stale_gate') {
      // 仍然被门禁拦：刷新提示，让用户重新决策（重写一律去 Agent 页）
      detail.value = d
      return
    }
    ElMessage.error(errorDetail(e, '启动失败'))
  } finally {
    starting.value = false
  }
}

function continueRun() {
  // 已知不一致继续：放行并由后端给新章打 unstable 标注
  const payload = { ...(pending?.payload || {}), allow_inconsistent: true }
  start(payload)
}

function goAgent() {
  const from = Number(detail.value.regenerate_from) || 1
  close()
  emit('open-agent', { from_ch: from })
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
.sg-note {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.7;
  color: #8a6d3b;
}
</style>
