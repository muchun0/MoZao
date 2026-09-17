<template>
  <div class="tools-grid">
    <!-- 质量校验 -->
    <el-card class="tool-card">
      <template #header><b>质量校验</b></template>
      <p class="muted">AI检查设定一致性/剧情逻辑/合规</p>
      <el-space wrap>
        <el-button size="small" @click="runVerify('consistency', '设定一致性')">设定一致性</el-button>
        <el-button size="small" @click="runVerify('logic', '剧情逻辑')">剧情逻辑</el-button>
        <el-button size="small" @click="runVerify('compliance', '合规检测')">合规检测</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(item, i) in verifyResult" :key="i" :class="item.ok ? 'ok-box' : 'issue-box'">
          {{ item.text }}
        </div>
      </div>
    </el-card>

    <!-- 节奏分析 -->
    <el-card class="tool-card">
      <template #header><b>节奏分析</b></template>
      <p class="muted">每章字数分布与进度</p>
      <el-button size="small" @click="loadRhythm">查看节奏</el-button>
      <div class="result-area">
        <div v-for="(c, i) in rhythmData" :key="i" class="rhythm-row">
          <span class="rhythm-label">第{{ c.no }}章</span>
          <div class="rhythm-bar" :style="{width: (c.words/maxRhythm*100)+'%'}"></div>
          <span class="rhythm-words">{{ c.words }}字</span>
        </div>
      </div>
    </el-card>

    <!-- 多方案生成 -->
    <el-card class="tool-card">
      <template #header><b>多方案生成</b></template>
      <p class="muted">为章节目标生成3个走向</p>
      <el-input v-model="altGoal" placeholder="输入章节目标" />
      <el-button size="small" type="primary" style="margin-top:8px" @click="genAlts" :loading="altLoading">生成方案</el-button>
      <div class="result-area">
        <div v-for="(a, i) in alts" :key="i" class="alt-box">
          <b>方案{{ i+1 }}：{{ a.title }}</b>
          <p>{{ a.description }}</p>
        </div>
      </div>
    </el-card>

    <!-- 批量生成 -->
    <el-card class="tool-card">
      <template #header><b>批量生成</b></template>
      <p class="muted">指定章节范围后台逐章生成</p>
      <el-space>
        <el-input-number v-model="batchStart" :min="1" controls-position="right" style="width:90px" />
        <span>至</span>
        <el-input-number v-model="batchEnd" :min="1" controls-position="right" style="width:90px" />
        <el-button size="small" type="primary" @click="doBatch">开始</el-button>
      </el-space>
    </el-card>

    <!-- 重新生成 -->
    <el-card class="tool-card">
      <template #header><b>重新生成</b></template>
      <p class="muted">对已生成内容不满意可重新生成</p>
      <el-space wrap>
        <el-button size="small" type="warning" @click="regenOutline" :loading="regenLoading">重新生成大纲</el-button>
        <el-button size="small" type="warning" @click="regenWorld" :loading="regenLoading2">重新生成世界观</el-button>
      </el-space>
    </el-card>

    <!-- 大纲快照 -->
    <el-card class="tool-card">
      <template #header><b>大纲快照</b></template>
      <p class="muted">保存/回滚大纲版本</p>
      <el-space>
        <el-input v-model="snapName" placeholder="版本名（选填）" style="width:140px" />
        <el-button size="small" @click="saveSnap">保存</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(s, i) in snaps" :key="i" class="snap-row">
          <span>{{ s.name }} <small class="muted">{{ s.created }}</small></span>
          <el-button size="small" link @click="restoreSnap(s.name)">回滚</el-button>
        </div>
      </div>
    </el-card>

    <!-- 伏笔管理 -->
    <el-card class="tool-card">
      <template #header><b>伏笔管理</b></template>
      <p class="muted">追踪已埋伏笔</p>
      <el-space>
        <el-input v-model="fsInput" placeholder="添加伏笔" style="flex:1" />
        <el-button size="small" @click="addFs">添加</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(f, i) in foreshadowing" :key="i" class="fs-row">
          <span>{{ f }}</span>
          <el-button size="small" link type="danger" @click="delFs(i)">删</el-button>
        </div>
      </div>
    </el-card>

    <!-- 核心设定锁定 -->
    <el-card class="tool-card">
      <template #header><b>核心设定锁定</b></template>
      <p class="muted">AI必须遵守的铁律</p>
      <el-space>
        <el-input v-model="lkInput" placeholder="如：主角不能杀人" style="flex:1" />
        <el-button size="small" @click="addLk">添加</el-button>
      </el-space>
      <div class="result-area">
        <div v-for="(f, i) in locked" :key="i" class="fs-row">
          <span>🔒 {{ f }}</span>
          <el-button size="small" link type="danger" @click="delLk(i)">删</el-button>
        </div>
      </div>
    </el-card>

    <!-- 我的Skills -->
    <el-card class="tool-card">
      <template #header><b>我的Skills</b></template>
      <p class="muted">自定义写作风格，生成时自动注入对应角色</p>
      <el-button size="small" type="primary" @click="showSkillDlg = true">+ 新建Skill</el-button>
      <div class="result-area">
        <div v-for="s in mySkills" :key="s.id" class="fs-row">
          <span>📝 {{ s.name }} <small v-if="s.role" class="muted">({{ s.role }})</small></span>
          <el-button size="small" link type="danger" @click="delMySkill(s.id)">删</el-button>
        </div>
      </div>
    </el-card>

    <!-- 新建Skill弹窗 -->
    <el-dialog v-model="showSkillDlg" title="新建自定义Skill" width="560px" append-to-body>
      <el-form :model="skillForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="skillForm.name" placeholder="如：克制冷峻风格" /></el-form-item>
        <el-form-item label="适用角色">
          <el-select v-model="skillForm.role" placeholder="全部角色" clearable style="width:100%">
            <el-option label="全部角色" value="" />
            <el-option label="调研(Researcher)" value="researcher" />
            <el-option label="架构师(Architect)" value="architect" />
            <el-option label="写手(Writer)" value="writer" />
            <el-option label="审校(Reviewer)" value="reviewer" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="skillForm.description" placeholder="一句话说明这个Skill的作用" /></el-form-item>
        <el-form-item label="提示词内容">
          <el-input v-model="skillForm.prompt_append" type="textarea" :rows="6" placeholder="写给AI的写作要求，如：叙事克制，多用动作细节和感官描写传递情绪，少直接抒情" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSkillDlg = false">取消</el-button>
        <el-button type="primary" @click="saveMySkill">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import * as toolsApi from '../../api/tools'
