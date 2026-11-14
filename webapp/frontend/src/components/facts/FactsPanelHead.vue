<template>
  <header class="fph">
    <div class="fph-main">
      <div class="fph-title-row">
        <h3 class="fph-title">{{ title }}</h3>
        <span class="fph-readonly">{{ readonly ? '只读' : '可编排' }}</span>
      </div>
      <p class="fph-sub">{{ sub }}</p>
    </div>
    <el-button size="small" :loading="loading" @click="$emit('refresh')">刷新</el-button>
  </header>
  <div v-if="stats && stats.length" class="fph-stats">
    <span v-for="s in stats" :key="s.label" class="fph-stat" :class="s.tone || ''">
      <span class="fph-num">{{ s.value }}</span>
      <span class="fph-label">{{ s.label }}</span>
    </span>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, required: true },
  sub: { type: String, default: '' },
  stats: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  readonly: { type: Boolean, default: true },
})
defineEmits(['refresh'])
</script>

<style scoped>
.fph { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.fph-main { min-width: 0; }
.fph-title-row { display: flex; align-items: center; gap: 8px; }
.fph-title { font-family: var(--serif); font-size: 20px; margin: 0; }
.fph-readonly {
  font-size: 11px; color: var(--muted); border: 1px solid var(--card-line);
  border-radius: 999px; padding: 1px 8px; line-height: 1.6;
}
.fph-sub { margin: 4px 0 0; font-size: 13px; color: var(--muted); line-height: 1.7; max-width: 780px; }
.fph-stats { display: flex; flex-wrap: wrap; gap: 8px; }
.fph-stat {
  display: inline-flex; align-items: baseline; gap: 6px;
  background: var(--card); border: 1px solid var(--card-line); border-radius: var(--radius);
  padding: 5px 12px;
}
.fph-num { font-family: var(--serif); font-size: 16px; font-variant-numeric: tabular-nums; }
.fph-label { font-size: 12px; color: var(--muted); }
.fph-stat.danger .fph-num { color: var(--danger); }
.fph-stat.warn .fph-num { color: var(--gold-deep); }
</style>
