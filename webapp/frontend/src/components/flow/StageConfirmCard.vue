<template>
  <!-- R27 阶段确认卡：只渲染后端给的摘要与动作，不在前端解析产物 -->
  <div class="stage-card" :class="`st-${card.status}`">
    <header class="stage-head">
      <span class="stage-title">{{ titleText }}</span>
      <el-tag size="small" :type="tagType" effect="light">{{ statusText }}</el-tag>
      <span v-if="card.edited" class="stage-edited">产物已更新，请确认最新版本</span>
    </header>

    <div v-if="card.lines && card.lines.length" class="stage-lines">
      <div v-for="(line, i) in card.lines" :key="i" class="stage-line">{{ line }}</div>
    </div>
    <div v-else-if="card.error" class="stage-gap">摘要暂不可用（{{ card.error }}），仍可继续操作</div>
    <div v-else-if="card.reason === 'with_research_false'" class="stage-gap">建书时未开启联网调研，流程从大纲开始</div>
    <div v-else-if="card.reason === 'merged_into_outline'" class="stage-gap">卷纲已并入大纲一次生成，无需单独确认</div>
    <div v-if="card.message" class="stage-msg">{{ card.message }}</div>

    <footer class="stage-actions">
      <el-button v-if="has('continue')" type="primary" size="small" :loading="busy" @click="$emit('continue')">
        {{ continueText }}
      </el-button>
      <el-button v-if="has('edit')" size="small" :disabled="busy" @click="$emit('edit', card.stage)">
        {{ editText }}
      </el-button>
      <el-button v-if="has('regenerate')" size="small" :disabled="busy" @click="$emit('regenerate', card)">
        放弃本步重生成{{ regenMoney }}
      </el-button>
      <el-button v-if="has('back_outline')" size="small" :disabled="busy" @click="$emit('back-outline')">
        返回改大纲
      </el-button>
    </footer>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  card: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['continue', 'regenerate', 'edit', 'back-outline'])

const has = (name) => (props.card.actions || []).includes(name)
const titleText = computed(() => `${props.card.title || ''} · 等待确认`)
const continueText = computed(() => {
  if (props.card.stage === 'chapters') return '确认开始生成正文'
  // 首次起 run 失败（如余额不足）后回到 ready：这是重试，不是「确认通过」
  return props.card.status === 'ready' ? '重试本步（再次计费）' : '确认继续，进入下一阶段'
})
const editText = computed(() => {
  if (props.card.stage === 'outline') return '打开大纲编辑器'
  if (props.card.stage === 'world') return '打开世界观编辑器'
  return '查看全文'
})
const regenMoney = computed(() => {
  const p = props.card.regenerate_estimate
  return typeof p === 'number' && p > 0 ? `（预估 ¥${p.toFixed(2)}）` : ''
})
const statusText = computed(() => {
  const map = { done: '已完成', ready: '待开始', failed: '失败', stopped: '已停止', skipped: '已跳过' }
  return map[props.card.status] || props.card.status
})
const tagType = computed(() => {
  if (props.card.status === 'failed') return 'danger'
  if (props.card.status === 'stopped') return 'warning'
  if (props.card.status === 'ready') return 'primary'
  return 'success'
})
</script>

<style scoped>
.stage-card { border: 1px solid var(--el-border-color-light); border-radius: 8px; padding: 12px 14px; background: var(--el-bg-color); }
.stage-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stage-title { font-weight: 600; font-size: 14px; }
.stage-edited { font-size: 12px; color: var(--el-color-warning); }
.stage-lines { margin: 8px 0 4px; display: flex; flex-direction: column; gap: 4px; }
.stage-line { font-size: 13px; line-height: 1.6; color: var(--el-text-color-regular); }
.stage-gap, .stage-msg { font-size: 12px; color: var(--el-text-color-secondary); margin: 6px 0; }
.stage-msg { color: var(--el-color-warning); }
.stage-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
</style>
