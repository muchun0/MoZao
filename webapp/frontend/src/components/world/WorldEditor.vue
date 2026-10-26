<template>
  <!-- 世界观编辑面板（自 ProjectDetail 拆出，§11.1 文件规模红线）：
       关系图谱 + 世界观文本 + 人物列表 + 选中人物详情 + 事前设计卡。
       锁状态由父组件统一持有（单一权威源），本组件只发 toggle-lock 事件。 -->
  <div v-loading="!loaded">
    <template v-if="world">

      <!-- 顶部操作栏 -->
      <div class="world-edit-head">
        <h4 style="margin:0">世界观设定</h4>
        <div>
          <el-button size="small" type="primary" link @click="$emit('open-versions', 'world')">世界观版本</el-button>
          <el-button size="small" :type="isWorldLocked ? 'success' : 'default'" link @click="$emit('toggle-lock', 'world_view', '', !isWorldLocked)">
            {{ isWorldLocked ? '🔒已锁' : '🔓锁' }}
          </el-button>
          <el-button type="primary" size="small" @click="saveWorldDraft" :loading="worldSaving">保存</el-button>
        </div>
      </div>

      <!-- 人物关系图谱（全宽） -->
      <CharacterGraph v-if="world.characters?.length" :characters="worldDraft.characters" />

      <!-- 左右分栏：左世界观文本，右人物列表 -->
      <div class="world-layout">

        <!-- 左栏：世界观文本 -->
        <div class="world-text-section">
          <div class="section-title">世界设定</div>
          <el-input v-model="worldDraft.world_view" type="textarea" :rows="8" placeholder="描述这个世界的规则、时代背景、核心矛盾、势力格局……" class="world-textarea" />
        </div>

        <!-- 右栏：人物列表（紧凑列表，点击切换选中） -->
        <div class="char-list-section">
          <div class="section-title">
            人物
            <span class="section-count">{{ worldDraft.characters.length }}人</span>
          </div>
          <div class="char-list">
            <div
              v-for="(c, i) in worldDraft.characters"
              :key="i"
              class="char-item"
              :class="{ active: selectedCharIndex === i }"
              @click="selectedCharIndex = i"
            >
              <span class="char-dot" :class="roleClass(c.role)"></span>
              <span class="char-name">{{ c.name || '未命名' }}</span>
              <span class="char-tag">{{ c.role || '' }}</span>
            </div>
          </div>
          <el-button size="small" class="char-add-btn" @click="addCharacter">+ 添加人物</el-button>
        </div>

      </div>

      <!-- 选中人物详情（全宽） -->
      <div v-if="worldDraft.characters[selectedCharIndex]" class="char-detail">
        <div class="detail-head">
          <div class="detail-avatar">{{ (worldDraft.characters[selectedCharIndex].name || '?').charAt(0) }}</div>
          <div class="detail-info">
            <div class="detail-name">{{ worldDraft.characters[selectedCharIndex].name || '未命名' }}</div>
            <div class="detail-sub">{{ worldDraft.characters[selectedCharIndex].role || '角色' }}</div>
          </div>
          <div class="detail-actions">
            <el-button size="small" :type="isCharLocked(worldDraft.characters[selectedCharIndex].name) ? 'success' : 'default'" link
              :aria-label="isCharLocked(worldDraft.characters[selectedCharIndex].name) ? `解锁角色「${worldDraft.characters[selectedCharIndex].name}」` : `锁定角色「${worldDraft.characters[selectedCharIndex].name}」（锁定后重生成不覆盖）`"
              :title="isCharLocked(worldDraft.characters[selectedCharIndex].name) ? '已锁定，点击解锁' : '锁定后重生成不覆盖'"
              @click="$emit('toggle-lock', 'character', worldDraft.characters[selectedCharIndex].name, !isCharLocked(worldDraft.characters[selectedCharIndex].name))">
              {{ isCharLocked(worldDraft.characters[selectedCharIndex].name) ? '🔒' : '🔓' }}
            </el-button>
            <el-button type="danger" size="small" link @click="removeCharacter(selectedCharIndex)">删除</el-button>
          </div>
        </div>
        <div class="detail-fields">
          <div class="field">
            <label class="field-label">姓名</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].name" size="small" placeholder="角色姓名" />
          </div>
          <div class="field">
            <label class="field-label">角色定位</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].role" size="small" placeholder="主角/配角/反派…" />
          </div>
          <div class="field">
            <label class="field-label">外貌</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].appearance" size="small" />
          </div>
          <div class="field">
            <label class="field-label">性格</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].personality" size="small" />
          </div>
          <div class="field">
            <label class="field-label">核心欲望</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].desire" size="small" />
          </div>
          <div class="field">
            <label class="field-label">软肋</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].wound" size="small" />
          </div>
          <div class="field">
            <label class="field-label">弧光</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].arc" size="small" />
          </div>
          <div class="field">
            <label class="field-label">说话方式</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].speech_style" size="small" />
          </div>
          <div class="field field-full">
            <label class="field-label">关系</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].relations" size="small" />
          </div>
          <div class="field field-full">
            <label class="field-label">简介</label>
            <el-input v-model="worldDraft.characters[selectedCharIndex].desc" size="small" />
          </div>
        </div>
      </div>

      <!-- ===== 事前设计卡（折叠收起，默认不展开） ===== -->
      <div class="world-collapse-wrap">
        <el-collapse class="world-collapse">
        <el-collapse-item :title="`生物图鉴（${worldDraft.enemies?.length || 0}）`" name="enemies">
          <div class="world-edit-head">
            <el-button size="small" @click="worldDraft.enemies.push({ name:'', form:'', senses:'', behavior:'', kill:'', tier:'' })">+ 添加怪物</el-button>
          </div>
          <el-card v-for="(e,i) in (worldDraft.enemies||[])" :key="'en'+i" style="margin-bottom:10px">
            <div class="char-head">
              <el-input v-model="e.name" size="small" placeholder="怪物名（如灰卒）" style="width:140px" />
              <el-input v-model="e.tier" size="small" placeholder="层级（浅层/精英/首领）" style="width:180px;margin-left:8px" />
              <el-button type="danger" size="small" link style="margin-left:auto" @click="worldDraft.enemies.splice(i,1)">删除</el-button>
            </div>
            <div class="char-fields">
              <div class="char-row"><span class="char-label">形态解剖</span><el-input v-model="e.form" size="small" placeholder="写死：有无固定形态、肢体、器官、核心位置" /></div>
              <div class="char-row"><span class="char-label">感知方式</span><el-input v-model="e.senses" size="small" placeholder="触须振动/视觉/热感（决定能否写视线盲区）" /></div>
              <div class="char-row"><span class="char-label">行为模式</span><el-input v-model="e.behavior" size="small" placeholder="巡逻/伏击/食腐/对死人反应" /></div>
              <div class="char-row"><span class="char-label">弱点</span><el-input v-model="e.kill" size="small" placeholder="击杀条件/弱点" /></div>
            </div>
          </el-card>
        </el-collapse-item>

        <el-collapse-item :title="`地点卡（${worldDraft.locations?.length || 0}）`" name="locations">
          <div class="world-edit-head">
            <el-button size="small" @click="worldDraft.locations.push({ name:'', terrain:'', light:'', sound_smell:'', exits:'', danger:'' })">+ 添加地点</el-button>
          </div>
          <el-card v-for="(l,i) in (worldDraft.locations||[])" :key="'loc'+i" style="margin-bottom:10px">
            <div class="char-head">
              <el-input v-model="l.name" size="small" placeholder="地点名（如墟内支道）" style="width:160px" />
              <el-button type="danger" size="small" link style="margin-left:auto" @click="worldDraft.locations.splice(i,1)">删除</el-button>
            </div>
            <div class="char-fields">
              <div class="char-row"><span class="char-label">地形结构</span><el-input v-model="l.terrain" size="small" /></div>
              <div class="char-row"><span class="char-label">光线规则</span><el-input v-model="l.light" size="small" placeholder="地下无光/光源/天光在何处（正文不得矛盾）" /></div>
              <div class="char-row"><span class="char-label">声音气味</span><el-input v-model="l.sound_smell" size="small" /></div>
              <div class="char-row"><span class="char-label">出口连通</span><el-input v-model="l.exits" size="small" placeholder="哪里是死路/通哪" /></div>
              <div class="char-row"><span class="char-label">危险来源</span><el-input v-model="l.danger" size="small" /></div>
            </div>
          </el-card>
        </el-collapse-item>

        <el-collapse-item :title="`势力卡（${worldDraft.factions?.length || 0}）`" name="factions">
          <div class="world-edit-head">
            <el-button size="small" @click="worldDraft.factions.push({ name:'', goal:'', members:'', relation_to_mc:'' })">+ 添加势力</el-button>
          </div>
          <el-card v-for="(f,i) in (worldDraft.factions||[])" :key="'fac'+i" style="margin-bottom:10px">
            <div class="char-head">
              <el-input v-model="f.name" size="small" placeholder="势力名" style="width:160px" />
              <el-button type="danger" size="small" link style="margin-left:auto" @click="worldDraft.factions.splice(i,1)">删除</el-button>
            </div>
            <div class="char-fields">
              <div class="char-row"><span class="char-label">目标</span><el-input v-model="f.goal" size="small" /></div>
              <div class="char-row"><span class="char-label">关键成员</span><el-input v-model="f.members" size="small" /></div>
              <div class="char-row"><span class="char-label">与主角关系</span><el-input v-model="f.relation_to_mc" size="small" /></div>
            </div>
          </el-card>
        </el-collapse-item>

        <el-collapse-item title="机制初始值（数字标准答案）" name="mechanism">
          <el-card style="margin-bottom:10px">
            <div class="char-fields">
              <div class="char-row"><span class="char-label">旧痕起点</span><el-input v-model="worldDraft.mechanism_baseline.scar_start" size="small" placeholder="如：手腕" /></div>
              <div class="char-row"><span class="char-label">每死延伸</span><el-input v-model="worldDraft.mechanism_baseline.scar_per_death" size="small" placeholder="如：约一指宽" /></div>
              <div class="char-row"><span class="char-label">旧痕当前</span><el-input v-model="worldDraft.mechanism_baseline.scar_current" size="small" placeholder="如：累计约二十次死亡" /></div>
              <div class="char-row"><span class="char-label">终局位置</span><el-input v-model="worldDraft.mechanism_baseline.scar_end" size="small" placeholder="如：蔓延至心脉=蚀化" /></div>
              <div class="char-row"><span class="char-label">面板格式</span><el-input v-model="worldDraft.mechanism_baseline.panel_format" size="small" placeholder="唯一写法，如：今日可死次数：N/3" /></div>
              <div class="char-row"><span class="char-label">重置规则</span><el-input v-model="worldDraft.mechanism_baseline.reset_rule" size="small" placeholder="如：每日子时重置为3" /></div>
            </div>
          </el-card>
        </el-collapse-item>

        <el-collapse-item title="时钟规则（每章遵守）" name="clock">
          <el-card style="margin-bottom:10px">
            <div class="char-fields">
              <div class="char-row"><span class="char-label">日周期</span><el-input v-model="worldDraft.clock.day_cycle" size="small" placeholder="何时重置/晨昏/夜色节点" /></div>
              <div class="char-row"><span class="char-label">路程</span><el-input v-model="worldDraft.clock.travel" size="small" placeholder="关键地点间耗时，如：墟口到铁匠铺约一刻钟" /></div>
              <div class="char-row"><span class="char-label">光线规则</span><el-input v-model="worldDraft.clock.light_rule" size="small" placeholder="哪里有光哪里没光、何时天色" /></div>
            </div>
          </el-card>
        </el-collapse-item>

        <el-collapse-item :title="`语言禁忌（${worldDraft.style_taboos?.length || 0} 条）`" name="taboos">
          <div class="world-edit-head">
            <el-button size="small" @click="worldDraft.style_taboos.push('')">+ 添加禁用词</el-button>
          </div>
          <el-card style="margin-bottom:10px">
            <div v-for="(t,i) in (worldDraft.style_taboos||[])" :key="'stb'+i" style="display:flex;margin-bottom:6px">
              <el-input v-model="worldDraft.style_taboos[i]" size="small" placeholder="禁用词/表达（如：走位、视野CD、刷新）" style="flex:1" />
              <el-button type="danger" size="small" link @click="worldDraft.style_taboos.splice(i,1)">删除</el-button>
            </div>
          </el-card>
        </el-collapse-item>
      </el-collapse>
      </div>
    </template>
    <el-empty v-else-if="loaded">
      <template #description><span>暂无人物设定。可点下方按钮手动重生成（仅在你确认后执行，不会自动生成）</span></template>
      <el-button type="primary" @click="$emit('regen-world')">重生成世界观</el-button>
    </el-empty>
  </div>
