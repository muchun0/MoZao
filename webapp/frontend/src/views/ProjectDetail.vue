<template>
  <div v-loading="loading">
    <div class="detail-head">
      <h2>{{ project?.title || '加载中…' }}</h2>
      <el-button type="primary" @click="startGen">开始生成</el-button>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="章节" name="chapters">
        <div v-if="chapters.length" class="ch-list">
          <div v-for="c in chapters" :key="c.no" class="ch-item" @click="openChapter(c)">
            <span class="ch-no">第{{ c.no }}章</span>
            <span class="ch-title">{{ c.title }}</span>
            <el-tag v-if="c.status" :type="statusType(c.status)" size="small">{{ c.status }}</el-tag>
          </div>
        </div>
        <el-empty v-else description="暂无章节" />
      </el-tab-pane>

      <el-tab-pane label="大纲" name="outline">
        <div v-if="outline" class="outline-view">
          <div v-for="ch in outline.chapters" :key="ch.no" class="ol-item">
            <b>第{{ ch.no }}章 {{ ch.title }}</b>
            <p>{{ ch.goal }}</p>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane label="人物/世界观" name="world">
        <div v-if="world" class="world-view">
          <h4>世界观</h4>
          <p>{{ world.world_view }}</p>
          <h4>人物</h4>
          <el-card v-for="(c,i) in world.characters" :key="i" style="margin-bottom:10px">
            <b>{{ c.name }}</b> <el-tag size="small">{{ c.role }}</el-tag>
            <p style="margin-top:6px;color:#606266">{{ c.desc }}</p>
          </el-card>
        </div>
      </el-tab-pane>

      <el-tab-pane label="工具" name="tools">
        <ToolsPanel :pid="pid" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="readerVisible" :title="currentCh?.title" width="700px">
      <div class="reader-content" v-html="currentContent"></div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getProject, startGenerate } from '../api/project'
import { getChapters, getOutline, getWorld, getChapter } from '../api/novel'
import ToolsPanel from '../components/tools/ToolsPanel.vue'
import { ElMessage } from 'element-plus'

const route = useRoute()
const pid = route.params.id
const loading = ref(false)
const project = ref(null)
const chapters = ref([])
const outline = ref(null)
const world = ref(null)
const tab = ref('chapters')
const readerVisible = ref(false)
const currentCh = ref(null)
const currentContent = ref('')

async function loadAll() {
  loading.value = true
  try {
    project.value = await getProject(pid)
    chapters.value = (await getChapters(pid)).chapters || []
    outline.value = await getOutline(pid)
    world.value = await getWorld(pid)
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

async function openChapter(c) {
  currentCh.value = c
  readerVisible.value = true
  try {
    const res = await getChapter(pid, c.no)
    currentContent.value = (res.content || '').replace(/\n/g, '<br>')
  } catch { currentContent.value = '内容加载失败' }
}

async function startGen() {
  try {
    await startGenerate(pid, {})
    ElMessage.success('已开始生成')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  }
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'generating') return 'warning'
  return 'info'
}

onMounted(loadAll)
</script>

<style scoped>
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.ch-list { display: flex; flex-direction: column; gap: 8px; }
.ch-item { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #fff; border-radius: 8px; cursor: pointer; }
.ch-item:hover { background: #f5f7fa; }
.ch-no { color: #909399; font-size: 13px; width: 60px; }
.ch-title { flex: 1; }
.ol-item { padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.ol-item p { color: #606266; font-size: 13px; margin-top: 4px; }
.reader-content { line-height: 1.8; font-size: 15px; }
</style>
