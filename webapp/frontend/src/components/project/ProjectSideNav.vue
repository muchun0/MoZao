<template>
  <!-- 项目详情左侧栏导航：图标 + 名称，激活态深靛蓝实底 -->
  <aside class="pd-nav">
    <div v-for="g in groups" :key="g.label" class="nav-group">
      <div class="nav-group-title">{{ g.label }}</div>
      <div class="nav-list" role="tablist" :aria-label="g.label">
        <button
          v-for="item in g.items"
          :key="item.key"
          type="button"
          role="tab"
          :aria-selected="item.key === modelValue"
          class="nav-item"
          :class="{ active: item.key === modelValue }"
          @click="$emit('update:modelValue', item.key)"
        >
          <span class="nav-icon" v-html="item.icon"></span>
          <span class="nav-name">{{ item.name }}</span>
          <span v-if="item.badge" class="nav-badge">{{ item.badge }}</span>
        </button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: String, required: true },
  chapterCount: { type: Number, default: 0 },
})
defineEmits(['update:modelValue'])

// SVG 线性图标，stroke=currentColor，激活态自动变金色
const ICONS = {
  chapters: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 4h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z"/></svg>`,
  outline: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/></svg>`,
  world: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>`,
  assistant: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>`,
  agent: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="8" width="16" height="12" rx="2"/><circle cx="12" cy="14" r="2"/><path d="M12 8V4"/><circle cx="12" cy="3" r="1"/></svg>`,
  tools: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>`,
}

const groups = computed(() => [
  {
    label: '内容',
    items: [
      { key: 'chapters', name: '章节', icon: ICONS.chapters, badge: props.chapterCount || '' },
      { key: 'outline', name: '大纲', icon: ICONS.outline },
      { key: 'world', name: '世界观', icon: ICONS.world },
    ],
  },
  {
    label: '智能',
    items: [
      { key: 'assistant', name: '小墨AI', icon: ICONS.assistant },
      { key: 'agent', name: 'Agent', icon: ICONS.agent },
      { key: 'tools', name: '工具', icon: ICONS.tools },
    ],
  },
])
</script>

<style scoped>
.pd-nav { display: flex; flex-direction: column; gap: 16px; }
.nav-group-title { font-size: 12px; color: var(--muted); letter-spacing: .08em; margin-bottom: 6px; padding-left: 12px; }
.nav-list { display: flex; flex-direction: column; gap: 2px; }

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink-soft);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background-color .15s ease, color .15s ease;
}
.nav-item:hover { background: var(--indigo-soft); }
.nav-item.active {
  background: var(--indigo);
  color: #e9e4d2;
}
.nav-item:focus-visible { outline: 2px solid var(--gold-line); outline-offset: 1px; }

.nav-icon {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  opacity: .65;
}
.nav-icon :deep(svg) {
  width: 18px;
  height: 18px;
}
.nav-item.active .nav-icon { opacity: 1; color: #f0e2b6; }

.nav-name { flex: 1; line-height: 1.3; }

.nav-badge {
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.nav-item.active .nav-badge { color: rgba(233,228,210,.6); }

/* 窄屏降级为横向滚动 */
@media (max-width: 900px) {
  .pd-nav { flex-direction: row; gap: 20px; overflow-x: auto; padding-bottom: 4px; }
  .nav-group { flex: 0 0 auto; }
  .nav-list { flex-direction: row; gap: 6px; }
  .nav-item { flex: 0 0 auto; width: auto; padding: 8px 12px; }
  .nav-name { display: none; }
  .nav-badge { display: none; }
}
</style>
