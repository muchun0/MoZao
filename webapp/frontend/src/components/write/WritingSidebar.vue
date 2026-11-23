<template>
  <!-- R29 写作参考侧栏：默认收起为窄条，可展开/拖宽/记忆；窄屏降级为覆盖抽屉 -->
  <div v-if="open && narrow" class="ws-mask" @click="collapse" />
  <aside class="ws" :class="{ open, narrow, overlay: open && narrow }"
         :style="open ? { width: `${width}px` } : null">
    <!-- 收起态：只留一个入口，点开后再在顶部选页签；不再把 6 个页签压成竖排单字 -->
    <nav v-if="!open" class="rail">
      <button class="rail-btn rail-open" title="展开参考侧栏（本章上下文 / 大纲 / 人物 / 伏笔 / 设定 / 检索）" @click="expand()">
        <span class="rail-text">参考</span>
        <span v-if="badgeTotal" class="dot" :class="badgeClass">{{ badgeTotal }}</span>
      </button>
    </nav>

    <template v-else>
      <header class="ws-head">
        <button v-for="t in SIDEBAR_TABS" :key="t.key" class="ws-tab"
                :class="{ on: tab === t.key }" :title="t.label" @click="pick(t.key)">
          {{ t.label }}
          <span v-if="t.key === 'foreshadow' && badgeTotal" class="dot" :class="badgeClass">
            {{ badgeTotal }}
          </span>
        </button>
        <button class="ws-fold" title="收起参考侧栏，恢复 AI 写作面板" @click="collapse">收起</button>
      </header>

      <div class="ws-body">
        <KeepAlive>
          <SidebarChapter v-if="tab === 'chapter'" :data="context"
                          :loading="ctxLoading" :error="ctxError" @jump="jump" />
          <SidebarOutline v-else-if="tab === 'outline'" :pid="pid" :ch="ch" @jump="jump" />
          <SidebarCharacters v-else-if="tab === 'characters'" :pid="pid" />
          <SidebarForeshadow v-else-if="tab === 'foreshadow'" :pid="pid" @jump="jump" />
          <SidebarSettings v-else-if="tab === 'settings'" :pid="pid" />
          <StorySearch v-else :pid="pid" @open="hit => emit('open-search', hit)" />
        </KeepAlive>
      </div>

      <span class="ws-resize" title="拖动调整宽度" @mousedown.prevent="startResize" />
    </template>
  </aside>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { getWritingContext } from '../../api/writing'
import { errorDetail } from '../../utils/agentRun'
import {
  SIDEBAR_TABS, clampSidebarWidth, foreshadowBadge, isNarrowViewport,
  readSidebarState, writeSidebarState,
} from '../../utils/writingSidebar'
import SidebarChapter from './sidebar/SidebarChapter.vue'
import SidebarOutline from './sidebar/SidebarOutline.vue'
import SidebarCharacters from './sidebar/SidebarCharacters.vue'
import SidebarForeshadow from './sidebar/SidebarForeshadow.vue'
import SidebarSettings from './sidebar/SidebarSettings.vue'
import StorySearch from './StorySearch.vue'

const props = defineProps({
  pid: { type: String, required: true },
  ch: { type: Number, default: 0 },
})
const emit = defineEmits(['jump', 'open-search'])

const state = ref(readSidebarState(globalThis.localStorage))
const open = computed(() => state.value.open)
const tab = computed(() => state.value.tab)
const width = computed(() => clampSidebarWidth(state.value.width))
const narrow = ref(false)

const context = ref(null)
const ctxLoading = ref(false)
const ctxError = ref('')

const badge = computed(() => foreshadowBadge(context.value?.foreshadow?.counts))
const badgeTotal = computed(() => badge.value.total)
const badgeClass = computed(() => (badge.value.overdue > 0 ? 'bad' : 'warn'))

function persist() {
  writeSidebarState(globalThis.localStorage, state.value)
}
function expand(key) {
  state.value.open = true
  if (key) state.value.tab = key
  persist()
}
function collapse() {
  state.value.open = false
  persist()
}
function pick(key) {
  if (state.value.tab === key) return
  state.value.tab = key
  persist()
}
function jump(no) {
  if (Number(no) > 0) emit('jump', Number(no))
}