import { batchGenerate, regenOutline as apiRegenOutline, regenWorld as apiRegenWorld } from '../../api/novel'
import { ElMessage } from 'element-plus'

const props = defineProps({ pid: String })

const verifyResult = ref([])
const rhythmData = ref([])
const maxRhythm = ref(1)
const altGoal = ref('')
const altLoading = ref(false)
const alts = ref([])
const batchStart = ref(1)
const batchEnd = ref(1)
const snapName = ref('')
const snaps = ref([])
const fsInput = ref('')
const foreshadowing = ref([])
const lkInput = ref('')
const locked = ref([])
const regenLoading = ref(false)
const regenLoading2 = ref(false)

async function regenOutline() {
  if (!confirm('重新生成大纲将删除现有大纲和章节，确定？')) return
  regenLoading.value = true
  try {
    await apiRegenOutline(props.pid)
    ElMessage.success('已开始重新生成大纲，请稍候几分钟')
    setTimeout(() => location.reload(), 2000)
  } catch (e) { ElMessage.error('操作失败') } finally { regenLoading.value = false }
}
async function regenWorld() {
  regenLoading2.value = true
  try {
    await apiRegenWorld(props.pid)
    ElMessage.success('已开始重新生成世界观，请稍候')
    setTimeout(() => location.reload(), 2000)
  } catch (e) { ElMessage.error('操作失败') } finally { regenLoading2.value = false }
}

async function runVerify(kind, label) {
  verifyResult.value = [{ text: `正在检查${label}…`, ok: false }]
  try {
    const apiMap = { consistency: toolsApi.verifyConsistency, logic: toolsApi.verifyLogic, compliance: toolsApi.verifyCompliance }
    const r = await apiMap[kind](props.pid)
    const issues = r.issues || r.problems || []
    if (!issues.length) verifyResult.value = [{ text: '未发现问题 ✓', ok: true }]
    else verifyResult.value = issues.map(i => ({ text: (i.problem || i.issue || JSON.stringify(i)) + (i.suggestion ? '（建议：' + i.suggestion + '）' : ''), ok: false }))
  } catch (e) {
    verifyResult.value = [{ text: '失败：' + (e.response?.data?.detail || e.message), ok: false }]
  }
}

