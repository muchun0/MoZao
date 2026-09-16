<template>
  <div>
    <div class="outline-toolbar">
      <span class="muted">共{{ chapters.length }}章，点击卡片可编辑字段，拖拽可排序</span>
      <el-button size="small" @click="addChapter">+ 添加章</el-button>
      <el-button size="small" type="primary" @click="saveOutline" :loading="saving">保存大纲</el-button>
    </div>

    <draggable v-model="chapters" item-key="no" class="ol-list" :animation="200">
      #item="{ element, index }"
      <el-card class="ol-card">
        <div class="ol-head">
          <el-icon class="drag-icon"><rank /></el-icon>
          <span class="ol-no">第{{ index+1 }}章</span>
          <el-input v-model="element.title" placeholder="章节标题" size="small" style="flex:1" />
          <el-tag v-if="element.tag" size="small">{{ element.tag }}</el-tag>
          <el-button size="small" link type="danger" @click="removeChapter(index)">删</el-button>
        </div>
        <div class="ol-fields">
          <div v-for="f in fields" :key="f.key" class="ol-field">
            <label>{{ f.label }}</label>
            <el-input v-model="element[f.key]" type="textarea" :rows="f.rows" size="small" :placeholder="f.placeholder" />
          </div>
        </div>
      </el-card>
    </draggable>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import draggable from 'vuedraggable'
import { getOutline, saveOutline as saveOutlineApi } from '../../api/novel'
import { Rank } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const props = defineProps({ pid: String })
const emit = defineEmits(['saved'])

const chapters = ref([])
const saving = ref(false)

const fields = [
  { key: 'goal', label: '剧情目标', rows: 2, placeholder: '一句话说清本章发生什么' },
  { key: 'core_event', label: '核心事件', rows: 2, placeholder: '本章主要事件' },
  { key: 'conflict', label: '冲突设计', rows: 2, placeholder: '主要矛盾与升级点' },
  { key: 'character_moves', label: '人物动线', rows: 2, placeholder: '出场人物与行为' },
  { key: 'foreshadowing', label: '伏笔/回收', rows: 1, placeholder: '埋设或回收的伏笔' },
  { key: 'emotion', label: '情绪价值', rows: 1, placeholder: '爽点/虐点/悬念' },
  { key: 'transition', label: '转场', rows: 1, placeholder: '与前后章衔接' },
  { key: 'tag', label: '标签', rows: 1, placeholder: '高潮/过渡/爽点/反转' },
]

onMounted(async () => {
  try {
    const r = await getOutline(props.pid)
    chapters.value = (r.chapters || []).map((c, i) => ({
      no: c.no || i+1,
      title: c.title || '',
      goal: c.goal || '',
      core_event: c.core_event || '',
      conflict: c.conflict || '',
      character_moves: c.character_moves || '',
      foreshadowing: c.foreshadowing || '',
      emotion: c.emotion || '',
      transition: c.transition || '',
      tag: c.tag || '',
    }))
  } catch (e) {
    ElMessage.error('大纲加载失败')
  }
})

function addChapter() {
  chapters.value.push({
    no: chapters.value.length + 1, title: '', goal: '', core_event: '',
    conflict: '', character_moves: '', foreshadowing: '', emotion: '', transition: '', tag: '',
  })
}

function removeChapter(i) {
  chapters.value.splice(i, 1)
}

async function saveOutline() {
  saving.value = true
  try {
    await saveOutlineApi(props.pid, { chapters: chapters.value })
    ElMessage.success('大纲已保存')
    emit('saved')
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally { saving.value = false }
}
</script>

<style scoped>
.outline-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.muted { color: #909399; font-size: 13px; }
.ol-list { display: flex; flex-direction: column; gap: 10px; }
.ol-card { cursor: grab; }
.ol-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.drag-icon { cursor: grab; color: #c0c4cc; }
.ol-no { font-size: 13px; color: #909399; width: 60px; }
.ol-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.ol-field label { display: block; font-size: 12px; color: #909399; margin-bottom: 4px; }
</style>
