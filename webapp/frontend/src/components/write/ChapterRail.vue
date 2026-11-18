<template>
  <!-- 写作页左侧章节目录：只读列表 + 当前章高亮 -->
  <aside class="rail">
    <div class="rail-head">
      <span class="rail-title">章节</span>
      <span class="rail-count">{{ chapters.length }}</span>
    </div>
    <div class="rail-body">
      <button v-for="c in chapters" :key="c.no" class="rail-item"
              :class="{ active: c.no === modelValue }" @click="$emit('update:modelValue', c.no)">
        <span class="rail-no">{{ c.no }}</span>
        <span class="rail-name">{{ c.title || `第${c.no}章` }}</span>
        <span class="rail-words">{{ c.words }}</span>
      </button>
      <p v-if="!chapters.length" class="rail-empty">还没有正文，先去生成章节</p>
    </div>
  </aside>
</template>

<script setup>
defineProps({
  chapters: { type: Array, default: () => [] },
  modelValue: { type: Number, default: 0 },
})
defineEmits(['update:modelValue'])
</script>

<style scoped>
.rail {
  width: 220px; flex: 0 0 220px; height: 100%; display: flex; flex-direction: column;
  background: var(--card); border-right: 1px solid var(--card-line);
}
.rail-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 16px 10px; color: var(--ink-soft); font-size: 13px;
}
.rail-title { font-family: var(--serif); letter-spacing: 2px; }
.rail-count { color: var(--muted); font-size: 12px; }
.rail-body { flex: 1; overflow-y: auto; padding: 4px 8px 16px; }
.rail-item {
  width: 100%; display: flex; align-items: baseline; gap: 8px;
  padding: 9px 10px; margin-bottom: 2px; border: 0; border-radius: 10px;
  background: transparent; color: var(--ink-soft); text-align: left; cursor: pointer;
  font-family: var(--sans); font-size: 13px;
}
.rail-item:hover { background: var(--indigo-soft); }
.rail-item.active { background: var(--indigo); color: #f4f1e6; }
.rail-no { font-size: 12px; opacity: .7; min-width: 18px; }
.rail-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rail-words { font-size: 11px; opacity: .55; }
.rail-empty { color: var(--muted); font-size: 12px; padding: 12px; line-height: 1.7; }
</style>
