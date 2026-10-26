<template>
  <div class="char-graph-wrap">
    <div class="graph-header">
      <h4>人物关系图谱</h4>
      <div class="graph-legend">
        <span class="legend-item"><span class="dot dot-protagonist"></span>主角</span>
        <span class="legend-item"><span class="dot dot-opponent"></span>反派</span>
        <span class="legend-item"><span class="dot dot-supporting"></span>配角</span>
      </div>
    </div>
    <div class="graph-canvas" :style="{ height: canvasHeight + 'px' }">
      <svg :width="width" :height="canvasHeight" class="char-graph"
           @mouseleave="hoveredNode = null">
        <!-- 连线：只画中心人物与其他人的连线 -->
        <line v-for="(e, i) in edges" :key="'e'+i"
              :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
              :stroke="edgeColor(e, i)"
              :stroke-width="edgeWidth(e, i)"
              :opacity="edgeOpacity(e, i)" />
        <!-- 节点 -->
        <g v-for="(n, i) in nodes" :key="'n'+i"
           @mouseenter="hoveredNode = i"
           :class="{ 'is-hovered': hoveredNode === i }"
           style="cursor: pointer;">
          <circle :cx="n.x" :cy="n.y" :r="n.r" :fill="nodeColor(n.role)"
                  :opacity="nodeOpacity(i)" />
          <text :x="n.x" :y="n.y" text-anchor="middle" dominant-baseline="central"
                class="node-label" :fill="nodeTextColor(n.role)">{{ shortName(n.name) }}</text>
          <text v-if="n.role" :x="n.x" :y="n.y + n.r + 14" text-anchor="middle"
                class="node-role">{{ roleLabel(n.role) }}</text>
        </g>
      </svg>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({ characters: Array })
const hoveredNode = ref(null)

const width = 560
const centerX = width / 2

const canvasHeight = computed(() => {
  const n = props.characters?.length || 0
  if (n <= 1) return 200
  return Math.max(280, n * 55 + 80)
})

// 主角放中心，其他人环绕
const nodes = computed(() => {
  const chars = props.characters || []
  if (!chars.length) return []

  // 找主角（role含"主"），找不到就取第一个
  const protagonistIdx = chars.findIndex(c => (c.role || '').includes('主'))
  const mcIdx = protagonistIdx >= 0 ? protagonistIdx : 0

  const cx = centerX
  const cy = canvasHeight.value / 2

  const result = []

  // 中心人物
  const mc = chars[mcIdx]
  result.push({
    name: mc.name || '主角',
    role: mc.role || '主角',
    x: cx,
    y: cy,
    r: 42,
    isCenter: true,
  })

  // 环绕人物
  const others = chars.filter((_, i) => i !== mcIdx)
  const n = others.length
  if (n === 0) return result

  const radius = Math.min(200, 80 + n * 22)
  others.forEach((c, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2
    result.push({
      name: c.name || ('角色' + (i+1)),
      role: c.role || '',
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      r: 32,
      isCenter: false,
    })
  })

  return result
})

// 连线：中心人物连到所有其他人
const edges = computed(() => {
  const ns = nodes.value
  if (ns.length < 2) return []
  const center = ns[0]
  const arr = []
  for (let i = 1; i < ns.length; i++) {
    arr.push({
      x1: center.x, y1: center.y,
      x2: ns[i].x, y2: ns[i].y,
      targetIdx: i,
    })
  }
  return arr
})

function edgeColor(e, i) {
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) {
    return '#c9a227'
  }
  return '#d9d4c5'
}

function edgeWidth(e, i) {
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) {
    return 2
  }
  return 1.2
}

function edgeOpacity(e, i) {
  if (hoveredNode.value === null) return 1
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) return 1
  return 0.2
}

function nodeOpacity(i) {
  if (hoveredNode.value === null) return 1
  if (hoveredNode.value === i) return 1
  // 其他节点降低透明度
  const edge = edges.value.find(e => e.targetIdx === i)
  if (edge && (hoveredNode.value === 0 || hoveredNode.value === edge.targetIdx)) return 1
  return 0.3
}

function nodeColor(role) {
  if (!role) return '#909399'
  if (role.includes('主')) return '#232f55'
  if (role.includes('反')) return '#b3402f'
  if (role.includes('配')) return '#6b8e5a'
  return '#909399'
}

function nodeTextColor(role) {
  return '#fff'
}

function roleLabel(role) {
  if (!role) return ''
  if (role.includes('主')) return '主角'
  if (role.includes('反')) return '反派'
  if (role.includes('配')) return '配角'
  return role.slice(0, 4)
}

function shortName(name) {
  if (!name) return '?'
  return name.length > 4 ? name.slice(0, 4) : name
}
</script>

<style scoped>
.char-graph-wrap {
  margin-bottom: 20px;
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  padding: 16px;
}

.graph-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.graph-header h4 {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  font-family: var(--serif);
}

.graph-legend {
  display: flex;
  gap: 14px;
  font-size: 11px;
  color: var(--muted);
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.dot-protagonist { background: #232f55; }
.dot-opponent { background: #b3402f; }
.dot-supporting { background: #6b8e5a; }

.graph-canvas {
  display: flex;
  justify-content: center;
  overflow: hidden;
}

.char-graph {
  display: block;
  max-width: 100%;
}

.node-label {
  font-size: 12px;
  font-weight: 500;
  text-anchor: middle;
  dominant-baseline: central;
}

.node-role {
  font-size: 10px;
  fill: var(--muted);
  text-anchor: middle;
}

g.is-hovered circle {
  filter: drop-shadow(0 0 6px rgba(201,162,39,.4));
}
</style>
