<template>
  <div>
    <!-- 总纲 -->
    <el-card class="outline-intro-card">
      <template #header>
        <div class="intro-header">
          <b>总纲</b>
          <el-button size="small" type="primary" @click="saveOutline" :loading="saving">保存</el-button>
        </div>
      </template>
      <el-input v-model="outlineTitle" placeholder="书名/总纲标题" size="small" style="margin-bottom:8px" />
      <el-input v-model="outlineIntro" type="textarea" :rows="3" placeholder="全书核心设定、主线方向、核心冲突" />
    </el-card>

    <div class="outline-toolbar">
      <span class="muted">共{{ volumes.length }}卷 / {{ chapters.length }}章</span>
      <el-button size="small" @click="addVolume">+ 分卷</el-button>
    </div>

    <!-- 分卷区块：每卷下面挂自己的章节 -->
    <div v-for="(vol, vi) in volumes" :key="'vol-'+vol.no+'-'+vi" class="vol-section">
      <el-card class="vol-card">
        <div class="vol-head">
          <span class="vol-label">第{{ vi+1 }}卷</span>
          <el-input v-model="vol.name" placeholder="卷名" size="small" style="width:200px" />
          <el-button size="small" link type="primary" @click="addChapterToVolume(vol.no)">+ 章节</el-button>
          <el-button size="small" link @click="regenVolume(vol)" :loading="regeningVol === vol.no">重生成本卷</el-button>
          <el-button size="small" link type="danger" @click="removeVolume(vi)">删卷</el-button>
        </div>
        <div class="vol-fields">
          <el-input v-model="vol.intro" placeholder="本卷核心主线" size="small" />
          <el-input v-model="vol.main_conflict" placeholder="本卷主要矛盾" size="small" />
          <el-input v-model="vol.climax" placeholder="本卷高潮" size="small" />
        </div>
      </el-card>

      <!-- 该分卷下的章节 -->
      <draggable :model-value="volChapters(vol.no)" item-key="no" class="ol-list" :animation="200" group="chapters"
        @change="(e) => onChapterDrag(e, vol.no)">
        <template #item="{ element, index }">
          <el-card class="ol-card">
            <div class="ol-head">
              <el-icon class="drag-icon"><rank /></el-icon>
              <span class="ol-no">第{{ chapterNo(element) }}章</span>
              <el-input v-model="element.title" placeholder="章节标题" size="small" style="flex:1" />
              <el-tag v-if="element.tag" size="small">{{ element.tag }}</el-tag>
              <el-button size="small" link type="danger" @click="removeChapter(element)">删</el-button>
            </div>
            <div class="ol-fields">
              <div v-for="f in fields" :key="f.key" class="ol-field">
                <label>{{ f.label }}</label>
                <el-input v-model="element[f.key]" type="textarea" :rows="f.rows" size="small" :placeholder="f.placeholder" />
              </div>
            </div>
          </el-card>
        </template>
      </draggable>
    </div>

    <!-- 未分配分卷的章节（兼容旧数据） -->
    <div v-if="unassignedChapters.length" class="vol-section">
      <el-card class="vol-card unassigned">
        <div class="vol-head">
          <span class="vol-label">未分配章节</span>
          <el-button size="small" link type="primary" @click="addChapterToVolume(0)">+ 章节</el-button>
        </div>
      </el-card>
      <draggable :model-value="unassignedChapters" item-key="no" class="ol-list" :animation="200" group="chapters"
        @change="(e) => onChapterDrag(e, 0)">
        <template #item="{ element, index }">
          <el-card class="ol-card">
            <div class="ol-head">
              <el-icon class="drag-icon"><rank /></el-icon>
              <span class="ol-no">第{{ chapterNo(element) }}章</span>
              <el-input v-model="element.title" placeholder="章节标题" size="small" style="flex:1" />
              <el-tag v-if="element.tag" size="small">{{ element.tag }}</el-tag>
              <el-button size="small" link type="danger" @click="removeChapter(element)">删</el-button>
            </div>
            <div class="ol-fields">
              <div v-for="f in fields" :key="f.key" class="ol-field">
                <label>{{ f.label }}</label>
                <el-input v-model="element[f.key]" type="textarea" :rows="f.rows" size="small" :placeholder="f.placeholder" />
              </div>
            </div>
          </el-card>
        </template>
      </draggable>
    </div>

    <!-- 底部添加章节 -->
    <div class="chapters-footer">
      <el-button size="small" type="primary" @click="addChapterToVolume(lastVolumeNo())">+ 添加章节</el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import draggable from 'vuedraggable'
import { getOutline, saveOutline as saveOutlineApi, regenerateVolume } from '../../api/novel'
import { Rank } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps({ pid: String })
const emit = defineEmits(['saved'])

