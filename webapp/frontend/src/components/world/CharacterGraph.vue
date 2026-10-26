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
        <!-- 连线：中心人物与其他人的连线，线上标注关系 -->
        <g v-for="(e, i) in edges" :key="'e'+i">
          <line :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
                :stroke="edgeColor(e)" :stroke-width="edgeWidth(e)"
                :opacity="edgeOpacity(e)" />
          <!-- 关系标签：线的中点 -->
          <text v-if="e.relationText"
                :x="e.midX" :y="e.midY"
                text-anchor="middle" class="edge-label"
                :opacity="edgeLabelOpacity(e)">
            {{ e.relationText }}
          </text>
        </g>
        <!-- 节点 -->
        <g v-for="(n, i) in nodes" :key="'n'+i"
           @mouseenter="hoveredNode = i"
           style="cursor: pointer;">
          <circle :cx="n.x" :cy="n.y" :r="n.r" :fill="nodeColor(n.role)"
                  :opacity="nodeOpacity(i)" />
          <text :x="n.x" :y="n.y" text-anchor="middle" dominant-baseline="central"
                class="node-label">{{ shortName(n.name) }}</text>
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
  return Math.max(300, n * 60 + 80)
})

// 主角放中心，其他人环绕
const nodes = computed(() => {
  const chars = props.characters || []
  if (!chars.length) return []

  const protagonistIdx = chars.findIndex(c => (c.role || '').includes('主'))
  const mcIdx = protagonistIdx >= 0 ? protagonistIdx : 0

  const cx = centerX
  const cy = canvasHeight.value / 2

  const result = []
  const mc = chars[mcIdx]
  result.push({
    name: mc.name || '主角',
    role: mc.role || '主角',
    relations: mc.relations || '',
    x: cx,
    y: cy,
    r: 42,
    isCenter: true,
  })

  const others = chars.filter((_, i) => i !== mcIdx)
  const n = others.length
  if (n === 0) return result

  const radius = Math.min(200, 80 + n * 24)
  others.forEach((c, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2
    result.push({
      name: c.name || ('角色' + (i+1)),
      role: c.role || '',
      relations: c.relations || '',
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      r: 32,
      isCenter: false,
    })
  })

  return result
})

// 提取关系标签：从人物的relations字段里取前几个字
function extractRelation(relText, name) {
  if (!relText) return ''
  // 去掉主角名字本身，只留关系描述
  let t = relText.trim()
  // 太长就截断
  if (t.length > 8) t = t.slice(0, 8) + '…'
  return t
}

// 连线：中心人物连到所有其他人，线上标注关系
const edges = computed(() => {
  const ns = nodes.value
  if (ns.length < 2) return []
  const center = ns[0]
  const arr = []
  for (let i = 1; i < ns.length; i++) {
    const target = ns[i]
    // 优先用外围人物的relations，其次用中心人物的
    const rel = target.relations || center.relations || ''
    const relText = extractRelation(rel, target.name)
    arr.push({
      x1: center.x, y1: center.y,
      x2: target.x, y2: target.y,
      midX: (center.x + target.x) / 2,
      midY: (center.y + target.y) / 2,
      targetIdx: i,
      relationText: relText,
    })
  }
  return arr
})

function edgeColor(e) {
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) {
    return '#c9a227'
  }
  return '#d9d4c5'
}

function edgeWidth(e) {
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) return 2
  return 1.2
}

function edgeOpacity(e) {
  if (hoveredNode.value === null) return 1
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) return 1
  return 0.2
}

function edgeLabelOpacity(e) {
  if (hoveredNode.value === null) return 0.85
  if (hoveredNode.value === 0 || hoveredNode.value === e.targetIdx) return 1
  return 0.15
}

function nodeOpacity(i) {
  if (hoveredNode.value === null) return 1
  if (hoveredNode.value === i) return 1
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
  margin-bottom: 16px;
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  padding: 14px 16px;
}

.graph-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.graph-header h4 {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  font-family: var(--serif);
}

.graph-legend {
  display: flex;
  gap: 12px;
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
  fill: #fff;
  text-anchor: middle;
  dominant-baseline: central;
}

.node-role {
  font-size: 10px;
  fill: var(--muted);
  text-anchor: middle;
}

.edge-label {
  font-size: 10px;
  fill: var(--ink-soft);
  paint-order: stroke;
  stroke: var(--paper);
  stroke-width: 3;
  stroke-linejoin: round;
}
</style>
