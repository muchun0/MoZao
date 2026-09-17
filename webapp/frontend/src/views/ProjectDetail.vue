<template>
  <div v-loading="loading">
    <div class="detail-head">
      <div>
        <h2>{{ project?.config?.title || '加载中…' }}</h2>
        <el-progress v-if="generating" :percentage="genPct" :stroke-width="8" style="width:400px;margin-top:8px" :status="genDone?'success':''" />
        <span v-if="generating && !genDone" class="gen-status">{{ genStatus }}</span>
      </div>
      <div class="step-actions">
        <el-button v-if="generating && !genDone" @click="cancelGen">取消</el-button>
        <el-button v-if="!outlineReady" type="primary" @click="runOutline" :loading="generating && !genDone">
          {{ generating && !genDone ? '生成中…' : '生成大纲与世界观' }}
        </el-button>
        <template v-else>
          <el-button @click="doRegenOutline" :disabled="generating && !genDone" title="重生成总纲/分卷">重生成大纲</el-button>
          <el-button @click="doRegenWorld" :disabled="generating && !genDone" title="重生成世界观/人物">重生成世界观</el-button>
          <el-button type="primary" @click="startGen" :disabled="generating && !genDone">
            {{ generating && !genDone ? '生成中…' : '开始写章节' }}
          </el-button>
        </template>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="章节" name="chapters">
        <div v-if="chapters.length" class="ch-list">
          <div v-for="g in groupedChapters" :key="g.key" class="ch-group">
            <div class="ch-group-title">{{ g.name }}</div>
            <div v-for="c in g.list" :key="c.no" class="ch-item" @click="openChapter(c)">
              <span class="ch-no">第{{ c.no }}章</span>
              <span class="ch-title">{{ c.title }}</span>
              <el-tag v-if="c.status" :type="statusType(c.status)" size="small">{{ c.status }}</el-tag>
              <el-button size="small" type="warning" link @click.stop="regenChapter(c)" :loading="regenNo === c.no">重新生成</el-button>
              <el-button size="small" :type="isChLocked(c.no) ? 'success' : 'default'" link @click.stop="toggleLock('chapter', c.no)">
                {{ isChLocked(c.no) ? '🔒已锁' : '🔓锁' }}
              </el-button>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无章节" />
      </el-tab-pane>

      <el-tab-pane label="大纲" name="outline">
        <OutlineEditor :pid="pid" @saved="loadAll" />
      </el-tab-pane>

      <el-tab-pane label="人物/世界观" name="world">
        <template v-if="world">
          <CharacterGraph v-if="world.characters?.length" :characters="worldDraft.characters" />
          <div class="world-edit-head">
            <h4 style="margin:0">世界观</h4>
            <div>
              <el-button size="small" :type="isWorldLocked ? 'success' : 'default'" link @click="toggleLock('world_view', '', !isWorldLocked)">
                {{ isWorldLocked ? '🔒已锁' : '🔓锁' }}
              </el-button>
              <el-button type="primary" size="small" @click="saveWorldDraft" :loading="worldSaving">保存</el-button>
            </div>
          </div>
          <el-input v-model="worldDraft.world_view" type="textarea" :rows="6" placeholder="描述这个世界的规则、时代背景、核心矛盾、势力格局……" />
          <div class="world-edit-head" style="margin-top:16px">
            <h4 style="margin:0">人物档案（{{ worldDraft.characters.length }}）</h4>
            <el-button size="small" @click="addCharacter">+ 添加人物</el-button>
          </div>
          <el-card v-for="(c,i) in worldDraft.characters" :key="i" style="margin-bottom:10px">
            <div class="char-head">
              <el-input v-model="c.name" size="small" placeholder="姓名" style="width:140px" />
              <el-input v-model="c.role" size="small" placeholder="角色（主角/配角/反派…）" style="width:180px;margin-left:8px" />
              <el-button size="small" :type="isCharLocked(c.name) ? 'success' : 'default'" link @click="toggleLock('character', c.name, !isCharLocked(c.name))">{{ isCharLocked(c.name) ? '🔒' : '🔓' }}</el-button>
              <el-button type="danger" size="small" link style="margin-left:auto" @click="worldDraft.characters.splice(i,1)">删除</el-button>
            </div>
            <div class="char-fields">
              <div class="char-row"><span class="char-label">外貌</span><el-input v-model="c.appearance" size="small" /></div>
              <div class="char-row"><span class="char-label">性格</span><el-input v-model="c.personality" size="small" /></div>
              <div class="char-row"><span class="char-label">核心欲望</span><el-input v-model="c.desire" size="small" /></div>
              <div class="char-row"><span class="char-label">软肋</span><el-input v-model="c.wound" size="small" /></div>
              <div class="char-row"><span class="char-label">弧光</span><el-input v-model="c.arc" size="small" /></div>
              <div class="char-row"><span class="char-label">说话方式</span><el-input v-model="c.speech_style" size="small" /></div>
              <div class="char-row"><span class="char-label">关系</span><el-input v-model="c.relations" size="small" /></div>
              <div class="char-row"><span class="char-label">简介</span><el-input v-model="c.desc" size="small" /></div>
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
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { getProject, startGenerate, cancelGenerate, getStatus } from '../api/project'
import { getChapters, getOutline, getWorld, getChapter, saveWorld, getLock, setLock, genOutline, regenOutline, regenWorld, rewriteChapter } from '../api/novel'
import ToolsPanel from '../components/tools/ToolsPanel.vue'
import OutlineEditor from '../components/outline/OutlineEditor.vue'
import CharacterGraph from '../components/world/CharacterGraph.vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const pid = route.params.id
const loading = ref(false)
const project = ref(null)
const chapters = ref([])
const outline = ref(null)
const world = ref(null)
const worldDraft = ref({ world_view: "", characters: [] })
const worldSaving = ref(false)
const lock = ref({})
const outlineReady = computed(() => !!(outline.value && outline.value.chapters && outline.value.chapters.length))
const isWorldLocked = computed(() => !!lock.value.world_view)
const isChLocked = (no) => !!(lock.value.chapter || {})[no]
const isCharLocked = (name) => !!(lock.value.character || {})[name]
async function toggleLock(kind, key="", forced) {
  try {
    const locked = forced !== undefined ? forced : !((lock.value[kind]||{})[key])
    await setLock(pid, { kind, key: String(key), locked })
    lock.value = await getLock(pid)
    ElMessage.success(locked ? "已锁定，重生成时不会覆盖" : "已解锁")
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || "操作失败")
  }
}
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

