<template>
  <div class="tools-grid">
    <el-card v-for="tool in tools" :key="tool.title" class="tool-card">
      <template #header><b>{{ tool.title }}</b></template>
      <p class="muted">{{ tool.desc }}</p>
      <slot :name="tool.slot" />
    </el-card>
  </div>
</template>

<script setup>
defineProps({ pid: String })
const tools = [
  { title: '质量校验', desc: 'AI检查设定一致性/剧情逻辑/合规', slot: 'quality' },
  { title: '节奏分析', desc: '每章字数分布与进度', slot: 'rhythm' },
  { title: '多方案生成', desc: '为章节目标生成3个走向', slot: 'alt' },
  { title: '批量生成', desc: '指定章节范围后台逐章生成', slot: 'batch' },
  { title: '大纲快照', desc: '保存/回滚大纲版本', slot: 'snap' },
  { title: '伏笔管理', desc: '追踪已埋伏笔', slot: 'foreshadow' },
  { title: '核心设定锁定', desc: 'AI必须遵守的铁律', slot: 'locked' },
]
</script>

<style scoped>
.tools-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.muted { color: #909399; font-size: 13px; }
</style>
