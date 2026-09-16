<template>
  <div v-loading="loading">
    <div class="detail-head">
      <div>
        <h2>{{ project?.config?.title || '加载中…' }}</h2>
        <el-progress v-if="generating" :percentage="genPct" :stroke-width="8" style="width:400px;margin-top:8px" :status="genDone?'success':''" />
        <span v-if="generating && !genDone" class="gen-status">{{ genStatus }}</span>
      </div>
      <div>
        <el-button v-if="generating && !genDone" @click="cancelGen">取消</el-button>
        <el-button type="primary" @click="startGen" :disabled="generating && !genDone">
          {{ generating && !genDone ? '生成中…' : '开始生成' }}
        </el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="章节" name="chapters">
        <div v-if="chapters.length" class="ch-list">
          <div v-for="c in chapters" :key="c.no" class="ch-item" @click="openChapter(c)">
            <span class="ch-no">第{{ c.no }}章</span>
            <span class="ch-title">{{ c.title }}</span>
            <el-tag v-if="c.status" :type="statusType(c.status)" size="small">{{ c.status }}</el-tag>
            <el-button size="small" type="warning" link @click.stop="regenChapter(c)" :loading="regenNo === c.no">重新生成</el-button>
          </div>
        </div>
        <el-empty v-else description="暂无章节" />
      </el-tab-pane>

      <el-tab-pane label="大纲" name="outline">
        <OutlineEditor :pid="pid" @saved="loadAll" />
      </el-tab-pane>

      <el-tab-pane label="人物/世界观" name="world">
        <template v-if="world">
          <CharacterGraph v-if="world.characters?.length" :characters="world.characters" />
          <h4>世界观</h4>
          <p class="world-text">{{ world.world_view }}</p>
          <h4>人物档案</h4>
          <el-card v-for="(c,i) in world.characters" :key="i" style="margin-bottom:10px">
            <div class="char-head"><b>{{ c.name }}</b> <el-tag size="small">{{ c.role }}</el-tag></div>
            <div class="char-fields">
              <div v-if="c.appearance" class="char-row"><span class="char-label">外貌</span><span>{{ c.appearance }}</span></div>
              <div v-if="c.personality" class="char-row"><span class="char-label">性格</span><span>{{ c.personality }}</span></div>
              <div v-if="c.desire" class="char-row"><span class="char-label">核心欲望</span><span>{{ c.desire }}</span></div>
              <div v-if="c.wound" class="char-row"><span class="char-label">软肋</span><span>{{ c.wound }}</span></div>
              <div v-if="c.arc" class="char-row"><span class="char-label">弧光</span><span>{{ c.arc }}</span></div>
              <div v-if="c.speech_style" class="char-row"><span class="char-label">说话方式</span><span>{{ c.speech_style }}</span></div>
              <div v-if="c.relations" class="char-row"><span class="char-label">关系</span><span>{{ c.relations }}</span></div>
              <div v-if="c.desc" class="char-row"><span class="char-label">简介</span><span>{{ c.desc }}</span></div>
            </div>
          </el-card>
        </template>
        <el-empty v-else description="暂无人物设定" />
      </el-tab-pane>

      <el-tab-pane label="工具" name="tools">
        <ToolsPanel :pid="pid" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="readerVisible" :title="currentCh?.title" width="800px" top="3vh">
      <template #header>
        <div class="reader-header">
          <span>第{{ currentCh?.no }}章 {{ currentCh?.title }}</span>
          <div class="reader-tools">
            <el-button-group size="small">
              <el-button @click="readerTheme = readerTheme === 'light' ? 'dark' : 'light'" :title="readerTheme === 'light' ? '夜间模式' : '日间模式'">
                {{ readerTheme === 'light' ? '🌙' : '☀️' }}
              </el-button>
              <el-button @click="fontSize = Math.max(14, fontSize - 2)" title="缩小字体">A-</el-button>
              <el-button @click="fontSize = Math.min(24, fontSize + 2)" title="放大字体">A+</el-button>
            </el-button-group>
            <el-button v-if="!editing" size="small" type="primary" @click="startEdit">编辑</el-button>
            <template v-else>
              <el-button size="small" @click="cancelEdit">取消</el-button>
              <el-button size="small" type="success" @click="saveEdit" :loading="saving">保存</el-button>
            </template>
          </div>
        </div>
      </template>
      <div v-if="!editing" class="reader-content" :class="readerTheme" :style="{fontSize: fontSize + 'px'}" v-html="currentContent"></div>
      <el-input v-else v-model="editContent" type="textarea" :rows="22" class="editor-area" />
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { getProject, startGenerate, cancelGenerate, getStatus } from '../api/project'
import { getChapters, getOutline, getWorld, getChapter } from '../api/novel'
import ToolsPanel from '../components/tools/ToolsPanel.vue'
import OutlineEditor from '../components/outline/OutlineEditor.vue'
import CharacterGraph from '../components/world/CharacterGraph.vue'
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
const editing = ref(false)
const editContent = ref('')
const saving = ref(false)
const readerTheme = ref('light')
const fontSize = ref(16)
const generating = ref(false)
const genDone = ref(false)
const genPct = ref(0)
const genStatus = ref('')
const regenNo = ref(0)

