<template>
  <div>
    <div class="toolbar">
      <el-input v-model="search" placeholder="搜索书名、题材…" clearable style="width:260px" />
      <el-radio-group v-model="filter" size="small">
        <el-radio-button value="">全部</el-radio-button>
        <el-radio-button value="active">进行中</el-radio-button>
        <el-radio-button value="archived">已归档</el-radio-button>
      </el-radio-group>
      <el-select v-model="sort" style="width:140px;margin-left:auto">
        <el-option label="按创建时间" value="created" />
        <el-option label="按更新时间" value="updated" />
        <el-option label="按名称" value="name" />
      </el-select>
      <el-button type="primary" @click="showCreate = true">+ 新建小说</el-button>
    </div>

    <div v-loading="loading" class="grid">
      <el-card v-for="p in filtered" :key="p.id" class="pcard" @click="$router.push('/project/'+p.id)">
        <div class="pcover" :style="coverStyle(p)">
          <span class="pcover-title">{{ p.title }}</span>
          <el-tag v-if="p.archived" class="pcover-badge" size="small" type="info" effect="dark">已归档</el-tag>
        </div>
        <div class="pname">{{ p.title }}</div>
        <div class="pmeta">{{ p.genre || '未设题材' }} · 目标 {{ fmtWords(p.total_words) }}</div>
        <div class="pmeta">已生成 {{ p.chapters_done || 0 }} / {{ p.chapters_total || '—' }} 章</div>
        <el-progress :percentage="pct(p)" :stroke-width="6" style="margin-top:8px" />
        <div class="pactions" @click.stop>
          <el-button size="small" type="primary" plain @click="$router.push('/project/'+p.id)">
            {{ p.archived ? '查看' : '进入' }}
          </el-button>
          <el-button v-if="p.chapters_done && !p.archived" size="small" @click="$router.push('/project/'+p.id+'/write')">继续写</el-button>
          <el-button v-if="!p.archived" size="small" @click="doArchive(p)">归档</el-button>
          <el-button v-else size="small" @click="doArchive(p)">取消归档</el-button>
          <el-button size="small" @click="doClone(p)">克隆</el-button>
          <el-button size="small" type="danger" @click="doDelete(p)">删除</el-button>
        </div>
      </el-card>
      <el-empty v-if="!loading && !filtered.length" :description="list.length ? '当前筛选下没有作品' : '还没有小说，点右上角新建一本'">
        <el-button v-if="!list.length" type="primary" @click="showCreate = true">新建小说</el-button>
        <el-button v-else @click="filter = ''; search = ''">查看全部作品</el-button>
      </el-empty>
    </div>

    <el-dialog v-model="showCreate" title="新建小说" width="780px" class="create-dialog"
               :close-on-click-modal="false" append-to-body>
      <el-form :model="form" label-width="110px">
        <div class="fg-title">基本信息</div>
        <p class="fg-min-hint">带 * 为必填，其余选填。最少填书名 + 一句核心设定即可开始，后续可在项目里补充。</p>
        <el-form-item label="书名" required>
          <el-input ref="titleInputRef" v-model="form.title" placeholder="给你的小说起个名字（必填）" />
        </el-form-item>
        <el-form-item label="题材"><el-input v-model="form.genre" placeholder="如：都市科幻、玄幻、言情（选填）" /></el-form-item>
        <div class="fg-row">
          <el-form-item label="目标字数" style="flex:1"><el-input-number v-model="form.total_words" :min="1000" :max="1000000" :step="1000" /></el-form-item>
          <el-form-item label="每章字数" style="flex:1"><el-input-number v-model="form.chapter_words" :min="500" :max="20000" :step="500" /></el-form-item>
        </div>
        <p class="fg-chap-hint">按当前字数估算，约 {{ estChapters }} 章（创建后可再调整）</p>
        <div class="fg-title">生成模式</div>
        <el-form-item label="模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="auto">AI 从头推进（大纲、世界观、正文都由 AI 生成）</el-radio>
            <el-radio value="expand">AI 扩写（我提供大纲）</el-radio>
          </el-radio-group>
        </el-form-item>
        <p v-if="form.mode === 'auto'" class="fg-note">建书后大纲、世界观会先停下等你看过再继续；也可在项目页切换成自动推进。</p>
        <template v-if="form.mode === 'expand'">
          <div class="fg-sub">更新信息 · 只对新增字数计费</div>
        <el-form-item label="现有字数">
            <el-input-number v-model="form.existing_words" :min="0" :step="10000" />
            <span class="muted" style="margin-left:8px">按差值计费</span>
          </el-form-item>
        <div class="fg-sub">参考资料（可选）</div>
          <el-form-item label="上传大纲">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <input type="file" ref="fileInput" accept=".txt,.md,.docx" @change="onFilePick" />
              <span class="muted" style="font-size:12px">.txt / .md / .docx，≤5MB</span>
              <el-button link type="primary" size="small" @click="showFormat = true">查看大纲格式示例</el-button>
            </div>
            <div v-if="fileMsg" class="file-msg" :class="fileMsg.includes('识别') || fileMsg.includes('解析完成') ? 'file-ok' : 'file-err'">{{ fileMsg }}</div>
          </el-form-item>
          <el-form-item label="大纲内容">
            <el-input v-model="form.expand_input" type="textarea" :rows="6"
              placeholder="粘贴你的大纲（可选）。章节建议用「第X章」标题开头，便于识别章节结构；也可只写设定与主线，之后在项目页由 AI 生成大纲" />
          </el-form-item>
          <el-form-item label="手写内容">
            <el-input v-model="form.user_content" type="textarea" :rows="4" placeholder="粘贴你已写好的正文（可选），AI将在此基础上续写" />
          </el-form-item>
          <el-form-item label="附带题材调研">
            <el-switch v-model="form.with_research" />
            <span class="muted" style="margin-left:8px;font-size:12px">生成前先做一次爆款共性调研：结果沉淀到拆书库，并作为扩写参考（耗时略增、不额外计费）</span>
          </el-form-item>
        </template>

        <div class="fg-title">故事设定</div>
        <el-form-item label="核心设定"><el-input v-model="form.premise" type="textarea" :rows="3" placeholder="一句话讲清故事核心创意（如：社畜穿越到修真界，靠内卷法则证道）" /></el-form-item>
        <el-form-item label="故事背景"><el-input v-model="form.background" type="textarea" :rows="3" placeholder="时代背景 / 世界规则 / 主要地点等（AI 会严格遵守，不会写反）" /></el-form-item>
        <el-form-item label="主角设定"><el-input v-model="form.hero" type="textarea" :rows="2" placeholder="主角身份 / 性格 / 目标 / 特殊优势（如重生记忆、异能）" /></el-form-item>
        <el-form-item label="预设人物">
          <div class="tag-wrap">
            <el-tag v-for="(c, i) in form.characters" :key="i" closable @close="form.characters.splice(i, 1)" style="margin:0 6px 6px 0">{{ c }}</el-tag>
            <el-input v-model="charInput" size="small" style="width:220px" placeholder="输入配角设定，回车添加" @keyup.enter="addCharacter" />
          </div>
          <span class="muted fg-tip">每回车一次添加一个人物；建议「姓名｜身份｜与主角关系｜目标」写清，便于 AI 区分</span>
        </el-form-item>
        <el-form-item label="剧情走向"><el-input v-model="form.plot_direction" type="textarea" :rows="2" placeholder="主线脉络 / 希望出现的桥段 / 关键冲突（参考方向，AI 在此基础上细化）" /></el-form-item>
        <el-form-item label="避雷元素">
          <div class="tag-wrap">
            <el-tag v-for="(f, i) in form.forbidden" :key="i" closable type="danger" @close="form.forbidden.splice(i, 1)" style="margin:0 6px 6px 0">{{ f }}</el-tag>
            <el-input v-model="forbidInput" size="small" style="width:220px" placeholder="输入后回车添加" @keyup.enter="addForbidden" />
          </div>
          <span class="muted fg-tip">不想出现的元素/情节（AI 会严格遵守，如：主角不圣母、拒绝悲剧结局）</span>
        </el-form-item>

        <div class="fg-title">质量选项</div>
        <el-form-item label="上下文回顾">
          <el-input-number v-model="form.memory_window" :min="0" :max="20" />
          <span class="muted fg-tip">写当前章时回顾最近几章：越大越连贯、成本越高</span>
        </el-form-item>
        <div class="muted fg-tip" style="margin:0 0 10px 110px;font-size:12px;line-height:1.7">
          已默认开启：分场景细化写作、逐章自动润色与读者视角检查；去 AI 味、防套路重复、人物描写更立体，单价 3 元/万字
        </div>

        <el-form-item label="费用估算">
          <div class="price-hint">
            <span v-if="form.mode === 'expand'">
              目标 {{ form.total_words }} 字 - 已有 {{ form.existing_words || 0 }} 字 = AI生成约 {{ aiWords }} 字
            </span>
            <span v-else>AI生成约 {{ form.total_words }} 字</span>
            <br>
            单价：3.00 元/万字（高质量）
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="create-foot">
          <div class="create-est">
            <span>预估费用 <b>约 ¥{{ estPrice }}</b>（目标 {{ form.total_words }} 字）</span>
            <span class="muted">创建不扣费，生成后按实际字数结算</span>
          </div>
          <span v-if="insufficient" class="create-balance-warn">余额不足：需要约 ¥{{ estPrice }}，当前 ¥{{ balance.toFixed(2) }}，请先充值</span>
          <div>
            <el-button @click="showCreate=false">取消</el-button>
            <el-button type="primary" :loading="creating" :disabled="insufficient" @click="doCreate">创建</el-button>
          </div>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showFormat" title="大纲格式示例" width="560px">
      <pre class="format-sample">{{ formatSample }}</pre>
      <template #footer>
        <el-button @click="showFormat=false">关闭</el-button>
        <el-button type="primary" @click="downloadFormat">下载示例（.txt）</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { listProjects, createProject, archiveProject, cloneProject, deleteProject, getBalance } from '../api/project'