</template>

<script setup>
// 世界观编辑面板：数据自治（自行加载/保存），父组件通过 reload() 在生成完成后触发刷新。
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { getWorld, saveWorld } from '../../api/novel'
import CharacterGraph from './CharacterGraph.vue'

const props = defineProps({
  pid: { type: String, required: true },
  // 锁状态（父组件持有的单一权威源），结构：{ world_view: bool, character: { name: bool } }
  lock: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['toggle-lock', 'open-versions', 'regen-world'])

const loaded = ref(false)
const world = ref(null)
const worldDraft = ref({
  world_view: '', characters: [],
  enemies: [], locations: [], factions: [],
  mechanism_baseline: {}, clock: {}, style_taboos: [],
})
const worldSaving = ref(false)
const selectedCharIndex = ref(0)

const isWorldLocked = computed(() => !!props.lock.world_view)
const isCharLocked = (name) => !!(props.lock.character || {})[name]

function roleClass(role) {
  if (!role) return 'dot-default'
  if (role.includes('主')) return 'dot-mc'
  if (role.includes('反')) return 'dot-foe'
  if (role.includes('配')) return 'dot-ally'
  return 'dot-default'
}

function normalizeWorldDraft() {
  const d = worldDraft.value
  d.enemies = d.enemies || []
  d.locations = d.locations || []
  d.factions = d.factions || []
  d.style_taboos = d.style_taboos || []
  d.mechanism_baseline = d.mechanism_baseline || {}
  d.clock = d.clock || {}
}

async function reload() {
  try {
    world.value = await getWorld(props.pid)
    worldDraft.value = JSON.parse(JSON.stringify(world.value))
    normalizeWorldDraft()
    selectedCharIndex.value = 0
  } catch {
    world.value = null
  } finally {
    loaded.value = true
  }
}

function addCharacter() {
  worldDraft.value.characters.push({ name: '', role: '', desc: '', appearance: '', personality: '', desire: '', wound: '', arc: '', speech_style: '', relations: '' })
  selectedCharIndex.value = worldDraft.value.characters.length - 1
}

function removeCharacter(index) {
  worldDraft.value.characters.splice(index, 1)
  if (selectedCharIndex.value >= worldDraft.value.characters.length) {
    selectedCharIndex.value = Math.max(0, worldDraft.value.characters.length - 1)
  }
}

async function saveWorldDraft() {
  worldSaving.value = true
  try {
    const res = await saveWorld(props.pid, worldDraft.value)
    world.value = res
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    worldSaving.value = false
  }
}

onMounted(reload)
defineExpose({ reload })
</script>

<style scoped>
.world-edit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--ink);
}