async function regenChapter(c) {
  if (!confirm(`确定重新生成第${c.no}章《${c.title}》？当前内容将被覆盖。`)) return
  regenNo.value = c.no
  try {
    const res = await fetch(`/api/projects/${pid}/chapters/${c.no}/rewrite`, { method: 'POST' })
    if (!res.ok) {
      const err = await res.json()
      ElMessage.error(err.detail || '重生成失败')
      return
    }
    ElMessage.success(`第${c.no}章已重新生成（${(await res.json()).words}字）`)
    loadChapters()
  } catch (e) {
    ElMessage.error('重生成失败')
  } finally { regenNo.value = 0 }
}
let pollTimer = null

async function loadAll() {
  loading.value = true
  try {
    project.value = await getProject(pid)
    chapters.value = (await getChapters(pid)).chapters || []
    world.value = await getWorld(pid)
    await checkStatus()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

async function checkStatus() {
  try {
    const s = await getStatus(pid)
    generating.value = s.running
    genDone.value = !s.running && s.chapters_done > 0
    if (s.chapters_total) genPct.value = Math.round(s.chapters_done / s.chapters_total * 100)
    genStatus.value = s.error ? ('错误: ' + s.error) : ''
    if (s.running) startPolling()
  } catch {}
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const s = await getStatus(pid)
      generating.value = s.running
      if (s.chapters_total) genPct.value = Math.round(s.chapters_done / s.chapters_total * 100)
      genStatus.value = s.error ? ('错误: ' + s.error) : ''
      if (!s.running) { genDone.value = true; clearInterval(pollTimer); loadChapters() }
    } catch {}
  }, 3000)
}

async function loadChapters() {
  chapters.value = (await getChapters(pid)).chapters || []
}

async function openChapter(c) {
  currentCh.value = c
  readerVisible.value = true
  editing.value = false
  try {
    const res = await getChapter(pid, c.no)
    editContent.value = res.content || ''
    currentContent.value = editContent.value.replace(/\n/g, '<br>')
  } catch { currentContent.value = '内容加载失败' }
}

function startEdit() {
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}
async function saveEdit() {
  saving.value = true
  try {
    await fetch(`/api/projects/${pid}/chapters/${currentCh.value.no}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: editContent.value })
    })
    currentContent.value = editContent.value.replace(/\n/g, '<br>')
    editing.value = false
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally { saving.value = false }
}

async function startGen() {
  try {
    await startGenerate(pid, {})
    ElMessage.success('已开始生成')
    generating.value = true
    genDone.value = false
    startPolling()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  }
}

async function cancelGen() {
  try {
    await cancelGenerate(pid)
    ElMessage.info('已取消')
    generating.value = false
    if (pollTimer) clearInterval(pollTimer)
  } catch (e) { ElMessage.error('取消失败') }
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'generating') return 'warning'
  return 'info'
}

onMounted(loadAll)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.gen-status { font-size: 13px; color: #909399; margin-left: 12px; }
.ch-list { display: flex; flex-direction: column; gap: 8px; }
.ch-item { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #fff; border-radius: 8px; cursor: pointer; }
.ch-item:hover { background: #f5f7fa; }
.ch-no { color: #909399; font-size: 13px; width: 60px; }
.ch-title { flex: 1; }
.world-text { margin-bottom: 16px; line-height: 1.8; color: #606266; }
.char-head { margin-bottom: 8px; }
.char-fields { display: flex; flex-direction: column; gap: 4px; }
.char-row { display: flex; gap: 8px; font-size: 13px; }
.char-label { flex: 0 0 70px; color: #909399; }
.reader-content { line-height: 1.9; padding: 8px 12px; }
.reader-content.dark { background: #1a1a2e; color: #e0e0e0; padding: 16px; border-radius: 8px; }
.reader-header { display: flex; justify-content: space-between; align-items: center; }
.reader-tools { display: flex; gap: 8px; align-items: center; }
.editor-area :deep(.el-textarea__inner) { font-family: inherit; line-height: 1.8; font-size: 15px; }
</style>