import { parseContent } from '../api/tools'
import { ElMessage, ElMessageBox } from 'element-plus'

const list = ref([])
const balance = ref(0)
const titleInputRef = ref(null)
const loading = ref(false)
const search = ref('')
const filter = ref('')
const sort = ref('created')
const showCreate = ref(false)
const creating = ref(false)
const showFormat = ref(false)
const fileMsg = ref('')
const charInput = ref('')
const forbidInput = ref('')

const formatSample = [
  '《示例书名》',
  '',
  '一句话简介：主角从都市穿越到修仙世界，凭借前世记忆逆天改命。',
  '',
  '背景设定：架空修仙世界，宗门林立，灵气复苏百年。',
  '',
  '主要人物：',
  '主角：林川，前特种兵，冷静果决，目标是成为最强剑修。',
  '配角：苏晚晴，药谷圣女，医术超绝，与主角互相扶持。',
  '',
  '剧情主线：主角拜入青云宗→秘境夺宝→宗门大比→复仇旧敌。',
  '',
  '第1章 初入青云',
  '主角穿越醒来，发现自己躺在青云宗山门外，被守门弟子当作杂役收留。',
  '',
  '第2章 灵根觉醒',
  '主角在杂役院测试灵根，意外觉醒极品雷灵根，引起长老注意。',
  '',
  '第3章 入门考核',
  '主角参加入门考核，以弱胜强击败内门弟子，正式拜入青云宗。',
].join('\n')

