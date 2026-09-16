<template>
  <div class="char-graph-wrap">
    <h4>人物关系图谱</h4>
    <svg :width="width" :height="height" class="char-graph">
      <!-- 连线 -->
      <line v-for="(e, i) in edges" :key="'e'+i" :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" stroke="#dcdfe6" stroke-width="1.5" />
      <!-- 节点 -->
      <g v-for="(n, i) in nodes" :key="'n'+i">
        <circle :cx="n.x" :cy="n.y" :r="nodeRadius" :fill="nodeColor(n.role)" />
        <text :x="n.x" :y="n.y" text-anchor="middle" dominant-baseline="central" class="node-label">{{ shortName(n.name) }}</text>
      </g>
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ characters: Array })

const width = 600
const nodeRadius = 36

const nodes = computed(() => {
  const chars = props.characters || []
  if (chars.length < 2) return []
  const cx = width / 2
  const cy = Math.max(120, chars.length * 70) / 2
  const r = Math.min(200, 60 + chars.length * 18)
  return chars.map((c, i) => {
    const angle = (i / chars.length) * 2 * Math.PI - Math.PI / 2
    return {
      name: c.name || ('角色' + (i+1)),
      role: c.role || '',
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
    }
  })
})

const height = computed(() => Math.max(180, (props.characters?.length || 0) * 70))

const edges = computed(() => {
  const arr = []
  const ns = nodes.value
  for (let i = 0; i < ns.length; i++) {
    for (let j = i + 1; j < ns.length; j++) {
      arr.push({ x1: ns[i].x, y1: ns[i].y, x2: ns[j].x, y2: ns[j].y })
    }
  }
  return arr
})

function nodeColor(role) {
  if (!role) return '#409eff'
  if (role.includes('主')) return '#409eff'
  if (role.includes('反')) return '#f56c6c'
  if (role.includes('配')) return '#67c23a'
  return '#909399'
}

function shortName(name) {
  if (!name) return '?'
  return name.length > 4 ? name.slice(0, 4) : name
}
</script>

<style scoped>
.char-graph-wrap { margin-bottom: 16px; }
.char-graph { display: block; margin: 0 auto; }
.node-label { font-size: 12px; fill: #fff; font-weight: 500; }
</style>