// 按分卷分组的章节菜单：以 outline.volumes 为骨架，已生成章节按 no 合并状态
const groupedChapters = computed(() => {
  const vols = (outline.value && outline.value.volumes) || []
  const out = []
  const chMap = {}
  ;(chapters.value || []).forEach(c => { chMap[c.no] = c })
  const olChs = (outline.value && outline.value.chapters) || []
  vols.forEach(v => {
    const list = olChs.filter(c => c.volume_no === v.no).map(c => ({ ...chMap[c.no], ...c }))
    if (list.length) out.push({ key: 'vol-' + v.no, name: v.name || ('第' + v.no + '卷'), list })
  })
  const unassigned = olChs.filter(c => !c.volume_no || c.volume_no === 0).map(c => ({ ...chMap[c.no], ...c }))
  if (unassigned.length) out.push({ key: 'vol-0', name: '未分卷', list: unassigned })
  // 兜底：outline 为空时直接用 chapters
  if (!olChs.length && chapters.value.length) {
    out.push({ key: 'all', name: '全部章节', list: chapters.value })
  }
  return out
})

async function regenChapter(c) {
  if (!confirm(`确定重新生成第${c.no}章《${c.title}》？当前内容将被覆盖。`)) return
  regenNo.value = c.no
  try {
    const res = await rewriteChapter(pid, c.no)
    ElMessage.success(`第${c.no}章已重新生成（${res.words}字）`)
    loadChapters()
  } catch (e) {
    ElMessage.error('重生成失败')
  } finally { regenNo.value = 0 }
}
function addCharacter() {
  worldDraft.value.characters.push({ name: "", role: "", desc: "", appearance: "", personality: "", desire: "", wound: "", arc: "", speech_style: "", relations: "" })
}
async function saveWorldDraft() {
  worldSaving.value = true
  try {
    const res = await saveWorld(pid, worldDraft.value)
    world.value = res
    ElMessage.success("已保存")
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || "保存失败")
  } finally { worldSaving.value = false }
}
let pollTimer = null