const form = ref({
  title: '', genre: '', total_words: 25000, chapter_words: 2500, premise: '',
  background: '', hero: '', characters: [], plot_direction: '', forbidden: [],
  memory_window: 5,
  mode: 'auto',
  existing_words: 0, user_content: '', expand_input: '', with_research: false,
})
const fileInput = ref(null)

const aiWords = computed(() => {
  const target = form.value.total_words || 0
  const existing = form.value.existing_words || 0
  return Math.max(0, target - existing)
})
const estPrice = computed(() => {
  const rate = 3
  return ((aiWords.value / 10000) * rate).toFixed(2)
})
// 按目标字数 / 每章字数估算章节数，给新手一个直观体量概念（仅提示，不参与计费）
const estChapters = computed(() => {
  const per = Number(form.value.chapter_words) || 0
  const total = Number(form.value.total_words) || 0
  if (!per || !total) return 0
  return Math.max(1, Math.round(total / per))
})
// 建书前就把余额与预估对上，避免填完表单才被后端 400 打回
const insufficient = computed(() => Number(balance.value || 0) < Number(estPrice.value))
async function loadBalance() {
  try {
    const r = await getBalance()
    const u = r?.user || r || {}
    balance.value = Number(u.balance || 0)
  } catch { /* 读取失败不阻塞建书，最终以后端校验为准 */ }
}
function fmtWords(n) {
  if (!n) return '0 字'
  return n >= 10000 ? (n / 10000).toFixed(1).replace(/\.0$/, '') + ' 万字' : n + ' 字'
}
// 无封面的稳定占位：按书名生成两种同系色，保证同一本书每次颜色一致
function coverStyle(p) {
  let h = 0
  for (const ch of String(p.title || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return { background: `linear-gradient(140deg, hsl(${h} 32% 34%), hsl(${(h + 26) % 360} 28% 22%))` }
}

function onFilePick(e) {
  const f = e.target.files[0]
  if (!f) return
  if (f.size > 5 * 1024 * 1024) {
    fileMsg.value = '文件超过 5MB 限制，请精简后上传'
    e.target.value = ''
    return
  }
  const ext = ((f.name.match(/\.([^.]+)$/) || [])[1] || '').toLowerCase()
  if (!['txt', 'md', 'docx'].includes(ext)) {
    fileMsg.value = '仅支持 .txt / .md / .docx 格式'
    e.target.value = ''
    return
  }
  fileMsg.value = '正在解析…'
  const fd = new FormData()
  fd.append('file', f)
  parseContent(fd).then(res => {
    form.value.expand_input = res.full_text || ''
    const n = res.chapters?.length || 0
    fileMsg.value = n ? `已识别 ${n} 个章节，已填入「大纲内容」` : '解析完成，已填入「大纲内容」（未识别到章节标题）'
  }).catch(err => {
    fileMsg.value = err.response?.data?.detail || '解析失败，请检查文件格式'
    e.target.value = ''
  })
}

function downloadFormat() {
  const blob = new Blob([formatSample], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = '大纲格式示例.txt'
  a.click()
  URL.revokeObjectURL(a.href)
}

async function load() {
  loading.value = true
  try {
    const res = await listProjects({ show_archived: true })
    list.value = res.projects || res || []
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

function addCharacter() {
  const v = (charInput.value || '').trim()
  if (!v || form.value.characters.includes(v)) return
  form.value.characters.push(v)
  charInput.value = ''
}

function addForbidden() {
  const v = (forbidInput.value || '').trim()
  if (!v || form.value.forbidden.includes(v)) return
  form.value.forbidden.push(v)
  forbidInput.value = ''
}

const filtered = computed(() => {
  let arr = list.value
  if (filter.value === 'archived') arr = arr.filter(p => p.archived)
  else if (filter.value === 'active') arr = arr.filter(p => !p.archived && !(p.chapters_total && (p.chapters_done || 0) >= p.chapters_total))
  else arr = arr.filter(p => !p.archived)
  if (search.value) {
    const q = search.value.toLowerCase()
    arr = arr.filter(p => p.title?.toLowerCase().includes(q) || p.genre?.toLowerCase().includes(q))
  }
  arr = [...arr]
  if (sort.value === 'name') arr.sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'))
  else if (sort.value === 'updated') arr.sort((a, b) => (b.updated || 0) - (a.updated || 0))
  else arr.sort((a, b) => (b.created || 0) - (a.created || 0))
  return arr
})

// 进度口径统一为章节进度（与详情页底部「X / Y 章」一致），不再按字数算百分比
function pct(p) {
  if (!p.chapters_total) return 0
  return Math.min(100, Math.round((p.chapters_done || 0) / p.chapters_total * 100))
}

async function doCreate() {
  if (!form.value.title.trim()) {
    ElMessage.warning('请输入书名')
    // 定位到书名输入框，避免表单滚在视口外时作者不知道改哪里
    titleInputRef.value?.focus()
    return
  }
  if (!Number(form.value.total_words) || Number(form.value.total_words) < 1000) {
    ElMessage.warning('目标字数至少 1000 字')
    return
  }
  if (!Number(form.value.chapter_words) || Number(form.value.chapter_words) < 500) {
    ElMessage.warning('每章字数至少 500 字，请修改后再创建（不会替你自动改值）')
    return
  }
  if (form.value.expand_input.length > 100000 || form.value.user_content.length > 100000) {
    ElMessage.warning('粘贴内容超过 10 万字上限，请拆分后分次粘贴，或改用文件上传')
    return
  }
  creating.value = true
  try {
    const res = await createProject(form.value)
    ElMessage.success('创建成功')
    if (form.value.mode === 'expand' && res?.outline_imported === false) {
      ElMessage.warning('已按你的大纲创建项目，但未识别到章节结构：可进入项目「大纲」页补充格式后重新导入，或点「生成大纲与世界观」由 AI 生成')
    }
    showCreate.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  } finally { creating.value = false }
}

async function doArchive(p) {
  if (!p.archived) {
    try {
      await ElMessageBox.confirm(
        `归档《${p.title}》？归档后从「全部」移入「已归档」，正文与数据都保留，可随时取消归档。`,
        '归档作品', { type: 'warning', confirmButtonText: '归档' })
    } catch { return }
  }
  const res = await archiveProject(p.id)
  ElMessage.success(res?.archived ? '已归档，可在「已归档」里找回' : '已取消归档')
  load()
}
async function doClone(p) {
  await cloneProject(p.id)
  ElMessage.success('已克隆')
  load()
}
async function doDelete(p) {
  const chapters = p.chapters_done || 0
  await ElMessageBox.confirm(
    `删除《${p.title}》？将同时删除 ${chapters} 章正文、版本历史与导出产物，且不可恢复。` +
    (p.archived ? '（当前为已归档状态）' : ''),
    '删除作品', { type: 'error', confirmButtonText: '永久删除', cancelButtonText: '取消' })
  await deleteProject(p.id)
  ElMessage.success('已删除')
  load()
}

onMounted(() => { load(); loadBalance() })
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.grid :deep(.el-empty) { grid-column: 1 / -1; width: 100%; }
.pcard { cursor: pointer; transition: box-shadow .2s; }
.pcard:hover { box-shadow: 0 4px 12px rgba(0,0,0,.1); }
/* 无封面用书封式占位：书名 + 稳定配色，卡片有视觉入口 */
.pcover {
  position: relative; height: 104px; border-radius: 8px; margin-bottom: 12px;
  display: flex; align-items: center; justify-content: center; padding: 10px;
  overflow: hidden;
}
.pcover-title {
  color: #f5efdc; font-family: var(--serif); font-size: 17px; letter-spacing: 2px;
  text-align: center; line-height: 1.35; display: -webkit-box; -webkit-line-clamp: 3;
  -webkit-box-orient: vertical; overflow: hidden;
}
.pcover-badge { position: absolute; top: 8px; right: 8px; }
.pname { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
.pmeta { font-size: 13px; color: #909399; }
.pactions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.create-foot { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.create-est { display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: #303133; text-align: left; }
.create-est b { color: #c9a227; }
.create-est .muted { font-size: 12px; }
.create-balance-warn { font-size: 12px; color: #f56c6c; }
.fg-note { font-size: 12px; color: #909399; margin: -2px 0 8px 110px; line-height: 1.6; }
.fg-min-hint { font-size: 12px; color: #606266; margin: 0 0 10px 8px; line-height: 1.6; }
.fg-chap-hint { font-size: 12px; color: #909399; margin: -4px 0 8px 110px; }
/* 建书表单长：头部与底部固定，中间独立滚动，滚到费用区时仍能看到标题/关闭 */
:deep(.create-dialog .el-dialog__body) { max-height: 66vh; overflow-y: auto; }
.price-hint { background: #f6f8fa; padding: 8px 12px; border-radius: 6px; font-size: 13px; line-height: 1.8; color: #606266; }
.price-hint b { color: #c9a227; }
.price-hint .muted { color: #909399; font-size: 12px; }
.file-msg { font-size: 12px; line-height: 1.6; margin-top: 4px; }
.file-ok { color: #67c23a; }
.file-err { color: #f56c6c; }
.format-sample { background: #f6f8fa; border: 1px solid #e4e7ed; border-radius: 6px; padding: 12px 16px; font-size: 13px; line-height: 1.7; color: #303133; white-space: pre-wrap; max-height: 420px; overflow: auto; }
.fg-title { font-size: 14px; font-weight: 600; color: #303133; margin: 18px 0 10px; padding-left: 8px; border-left: 3px solid #c9a227; line-height: 1.2; }
.fg-title:first-of-type { margin-top: 0; }
.fg-sub { font-size: 12px; color: #909399; margin: -2px 0 12px; padding-left: 8px; }
.fg-row { display: flex; gap: 16px; }
.fg-tip { margin-left: 10px; font-size: 12px; }
.tag-wrap { display: flex; align-items: center; flex-wrap: wrap; width: 100%; }
</style>
