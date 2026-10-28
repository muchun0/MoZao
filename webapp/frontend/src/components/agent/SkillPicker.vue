<template>
  <div class="skill-picker">
    <div class="skill-picker-head">
      <span class="skill-picker-title">挂载写作技能</span>
      <span class="skill-picker-count">已选 {{ modelValue.length }}/{{ max }}</span>
    </div>
    <div v-if="loading" class="skill-picker-tip">加载技能库…</div>
    <div v-else-if="!groups.length" class="skill-picker-tip">暂无可用技能</div>
    <div v-for="g in groups" :key="g.role" class="skill-group">
      <div class="skill-group-name">{{ g.label }}</div>
      <el-checkbox
        v-for="s in g.skills"
        :key="s.id"
        :model-value="selected.has(s.id)"
        :disabled="!selected.has(s.id) && modelValue.length >= max"
        class="skill-item"
        @change="toggle(s.id)"
      >
        <span class="skill-name">{{ s.name }}</span>
        <span class="skill-desc">{{ s.description }}</span>
        <span class="skill-words">注入约 {{ promptWords(s) }} 字</span>
      </el-checkbox>
    </div>
    <div v-if="modelValue.length >= max" class="skill-picker-limit">已达挂载上限（{{ max }} 个），如需更换请先取消已选项</div>
  </div>
</template>

<script setup>
// Skill 多选器（TECH-Agent用户化与版本化 §7/§10.2，阶段 1C 最小集）：
// 按 role 分组展示平台技能，挂载上限 5（全局 token 预算配套），单技能显示注入字数提示。
// 技能整理（去重/统一编号/分类字段）属阶段 3，本组件只读现有 skills 表字段。
import { computed, onMounted, ref } from 'vue'
import request from '../../api/request'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  max: { type: Number, default: 5 },
})
const emit = defineEmits(['update:modelValue'])

const skills = ref([])
const loading = ref(false)

const ROLE_LABELS = {
  '': '全局通用',
  writer: '写手',
  architect: '架构师',
  reviewer: '审稿',
  researcher: '调研',
}

const selected = computed(() => new Set(props.modelValue))

// 按 role 分组，组序固定：全局 → 架构 → 写手 → 审稿 → 调研 → 其他
const groups = computed(() => {
  const order = ['', 'architect', 'writer', 'reviewer', 'researcher']
  const byRole = {}
  for (const s of skills.value) {
    const role = s.role || ''
    if (!byRole[role]) byRole[role] = []
    byRole[role].push(s)
  }
  const keys = [...order.filter((r) => byRole[r]),
                ...Object.keys(byRole).filter((r) => !order.includes(r))]
  return keys.map((role) => ({
    role,
    label: ROLE_LABELS[role] || role,
    skills: byRole[role],
  }))
})

function promptWords(s) {
  return (s.prompt_append || '').length
}

function toggle(id) {
  const set = new Set(props.modelValue)
  if (set.has(id)) {
    set.delete(id)
  } else if (set.size < props.max) {
    set.add(id)
  }
  emit('update:modelValue', [...set])
}

onMounted(async () => {
  loading.value = true
  try {
    const res = await request.get('/skills')
    skills.value = res.skills || []
  } catch {
    skills.value = []
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.skill-picker-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.skill-picker-title { font-weight: 600; }
.skill-picker-count { color: var(--el-text-color-secondary); font-size: 12px; }
.skill-picker-tip { color: var(--el-text-color-secondary); font-size: 13px; padding: 8px 0; }
.skill-group { margin-bottom: 10px; }
.skill-group-name {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.skill-item {
  display: flex;
  margin-right: 0;
  height: auto;
  padding: 4px 0;
}
.skill-item :deep(.el-checkbox__label) {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.skill-name { font-weight: 500; }
.skill-desc { color: var(--el-text-color-regular); font-size: 12px; }
.skill-words { color: var(--el-text-color-secondary); font-size: 12px; }
.skill-picker-limit {
  color: var(--el-color-warning);
  font-size: 12px;
  margin-top: 4px;
}
</style>