async function loadRhythm() {
  try {
    const r = await toolsApi.getRhythm(props.pid)
    rhythmData.value = r.chapters || []
    maxRhythm.value = Math.max(...rhythmData.value.map(c => c.words), 1)
  } catch (e) { ElMessage.error('节奏分析失败') }
}

async function genAlts() {
  if (!altGoal.value) { ElMessage.warning('请输入章节目标'); return }
  altLoading.value = true
  try {
    const r = await toolsApi.genAlternatives(props.pid, { goal: altGoal.value })
    alts.value = r.alternatives || []
  } catch (e) { ElMessage.error('生成失败') } finally { altLoading.value = false }
}

async function doBatch() {
  try {
    await batchGenerate(props.pid, { start: batchStart.value, end: batchEnd.value })
    ElMessage.success(`已开始批量生成第${batchStart.value}-${batchEnd.value}章`)
  } catch (e) { ElMessage.error('批量生成失败') }
}

async function loadSnaps() {
  try { const r = await toolsApi.listSnapshots(props.pid); snaps.value = r.snapshots || [] } catch {}
}
async function saveSnap() {
  await toolsApi.saveSnapshot(props.pid, snapName.value || undefined)
  ElMessage.success('快照已保存')
  snapName.value = ''
  loadSnaps()
}
async function restoreSnap(name) {
  if (!confirm(`回滚到「${name}」？`)) return
  await toolsApi.restoreSnapshot(props.pid, name)
  ElMessage.success('已回滚')
}

async function loadFs() {
  try { const r = await toolsApi.listForeshadowing(props.pid); foreshadowing.value = r.foreshadowing || [] } catch {}
}
async function addFs() {
  if (!fsInput.value) return
  await toolsApi.addForeshadowing(props.pid, { item: fsInput.value })
  fsInput.value = ''
  loadFs()
}
async function delFs(i) {
  await toolsApi.delForeshadowing(props.pid, i)
  loadFs()
}

async function loadLk() {
  try { const r = await toolsApi.listLocked(props.pid); locked.value = r.locked_settings || [] } catch {}
}
async function addLk() {
  if (!lkInput.value) return
  await toolsApi.addLocked(props.pid, { item: lkInput.value })
  lkInput.value = ''
  loadLk()
}
async function delLk(i) {
  await toolsApi.delLocked(props.pid, i)
  loadLk()
}

const mySkills = ref([])
const showSkillDlg = ref(false)
const skillForm = ref({ name: '', role: '', description: '', prompt_append: '' })

async function loadMySkills() {
  try {
    const data = await toolsApi.listMySkills()
    mySkills.value = data.skills || []
  } catch {}
}
async function saveMySkill() {
  if (!skillForm.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  try {
    await toolsApi.saveMySkill(skillForm.value)
    ElMessage.success('Skill已保存')
    showSkillDlg.value = false
    skillForm.value = { name: '', role: '', description: '', prompt_append: '' }
    loadMySkills()
  } catch { ElMessage.error('保存失败') }
}
async function delMySkill(id) {
  if (!confirm('确定删除？')) return
  await toolsApi.delMySkill(id)
  loadMySkills()
}

onMounted(() => { loadSnaps(); loadFs(); loadLk(); loadMySkills() })
</script>

<style scoped>
.tools-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.muted { color: #909399; font-size: 13px; }
.result-area { margin-top: 10px; max-height: 250px; overflow-y: auto; }
.issue-box { padding: 8px 10px; background: #fdf6ec; border-left: 3px solid #e6a23c; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.ok-box { padding: 8px 10px; background: #f0f9eb; border-left: 3px solid #67c23a; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box { padding: 8px 10px; background: #f5f7fa; border: 1px solid #e4e7ed; margin-bottom: 6px; border-radius: 4px; font-size: 13px; }
.alt-box p { margin-top: 4px; color: #606266; }
.snap-row, .fs-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 13px; }
.rhythm-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 12px; }
.rhythm-label { width: 50px; color: #909399; }
.rhythm-bar { height: 12px; background: #c6e2ff; border-radius: 3px; }
.rhythm-words { color: #606266; }
</style>