// ---------- 本章聚合（角标与本章视图共用一次请求） ----------
async function loadContext() {
  if (!props.ch) {
    context.value = null
    ctxError.value = ''
    return
  }
  ctxLoading.value = true
  ctxError.value = ''
  try {
    context.value = await getWritingContext(props.pid, props.ch)
  } catch (e) {
    context.value = null
    ctxError.value = errorDetail(e, '本章参考加载失败')
  } finally {
    ctxLoading.value = false
  }
}

watch(() => [props.pid, props.ch], loadContext, { immediate: true })

// ---------- 拖宽 ----------
let drag = null
function startResize(e) {
  drag = { x: e.clientX, width: state.value.width }
  window.addEventListener('mousemove', onResize)
  window.addEventListener('mouseup', endResize)
}
function onResize(e) {
  if (!drag) return
  // 侧栏在右侧：向左拖动（clientX 变小）即变宽
  state.value.width = clampSidebarWidth(drag.width + (drag.x - e.clientX))
}
function endResize() {
  drag = null
  persist()
  window.removeEventListener('mousemove', onResize)
  window.removeEventListener('mouseup', endResize)
}

// ---------- 窄屏 ----------
function syncNarrow() {
  narrow.value = isNarrowViewport(window.innerWidth)
}

onMounted(() => {
  syncNarrow()
  window.addEventListener('resize', syncNarrow)
})
onUnmounted(() => {
  window.removeEventListener('resize', syncNarrow)
  window.removeEventListener('mousemove', onResize)
  window.removeEventListener('mouseup', endResize)
})
</script>

<style scoped>
.ws {
  flex: 0 0 40px; width: 40px; height: 100%; display: flex; flex-direction: column;
  background: var(--card); border-left: 1px solid var(--card-line); position: relative;
  transition: width .16s ease;
}
.ws.open { flex: 0 0 auto; }
.ws.overlay { position: absolute; top: 0; right: 0; bottom: 0; z-index: 40; box-shadow: var(--shadow); }
.ws-mask { position: absolute; inset: 0; z-index: 39; background: rgba(24, 24, 24, .28); }

.rail { display: flex; flex-direction: column; align-items: center; gap: 2px; padding-top: 8px; }
.rail-btn {
  width: 30px; height: 34px; border: 0; border-radius: 8px; background: transparent;
  color: var(--ink-soft); font-family: var(--serif); font-size: 13px; cursor: pointer;
  position: relative;
}
.rail-btn:hover { background: var(--indigo-soft); }
/* 收起态只占 40px：两个竖排字比单个「参」更像入口，也避免被读成残字 */
.rail-text { display: block; writing-mode: vertical-rl; letter-spacing: 3px; line-height: 1.25; font-size: 12px; }
.rail-fold { margin-top: auto; margin-bottom: 10px; color: var(--muted); }

.ws-head {
  display: flex; align-items: stretch; border-bottom: 1px solid var(--card-line);
  padding-right: 2px; flex: 0 0 auto;
}
.ws-tab {
  flex: 1; min-width: 0; border: 0; background: transparent; cursor: pointer;
  padding: 9px 2px; font-family: var(--sans); font-size: 11px; color: var(--muted);
  display: flex; align-items: center; justify-content: center; gap: 2px;
}
.ws-tab.on { color: var(--indigo); font-weight: 600; box-shadow: inset 0 -2px 0 var(--indigo); }
.ws-fold {
  flex: 0 0 auto; border: 0; background: transparent; color: var(--indigo);
  cursor: pointer; font-size: 12px; padding: 0 8px; white-space: nowrap;
}
.ws-body { flex: 1; min-height: 0; overflow: hidden; }
.ws-body :deep(.panel) { height: 100%; overflow-y: auto; padding: 12px; box-sizing: border-box; }

