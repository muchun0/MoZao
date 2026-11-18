<template>
  <!-- 选区浮动条：贴近选区尾部，点击后把选区交给父级发起改写/扩写 -->
  <div v-if="show" class="float" :style="style">
    <button class="item" @mousedown.prevent="$emit('rewrite')">改写</button>
    <span class="sep">|</span>
    <button class="item" @mousedown.prevent="$emit('expand')">扩写</button>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  x: { type: Number, default: 0 },
  y: { type: Number, default: 0 },
})
defineEmits(['rewrite', 'expand'])

const style = computed(() => {
  const w = 132
  const left = Math.min(Math.max(8, props.x - w / 2), window.innerWidth - w - 8)
  const top = Math.min(Math.max(8, props.y), window.innerHeight - 56)
  return { left: `${left}px`, top: `${top}px`, width: `${w}px` }
})
</script>

<style scoped>
.float {
  position: fixed; z-index: 3000; display: flex; align-items: center; justify-content: center;
  height: 34px; border-radius: 10px; background: var(--indigo-deep); color: #f3eee0;
  box-shadow: 0 6px 20px rgba(24,33,61,.32); user-select: none;
}
.item {
  flex: 1; height: 100%; border: 0; background: transparent; color: inherit;
  font-family: var(--sans); font-size: 13px; cursor: pointer; border-radius: 10px;
}
.item:hover { background: rgba(255,255,255,.12); }
.sep { opacity: .3; }
</style>