const outlineTitle = ref('')
const outlineIntro = ref('')
const volumes = ref([])
const chapters = ref([])  // 所有章节，每个chapter带volume_no
const saving = ref(false)
const regeningVol = ref(0)
async function regenVolume(vol) {
  try {
    await ElMessageBox.confirm('只重写本卷的简介和章节规划，其他分卷不动。继续？', '重生成本卷', { type: 'warning' })
  } catch { return }
  regeningVol.value = vol.no
  try {
    await regenerateVolume(props.pid, vol.no)
    ElMessage.success('本卷已重新生成，请刷新查看')
    emit('saved')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '重生成失败')
  } finally { regeningVol.value = 0 }
}

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

const unassignedChapters = computed(() => chapters.value.filter(c => !c.volume_no || c.volume_no === 0))

onMounted(async () => {
  try {
    const r = await getOutline(props.pid)
    outlineTitle.value = r.title || ''
    outlineIntro.value = r.intro || ''
    volumes.value = (r.volumes || []).map((v, vi) => ({
      no: v.no || vi+1, name: v.name || '', intro: v.intro || '',
      main_conflict: v.main_conflict || '', character_growth: v.character_growth || '',
      climax: v.climax || '', start_ch: v.start_ch || 0, end_ch: v.end_ch || 0,
    }))
    chapters.value = (r.chapters || []).map((c, i) => ({
      no: c.no || i+1, title: c.title || '', goal: c.goal || '',
      core_event: c.core_event || '', conflict: c.conflict || '',
      character_moves: c.character_moves || '', foreshadowing: c.foreshadowing || '',
      emotion: c.emotion || '', transition: c.transition || '', tag: c.tag || '',
      volume_no: c.volume_no || 0,
    }))
  } catch (e) {
    ElMessage.error('大纲加载失败')
  }
})

function volChapters(vno) {
  return chapters.value.filter(c => c.volume_no === vno)
}

function chapterNo(el) {
  // 返回章节在全局chapters中的序号
  return chapters.value.indexOf(el) + 1
}

function lastVolumeNo() {
  if (volumes.value.length === 0) return 0
  return volumes.value[volumes.value.length - 1].no
}

function addVolume() {
  const newNo = volumes.value.length + 1
  volumes.value.push({
    no: newNo, name: '', intro: '', main_conflict: '',
    character_growth: '', climax: '', start_ch: 0, end_ch: 0,
  })
}

async function removeVolume(vi) {
  try { await ElMessageBox.confirm('删除该分卷？其下章节将变为未分配，点保存后才真正生效。', '确认删除', { type: 'warning' }) } catch { return }
  const vno = volumes.value[vi].no
  chapters.value.forEach(c => { if (c.volume_no === vno) c.volume_no = 0 })
  volumes.value.splice(vi, 1)
  ElMessage.success('已删除分卷，请点保存生效')
}

function addChapterToVolume(vno) {
  chapters.value.push({
    no: chapters.value.length + 1, title: '', goal: '', core_event: '',
    conflict: '', character_moves: '', foreshadowing: '', emotion: '', transition: '', tag: '',
    volume_no: vno,
  })
}

function removeChapter(el) {
  const idx = chapters.value.indexOf(el)
  if (idx >= 0) chapters.value.splice(idx, 1)
}

function onChapterDrag(e, vno) {
  // 元素被拖入当前分卷时，更新它的 volume_no
  if (e.added && e.added.element) {
    e.added.element.volume_no = vno
  }
  // 重新编号章节序号
  chapters.value.forEach((c, i) => { c.no = i + 1 })
}

async function saveOutline() {
  saving.value = true
  try {
    await saveOutlineApi(props.pid, {
      title: outlineTitle.value,
      intro: outlineIntro.value,
      volumes: volumes.value,
      chapters: chapters.value,
    })
    ElMessage.success('大纲已保存')
    emit('saved')
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message))
  } finally { saving.value = false }
}
</script>

<style scoped>
.outline-intro-card { margin-bottom: 16px; }
.intro-header { display: flex; justify-content: space-between; align-items: center; }
.outline-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.muted { color: #909399; font-size: 13px; }
.vol-section { margin-bottom: 14px; }
.vol-card { background: #fafbfc; margin-bottom: 8px; }
.vol-card.unassigned { background: #f5f7fa; }
.vol-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.vol-label { font-size: 14px; font-weight: 600; color: #232f55; }
.vol-fields { display: flex; flex-direction: column; gap: 6px; }
.chapters-footer { display: flex; justify-content: center; margin: 16px 0; padding-top: 12px; border-top: 1px solid #ebeef5; }
.ol-list { display: flex; flex-direction: column; gap: 8px; padding-left: 12px; }
.ol-card { cursor: grab; }
.ol-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.drag-icon { cursor: grab; color: #c0c4cc; }
.ol-no { font-size: 13px; color: #909399; width: 60px; flex-shrink: 0; }
.ol-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.ol-field label { display: block; font-size: 12px; color: #909399; margin-bottom: 4px; }
</style>