.dot {
  min-width: 14px; height: 14px; padding: 0 3px; border-radius: 7px; color: #fff;
  font-size: 10px; line-height: 14px; text-align: center; font-family: var(--sans);
}
.dot.bad { background: var(--danger); }
.dot.warn { background: var(--gold-deep); }

.ws-resize {
  position: absolute; left: -3px; top: 0; bottom: 0; width: 6px; cursor: col-resize;
}
.ws-resize:hover { background: var(--indigo-soft); }

/* ---- 子面板共用视觉（子组件只写结构类名，样式集中在此） ---- */
.ws-body :deep(.hint),
.ws-body :deep(.empty),
.ws-body :deep(.empty-s) { color: var(--muted); font-size: 12px; line-height: 1.8; }
.ws-body :deep(.empty) { text-align: center; padding: 18px 8px; }
.ws-body :deep(.empty-s) { padding: 4px 0; }
.ws-body :deep(.err) { color: var(--danger); font-size: 12px; }
.ws-body :deep(.sec) { margin-bottom: 14px; }
.ws-body :deep(.sec h5) {
  display: flex; align-items: baseline; gap: 6px; margin: 0 0 6px;
  font-family: var(--serif); font-size: 13px; color: var(--ink-soft); letter-spacing: 1px;
}
.ws-body :deep(.sec h5 em),
.ws-body :deep(.group summary em) { margin-left: auto; font-style: normal; color: var(--muted); font-size: 11px; }
.ws-body :deep(.title) {
  margin: 0 0 8px; font-family: var(--serif); font-size: 14px; color: var(--ink);
}
.ws-body :deep(.row) { margin-bottom: 6px; }
.ws-body :deep(.lbl) { font-size: 11px; color: var(--indigo); }
.ws-body :deep(.txt) {
  margin: 2px 0 0; font-size: 12px; line-height: 1.75; color: var(--ink-soft);
  white-space: pre-wrap; word-break: break-word;
}
.ws-body :deep(.txt.muted) { color: var(--muted); }
.ws-body :deep(.clamp) {
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.ws-body :deep(.card) {
  border: 1px solid var(--card-line); border-radius: 10px; padding: 8px 10px;
  margin-bottom: 6px; background: var(--paper);
}
.ws-body :deep(.card-title) {
  margin: 0; font-family: var(--serif); font-size: 13px; color: var(--ink);
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}
.ws-body :deep(.chip) {
  display: inline-block; padding: 1px 6px; border-radius: 8px; font-size: 10px;
  background: var(--indigo-soft); color: var(--indigo);
}
.ws-body :deep(.chip.bad) { background: rgba(200, 60, 60, .12); color: var(--danger); }
.ws-body :deep(.chip.warn) { background: rgba(180, 140, 40, .16); color: var(--gold-deep); }
.ws-body :deep(.item) {
  width: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  border: 1px solid var(--card-line); border-radius: 10px; background: var(--paper);
  padding: 8px 10px; margin-bottom: 6px; cursor: pointer; text-align: left;
}
.ws-body :deep(.item:hover) { border-color: var(--gold-line); }
.ws-body :deep(.item.on) { border-color: var(--indigo); background: var(--indigo-soft); }
.ws-body :deep(.item-name) { font-family: var(--serif); font-size: 12px; color: var(--ink); }
.ws-body :deep(.item-sub) {
  flex: 1 1 100%; font-size: 11px; color: var(--muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.ws-body :deep(.group) { border-bottom: 1px dashed var(--card-line); padding: 6px 0; }
.ws-body :deep(.group summary) {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  font-family: var(--serif); font-size: 13px; color: var(--ink-soft); padding: 2px 0;
}
.ws-body :deep(.filters) { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 10px; }
.ws-body :deep(.filter) {
  border: 1px solid var(--card-line); border-radius: 10px; background: var(--paper);
  color: var(--muted); font-size: 11px; padding: 3px 8px; cursor: pointer;
}
.ws-body :deep(.filter.on) { border-color: var(--indigo); color: var(--indigo); background: var(--indigo-soft); }
.ws-body :deep(.filter em) { margin-left: 3px; font-style: normal; opacity: .75; }
.ws-body :deep(.chips) { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 0; }
</style>
