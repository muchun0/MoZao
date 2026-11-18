<template>
  <div class="gate-card" :class="`gate-${status}`">
    <header class="gate-head">
      <span class="gate-title">{{ title }}</span>
      <el-tag v-if="statusLabel" size="small" :type="tagType" effect="light">{{ statusLabel }}</el-tag>
      <span v-if="hint" class="gate-hint">{{ hint }}</span>
    </header>

    <!-- 摘要：只给判断用的几个数字，不铺全量内容 -->
    <div v-if="summary.length" class="gate-summary">
      <div v-for="s in summary" :key="s.label" class="gate-stat">
        <span class="gate-stat-value">{{ s.value }}</span>
        <span class="gate-stat-label">{{ s.label }}</span>
      </div>
    </div>

    <!-- 提示：默认只展开最可疑的 maxChecks 条，其余折叠为"还有 N 条" -->
    <div class="gate-checks">
      <div v-if="!checks.length" class="gate-ok">未发现明显问题</div>
      <div v-for="c in visibleChecks" :key="c.key" class="gate-check" :class="`lv-${c.level || 'warn'}`">
        <span class="gate-dot" />
        <span class="gate-check-title">{{ c.title }}</span>
        <span v-if="c.detail" class="gate-check-detail">{{ c.detail }}</span>
        <el-button v-if="c.anchor" link size="small" type="primary" @click="$emit('locate', c.anchor)">定位</el-button>
      </div>
      <el-button v-if="hiddenCount > 0" link size="small" class="gate-more" @click="expanded = !expanded">
        {{ expanded ? '收起提示' : `还有 ${hiddenCount} 条提示` }}
      </el-button>
    </div>

    <footer class="gate-actions">
      <el-button v-if="actions.includes('approve')" type="primary" size="small" :loading="busy"
                 @click="$emit('approve')">{{ approveText }}</el-button>
      <el-button v-if="actions.includes('edit')" size="small" :disabled="busy" @click="$emit('edit')">先编辑</el-button>
      <el-button v-if="actions.includes('regen')" size="small" :disabled="busy" @click="$emit('regen')">重生成这份</el-button>
      <el-button v-if="actions.includes('skip')" size="small" :disabled="busy" @click="$emit('skip')">跳过世界观</el-button>
      <el-button v-if="actions.includes('continue')" type="primary" size="small" :disabled="busy" @click="$emit('continue')">继续下一批</el-button>
      <el-button v-if="actions.includes('finish')" size="small" @click="$emit('finish')">结束，剩余下次再写</el-button>
      <slot name="extra" />
    </footer>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  // pending / approved / skipped / batch
  status: { type: String, default: 'pending' },
  statusLabel: { type: String, default: '' },
  summary: { type: Array, default: () => [] },       // [{label, value}]
  checks: { type: Array, default: () => [] },        // [{level,title,detail,anchor}]
  actions: { type: Array, default: () => ['approve', 'regen'] },
  approveText: { type: String, default: '通过，继续' },
  hint: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  maxChecks: { type: Number, default: 3 },
})
defineEmits(['approve', 'edit', 'regen', 'skip', 'locate', 'continue', 'finish'])

const expanded = ref(false)
// 门切换（大纲门 → 世界观门）时重置折叠态，避免沿用上张卡片的展开状态
watch(() => props.title, () => { expanded.value = false })

const tagType = computed(() => {
  if (props.status === 'approved') return 'success'
  if (props.status === 'skipped') return 'warning'
  if (props.status === 'batch') return 'info'
  return 'warning'
})
const normalized = computed(() => props.checks.map((c, i) => ({ ...c, key: `${i}-${c.title || ''}` })))
const visibleChecks = computed(() =>
  expanded.value ? normalized.value : normalized.value.slice(0, props.maxChecks))
const hiddenCount = computed(() => Math.max(0, normalized.value.length - visibleChecks.value.length))
</script>

<style scoped>
.gate-card {
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-bg-color-overlay);
  padding: 12px 14px;
  margin-bottom: 12px;
}
.gate-pending { border-left: 3px solid var(--el-color-warning); }
.gate-approved, .gate-skipped { border-left: 3px solid var(--el-color-success); }
.gate-batch { border-left: 3px solid var(--el-color-info); }
.gate-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.gate-title { font-weight: 600; font-size: 14px; }
.gate-hint { color: var(--el-text-color-secondary); font-size: 12px; }
.gate-summary { display: flex; gap: 24px; margin: 10px 0 6px; flex-wrap: wrap; }
.gate-stat { display: flex; flex-direction: column; }
.gate-stat-value { font-size: 15px; font-weight: 600; }
.gate-stat-label { font-size: 11px; color: var(--el-text-color-secondary); }
.gate-checks { display: flex; flex-direction: column; gap: 4px; }
.gate-ok { font-size: 12px; color: var(--el-color-success); }
.gate-check { display: flex; align-items: baseline; gap: 6px; font-size: 12px; }
.gate-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--el-color-warning); flex: none; }
.gate-check.lv-error .gate-dot { background: var(--el-color-danger); }
.gate-check-title { font-weight: 500; }
.gate-check-detail {
  color: var(--el-text-color-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%;
}
.gate-more { align-self: flex-start; }
.gate-actions { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
</style>