async function loadAll() {
  loading.value = true
  try {
    project.value = await getProject(pid)
    outline.value = await getOutline(pid)
    chapters.value = (await getChapters(pid)).chapters || []
    world.value = await getWorld(pid)
    worldDraft.value = JSON.parse(JSON.stringify(world.value))
    try { lock.value = await getLock(pid) } catch { lock.value = {} }
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

async function runOutline() {
  try { await genOutline(pid); ElMessage.success('已开始生成大纲与世界观，完成后可在「大纲」「人物/世界观」查看/编辑'); startPolling() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '启动失败') }
}
async function doRegenOutline() {
  try { await ElMessageBox.confirm('重生成大纲会覆盖现有总纲/分卷，已锁定内容不受影响。继续？', '重生成大纲', { type: 'warning' }) } catch { return }
  try { await regenOutline(pid); ElMessage.success('已开始重生成大纲'); startPolling() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '失败') }
}
async function doRegenWorld() {
  try { await ElMessageBox.confirm('重生成世界观会覆盖现有世界设定，已锁定内容不受影响。继续？', '重生成世界观', { type: 'warning' }) } catch { return }
  try { await regenWorld(pid); ElMessage.success('已开始重生成世界观'); startPolling() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '失败') }
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
.step-actions { display:flex; gap:8px; align-items:center; }
.gen-status { font-size: 13px; color: #909399; margin-left: 12px; }
.ch-list { display: flex; flex-direction: column; gap: 8px; }
.ch-group-title { font-weight: 600; font-size: 14px; color: #1f3a5f; margin: 10px 2px 4px; padding-left: 2px; border-left: 3px solid #c9a961; }
.ch-item { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #fff; border-radius: 8px; cursor: pointer; }
.ch-item:hover { background: #f5f7fa; }
.ch-no { color: #909399; font-size: 13px; width: 60px; }
.ch-title { flex: 1; }
.world-text { margin-bottom: 16px; line-height: 1.8; color: #606266; }
.world-edit-head { display: flex; align-items: center; justify-content: space-between; margin: 12px 0 8px; }
.char-head { margin-bottom: 8px; display: flex; align-items: center; flex-wrap: wrap; }
.char-row .el-input { flex: 1; }
.char-row .char-label { width: 70px; flex-shrink: 0; color: #909399; font-size: 13px; }
.char-fields { display: flex; flex-direction: column; gap: 6px; }
.char-row { display: flex; align-items: center; gap: 8px; }
.char-row { display: flex; gap: 8px; font-size: 13px; }
.char-label { flex: 0 0 70px; color: #909399; }
.reader-content { line-height: 1.9; padding: 8px 12px; }
.reader-content.dark { background: #1a1a2e; color: #e0e0e0; padding: 16px; border-radius: 8px; }
.reader-header { display: flex; justify-content: space-between; align-items: center; }
.reader-tools { display: flex; gap: 8px; align-items: center; }
.editor-area :deep(.el-textarea__inner) { font-family: inherit; line-height: 1.8; font-size: 15px; }
</style>