/* 左右分栏布局 */
.world-layout {
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 20px;
  align-items: stretch;
  margin-bottom: 20px;
}

/* UX 评审 U6：窄屏单列降级（原两栏在 <900px 会被压得过窄） */
@media (max-width: 900px) {
  .world-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.section-count {
  font-size: 11px;
  color: var(--muted);
  font-weight: normal;
}

/* 世界观文本 */
.world-text-section {
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.world-textarea :deep(.el-textarea__inner) {
  background: var(--paper);
  border-left: 3px solid var(--gold);
  border-radius: 0 4px 4px 0;
  min-height: 180px;
  line-height: 1.8;
}

/* 人物列表 */
.char-list-section {
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  padding: 14px 12px;
  display: flex;
  flex-direction: column;
}
.char-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.char-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  color: var(--ink-soft);
  transition: background 0.15s;
}
.char-item:hover { background: var(--paper); }
.char-item.active {
  background: var(--indigo);
  color: #f0e2b6;
}
.char-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-mc { background: var(--gold); }
.dot-foe { background: #b3402f; }
.dot-ally { background: #6b8e5a; }
.dot-default { background: #999; }
.char-name { flex: 1; }
.char-tag {
  font-size: 10px;
  color: var(--muted);
}
.char-item.active .char-tag {
  color: rgba(240,226,182,0.6);
}
.char-add-btn {
  width: 100%;
  margin-top: 8px;
  border: 1px dashed var(--card-line);
  background: transparent;
  color: var(--muted);
}
.char-add-btn:hover {
  border-color: var(--gold);
  color: var(--gold);
}

/* 选中人物详情 */
.char-detail {
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  margin-bottom: 20px;
}
.detail-head {
  padding: 14px 18px;
  border-bottom: 1px solid var(--card-line);
  display: flex;
  align-items: center;
  gap: 12px;
}
.detail-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: var(--indigo);
  color: #f0e2b6;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
}
.detail-info { flex: 1; }
.detail-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}
.detail-sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 1px;
}
.detail-actions {
  display: flex;
  gap: 4px;
}

.detail-fields {
  padding: 16px 18px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 18px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.field-label {
  font-size: 11px;
  color: var(--muted);
}
.field-full {
  grid-column: 1 / -1;
}

/* 折叠的事前设计卡 */
.world-collapse-wrap {
  background: var(--card);
  border: 1px solid var(--card-line);
  border-radius: var(--radius);
  margin-bottom: 20px;
  overflow: hidden;
}
.world-collapse {
  border-top: none;
}
.world-collapse :deep(.el-collapse-item__header) {
  font-size: 13px;
  font-weight: 500;
  color: var(--ink);
  padding-left: 16px;
  padding-right: 16px;
  border-bottom: 1px solid var(--card-line);
}
.world-collapse :deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: none;
}
.world-collapse :deep(.el-collapse-item__content) {
  padding: 12px 16px;
}

/* 事前设计卡内部通用样式 */
.char-head {
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.char-fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.char-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.char-row .el-input { flex: 1; }
.char-row .char-label {
  width: 70px;
  flex-shrink: 0;
  color: var(--muted);
  font-size: 13px;
}
</style>
