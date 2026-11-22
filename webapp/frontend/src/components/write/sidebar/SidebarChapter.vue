<template>
  <!-- 「本章」页签：章计划 + 出场人物 + 本章相关伏笔（纯展示，数据由侧栏统一拉取） -->
  <div class="panel">
    <p v-if="loading" class="hint">加载中…</p>
    <p v-else-if="error" class="err">{{ error }}</p>
    <template v-else>
      <p v-if="!data" class="empty">先选择一章再查看本章参考</p>
      <template v-else>
        <p v-if="!data.chapter" class="empty">本章还没有大纲计划，先到大纲页补充</p>
        <section v-else class="sec">
          <h5>章计划 <em>第{{ data.ch }}章</em></h5>
          <p class="title">{{ data.chapter.title || `第${data.ch}章` }}</p>
          <div v-for="f in planFields" :key="f.key" class="row">
            <span class="lbl">{{ f.label }}</span>
            <p class="txt">{{ data.chapter[f.key] || '—' }}</p>
          </div>
        </section>

        <section class="sec">
          <h5>出场人物 <em>{{ data.characters.length }}</em></h5>
          <div v-for="c in data.characters" :key="c.name" class="card">
            <p class="card-title">{{ c.name }} <span v-if="c.role" class="chip">{{ c.role }}</span></p>
            <p v-if="c.personality" class="txt">{{ c.personality }}</p>
            <p v-if="c.speech_style" class="txt muted">说话方式：{{ c.speech_style }}</p>
          </div>
          <p v-if="!data.characters.length" class="empty-s">本章计划里没有命中世界观人物</p>
        </section>

        <section class="sec">
          <h5>本章伏笔 <em>{{ data.foreshadow.items.length }}</em></h5>
          <button v-for="f in data.foreshadow.items" :key="f.name" class="item" @click="jump(f)">
            <span class="item-name">{{ f.name }}</span>
            <span class="chip" :class="sevClass(f.severity)">{{ f.state_label || f.state }}</span>
            <span class="item-sub">{{ revealInText(f.reveal_in) }}</span>
          </button>
          <p v-if="!data.available.foreshadow" class="empty-s">伏笔账本未开启</p>
          <p v-else-if="!data.foreshadow.items.length" class="empty-s">本章没有临期或逾期伏笔</p>
        </section>
      </template>
    </template>
  </div>
</template>

<script setup>
import { revealInText } from '../../../utils/writingSidebar'

defineProps({
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['jump'])

// 只展示会真正进入写作上下文的章计划字段（character_moves 是代码事实字段名）
const planFields = [
  { key: 'core_event', label: '核心事件' },
  { key: 'goal', label: '本章目标' },
  { key: 'conflict', label: '冲突' },
  { key: 'cliffhanger', label: '章末钩子' },
  { key: 'character_moves', label: '人物动线' },
  { key: 'foreshadowing', label: '伏笔计划' },
]

function sevClass(severity) {
  return severity === 'high' ? 'bad' : (severity === 'low' ? 'warn' : '')
}

function jump(item) {
  const target = Number(item.reveal_ch) || Number(item.buried_ch) || 0
  if (target > 0) emit('jump', target)
}
</script>
