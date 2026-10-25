<template>
  <div v-loading="loading">
    <div class="detail-head">
      <div>
        <h2>{{ project?.config?.title || '加载中…' }}</h2>
        <el-progress v-if="generating" :percentage="genPct" :stroke-width="8" style="width:400px;margin-top:8px"
          :status="genDone ? 'success' : ''" :indeterminate="!genDone && inPrePhase" :duration="2" />
        <div v-if="genError" class="gen-error-wrap">
          <span class="gen-error">{{ genError }}</span>
          <el-button v-if="resumable && !generating" type="primary" size="small" @click="startGen">继续生成</el-button>
        </div>
        <span v-else-if="generating && !genDone && genStatus" class="gen-status">{{ genStatus }}</span>
      </div>
      <div class="step-actions">
        <el-button v-if="generating && !genDone" @click="cancelGen">取消</el-button>
        <el-button v-if="!outlineReady && !hadOutline" type="primary" @click="runOutline" :loading="generating && !genDone">
          {{ generating && !genDone ? '生成中…' : '生成大纲与世界观' }}
        </el-button>
        <template v-else>
          <el-button @click="doRegenOutline" :disabled="generating && !genDone" title="重生成总纲/分卷">重生成大纲</el-button>
          <el-button @click="doRegenWorld" :disabled="generating && !genDone" title="重生成世界观/人物">重生成世界观</el-button>
          <el-button @click="openImport" :disabled="generating && !genDone" title="重新导入你写的大纲（覆盖前自动备份）">重新导入大纲</el-button>
          <el-dropdown trigger="click" @command="onSnapCmd">
            <el-button :disabled="generating && !genDone">大纲快照 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="!snapshots.length" disabled>暂无快照</el-dropdown-item>
                <el-dropdown-item v-for="sn in snapshots" :key="sn.name" :command="'restore:' + sn.name">
                  还原「{{ sn.display || sn.name }}」（{{ sn.chapters }} 章）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>          <el-button type="primary" @click="genPhase === 'done' ? regenAllChapters() : startGen()" :disabled="generating && !genDone">
            {{ generating && !genDone ? '生成中…' : (genPhase === 'done' ? '重新生成' : (genPhase === 'partial' ? '继续生成' : '开始生成')) }}
          </el-button>
          <el-dropdown v-if="chapters.length" trigger="click" @command="doDownload">
            <el-button :disabled="generating && !genDone" :loading="exporting">导出 ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="txt">TXT 文本</el-dropdown-item>
                <el-dropdown-item command="epub">EPUB 电子书</el-dropdown-item>
                <el-dropdown-item command="docx">DOCX Word</el-dropdown-item>
                <el-dropdown-item command="md">Markdown</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
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
              <el-tag v-if="c.status" :type="statusType(c.status)" size="small">{{ statusText(c.status) }}</el-tag>
              <el-button size="small" type="warning" link @click.stop="regenChapter(c)" :loading="regenNo === c.no">重新生成</el-button>
              <el-button size="small" type="primary" link @click.stop="openVersions(c)">版本</el-button>
              <el-button size="small" :type="isChLocked(c.no) ? 'success' : 'default'" link @click.stop="toggleLock('chapter', c.no)">
                {{ isChLocked(c.no) ? '🔒已锁' : '🔓锁' }}
              </el-button>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无章节" />
        <el-dialog v-model="versionsDialog" :title="`章节版本 · 第${versionsNo}章`" width="560px">
          <div class="ver-list">
            <div v-for="v in versionList" :key="v.v" class="ver-item" :class="{ active: v.selected }">
              <div class="ver-main">
                <div class="ver-info">
                  <span class="ver-no">v{{ v.v }}</span>
                  <el-tag size="small" :type="v.selected ? 'success' : 'info'">{{ v.selected ? '当前' : sourceText(v.source) }}</el-tag>
                  <el-tag v-if="v.quality && v.quality.score !== undefined && v.quality.score !== null"
                          size="small" :type="v.quality.passed ? 'success' : 'danger'">
                    质检 {{ v.quality.score }}{{ v.quality.passed ? ' ✓' : ' ✗' }}
                  </el-tag>
                  <el-tag v-if="v.quality && v.quality.auto_revised" size="small" type="warning">自动改</el-tag>
                  <span class="ver-words">{{ v.words }}字</span>
                  <span class="ver-ts">{{ fmtVerTime(v.ts) }}</span>
                </div>
                <div v-if="v.quality && (v.quality.issues || []).length" class="ver-issues">
                  <div class="ver-issues-head">改稿建议（{{ v.quality.issue_count || v.quality.issues.length }} 处）</div>
                  <div v-for="(it, i) in v.quality.issues.slice(0, 8)" :key="i" class="ver-issue">
                    <span :class="it.level === '大改' ? 'lv-big' : (it.level === '中改' ? 'lv-mid' : 'lv-small')">{{ it.level || '小改' }}</span>
                    <span v-if="it.type" class="ver-issue-type">[{{ it.type }}]</span>
                    <span>{{ it.detail }}</span>
                    <el-button size="small" link type="warning" @click="appealIssue(it)">申诉</el-button>
                  </div>
                  <div v-if="(v.quality.issues || []).length > 8" class="ver-issue-more">… 其余 {{ v.quality.issues.length - 8 }} 条见版本质检记录</div>
                </div>
                <div v-else class="ver-noq">该版本无质检问题记录</div>
              </div>
              <div class="ver-actions">
                <el-button size="small" link :type="v.selected ? '' : 'primary'" :disabled="v.selected"
                           @click="selectVersion(v.v)">{{ v.selected ? '使用中' : '选择/回退' }}</el-button>
                <el-button v-if="!v.selected" size="small" link type="danger" @click="deleteVersion(v.v)">删除</el-button>
              </div>
            </div>
            <el-empty v-if="!versionList.length" description="暂无历史版本（重写/重生成会自动保留多版本）" />
          </div>
          <div class="ver-tip">每次重写/重生成都会追加新版本、保留旧版；首次生成默认展示质检评分最高的版本，手动选择/编辑后以您的操作为准。切换版本后如需与后续章节衔接，建议重新生成后续章节以同步记忆账本。重写/重生成按本次 AI 实际生成字数计费，候选版本同样计费；手动编辑不计费。</div>
        </el-dialog>
      </el-tab-pane>

      <el-tab-pane label="大纲" name="outline">
        <OutlineEditor :key="outlineReloadKey" :pid="pid" @saved="loadAll" />
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

          <!-- ===== 事前设计卡（REQ-事前设定卡） ===== -->
          <el-divider content-position="left">事前设计卡（防设定矛盾，正文必须遵守）</el-divider>

          <!-- 生物图鉴 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">生物图鉴（{{ worldDraft.enemies?.length || 0 }}）</h4>
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

          <!-- 地点卡 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">地点卡（{{ worldDraft.locations?.length || 0 }}）</h4>
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

          <!-- 势力卡 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">势力卡（{{ worldDraft.factions?.length || 0 }}）</h4>
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

          <!-- 机制初始值 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">机制初始值（数字标准答案）</h4>
          </div>
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

          <!-- 时钟规则 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">时钟规则（每章遵守）</h4>
          </div>
          <el-card style="margin-bottom:10px">
            <div class="char-fields">
              <div class="char-row"><span class="char-label">日周期</span><el-input v-model="worldDraft.clock.day_cycle" size="small" placeholder="何时重置/晨昏/夜色节点" /></div>
              <div class="char-row"><span class="char-label">路程</span><el-input v-model="worldDraft.clock.travel" size="small" placeholder="关键地点间耗时，如：墟口到铁匠铺约一刻钟" /></div>
              <div class="char-row"><span class="char-label">光线规则</span><el-input v-model="worldDraft.clock.light_rule" size="small" placeholder="哪里有光哪里没光、何时天色" /></div>
            </div>
          </el-card>

          <!-- 语言禁忌 -->
          <div class="world-edit-head" style="margin-top:8px">
            <h4 style="margin:0">语言禁忌（{{ worldDraft.style_taboos?.length || 0 }} 条）</h4>
            <el-button size="small" @click="worldDraft.style_taboos.push('')">+ 添加禁用词</el-button>
          </div>
          <el-card style="margin-bottom:10px">
            <div v-for="(t,i) in (worldDraft.style_taboos||[])" :key="'stb'+i" style="display:flex;margin-bottom:6px">
              <el-input v-model="worldDraft.style_taboos[i]" size="small" placeholder="禁用词/表达（如：走位、视野CD、刷新）" style="flex:1" />
              <el-button type="danger" size="small" link @click="worldDraft.style_taboos.splice(i,1)">删除</el-button>
            </div>
          </el-card>
        </template>
        <el-empty v-else>
          <template #description><span>暂无人物设定。可点下方按钮手动重生成（仅在你确认后执行，不会自动生成）</span></template>
          <el-button type="primary" @click="doRegenWorld" :disabled="generating && !genDone">重生成世界观</el-button>
        </el-empty>
      </el-tab-pane>

      <el-tab-pane label="小墨AI" name="assistant">
        <AssistantPanel :project-id="pid" :chapters="chapters" :current-ch="currentCh"
                        @locate="locateChapter" />
      </el-tab-pane>

      <el-tab-pane label="工具" name="tools">
        <ToolsPanel :pid="pid" />
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="readerVisible" :title="currentCh?.title" width="800px" top="3vh" @closed="hideSelFloat">
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
      <div v-if="!editing" class="reader-content" :class="readerTheme" :style="{fontSize: fontSize + 'px'}" v-html="currentContent" @mouseup="onReaderMouseup"></div>
      <el-input v-else v-model="editContent" type="textarea" :rows="22" class="editor-area" @mouseup="onEditMouseup" />
    </el-dialog>

    <teleport to="body">
      <div v-show="selFloat.show" class="sel-float" :style="{ left: selFloat.x + 'px', top: selFloat.y + 'px' }">
        <span class="sel-float-item" @click="doExpand('expand')">扩写</span>
        <span class="sel-float-sep">|</span>
        <span class="sel-float-item" @click="doExpand('continue')">续写</span>
      </div>
    </teleport>

    <el-dialog v-model="importVisible" title="重新导入大纲" width="560px">
      <p class="import-hint">将用你的新内容覆盖当前大纲（覆盖前自动备份上一版，可在「大纲快照」中一键还原）。AI 不会改写你的内容。</p>
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <input type="file" ref="importFileInput" accept=".txt,.md,.docx" @change="onImportFilePick" />
        <span class="muted" style="font-size:12px">.txt / .md / .docx，≤5MB</span>
      </div>
      <div v-if="importFileMsg" class="file-msg" :class="importFileMsg.includes('识别') || importFileMsg.includes('解析完成') ? 'file-ok' : 'file-err'">{{ importFileMsg }}</div>
      <el-input v-model="importText" type="textarea" :rows="8" placeholder="或直接粘贴大纲内容（章节建议用「第X章」标题开头）" style="margin-top:8px" />
      <template #footer>
        <el-button @click="importVisible=false">取消</el-button>
        <el-button type="primary" :loading="reimporting" @click="doImport">导入</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, h } from 'vue'
import { useRoute } from 'vue-router'
import { getProject, startGenerate, regenerateChapters, cancelGenerate, getStatus, downloadProject } from '../api/project'
import { getChapters, getOutline, getWorld, getChapter, updateChapterContent, saveWorld, getLock, setLock, genOutline, regenOutline, regenWorld, rewriteChapter, getChapterVersions, selectChapterVersion, deleteChapterVersion, importOutline, expandSelection, appealChapterIssue } from '../api/novel'
import { parseContent, listSnapshots, restoreSnapshot } from '../api/tools'
import ToolsPanel from '../components/tools/ToolsPanel.vue'
import OutlineEditor from '../components/outline/OutlineEditor.vue'
import CharacterGraph from '../components/world/CharacterGraph.vue'
import AssistantPanel from '../components/assistant/AssistantPanel.vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const pid = route.params.id
const loading = ref(false)
const project = ref(null)
const chapters = ref([])
const outline = ref(null)
const world = ref(null)
const worldDraft = ref({
  world_view: "", characters: [],
  enemies: [], locations: [], factions: [],
  mechanism_baseline: {}, clock: {}, style_taboos: [],
})
const worldSaving = ref(false)
const lock = ref({})
// REQ-章节版本化：版本选择弹窗状态
const versionsDialog = ref(false)
const versionsNo = ref(0)
const versionList = ref([])
const outlineReady = computed(() => !!(outline.value && outline.value.chapters && outline.value.chapters.length))
// 记忆"曾生成过大纲"：重生成大纲期间后端会先删 outline.json，若此时重载大纲会拿到空值，
// 导致 outlineReady 翻转、v-else 按钮组整体消失。用 hadOutline 保住按钮组（生成中置灰而非隐藏）。
const hadOutline = ref(false)
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
const genError = ref('')
const exporting = ref(false)
let genStartTs = 0
const regenNo = ref(0)
// 生成完成后自增，强制重新挂载 OutlineEditor（它只在 onMounted 时拉一次大纲）
const outlineReloadKey = ref(0)
// 当前后端阶段（research/outline/editor_review/world/writing）：前置阶段用动画进度条
const currentPhase = ref('')
const PRE_PHASES = ['research', 'outline', 'editor_review', 'world']
const inPrePhase = computed(() => PRE_PHASES.includes(currentPhase.value))
// 成本防护/异常终止后可断点继续：后端提示含「可从断点继续」时，展示醒目的「继续生成」入口
const resumable = computed(() =>
  genError.value.includes('可从断点继续') || genError.value.includes('成本防护'))
// 章节生成进度（总章节数 / 已生成数），驱动主按钮三态：开始生成 → 继续生成 → 重新生成
const genTotal = ref(0)
const genDoneCount = ref(0)
const genPhase = computed(() => {
  if (generating.value && !genDone.value) return 'running'
  const total = genTotal.value || (outline.value?.chapters?.length) || 0
  const done = genDoneCount.value || chapters.value.length || 0
  if (!total) return 'none'
  if (done >= total) return 'done'
  if (done > 0) return 'partial'
  return 'none'
})

// 点击启动类按钮后立即给出进行中的进度条，不必等 3 秒后轮询
function beginGenUI(statusText) {
  generating.value = true
  genDone.value = false
  genError.value = ''
  genPct.value = 0
  genStatus.value = statusText
  currentPhase.value = ''
  genStartTs = 0  // 重新计时
}

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
  try { await ElMessageBox.confirm(`确定重新生成第${c.no}章《${c.title}》？将追加为新版本，旧版本保留。`, '重新生成章节', { type: 'warning' }) } catch { return }
  regenNo.value = c.no
  try {
    const res = await rewriteChapter(pid, c.no)
    const verTxt = res.version ? `，v${res.version}` : ''
    // P0-3：质检未通过不再静默交付——明确红标提示，用户可人工改稿或再次重写
    if (res.kept_previous) {
      const cvTxt = res.candidate_version ? `，重写稿已保留为 v${res.candidate_version}` : ''
      ElMessage.warning(`第${c.no}章已重写但质检未通过，已保留上一版正文${verTxt}${cvTxt}；可在「版本」弹窗查看新稿质检问题并选择/删除`)
    } else if (res.quality && !res.quality.passed) {
      ElMessage.warning(`第${c.no}章已重新生成（${res.words}字${verTxt}），但质检未通过（score=${res.quality.score}，${res.quality.issue_count}个问题），请人工检查后再编辑`)
    } else {
      ElMessage.success(`第${c.no}章已重新生成（${res.words}字${verTxt}）`)
    }
    loadChapters()
  } catch (e) {
    // 超时 ≠ 失败：重写 3 轮 LLM 调用可能超过 10 分钟前端等待，但后端会继续跑完落盘
    if (e?.code === 'ECONNABORTED' || (e?.message || '').includes('timeout')) {
      ElMessage.warning('重生成耗时较长，本次请求已超时；后端仍在处理，请稍后刷新页面查看最新内容')
      setTimeout(loadChapters, 3000)
    } else {
      ElMessage.error('重生成失败')
    }
  } finally { regenNo.value = 0 }
}

// REQ-章节版本化：版本选择
function sourceText(src) {
  return { ai: 'AI生成', user: '人工编辑', rewrite: '重写', regen: '重生成', legacy: '初始版' }[src] || src || '未知'
}
function fmtVerTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
// P2-3 申诉撤销：对某条质检/合规判罚发起申诉（误判兜底）
async function appealIssue(it) {
  const issueType = it.type || ''
  const label = issueType || '质检问题'
  try {
    const { value } = await ElMessageBox.prompt(
      `针对「${issueType || it.detail || '该问题'}」申诉：请简要说明为什么这是误判或可接受。`,
      `申诉 · ${label}`,
      { type: 'warning', inputPlaceholder: '例如：此段是战斗场景中的正常描写，非违规', confirmButtonText: '提交申诉', cancelButtonText: '取消', inputValidator: (v) => (v && v.trim() ? true : '请填写申诉理由') }
    )
    await appealChapterIssue(pid, versionsNo.value, { reason: value.trim(), issue_type: issueType })
    ElMessage.success('申诉已提交，将计入误判复核')
  } catch (e) {
    if (e === 'cancel' || e?.message === 'cancel' || e?.action === 'cancel') return
    ElMessage.error(e?.response?.data?.detail || '申诉提交失败')
  }
}

async function openVersions(c) {
  versionsNo.value = c.no
  versionList.value = []
  versionsDialog.value = true
  try {
    const res = await getChapterVersions(pid, c.no)
    versionList.value = res.versions || []
  } catch (e) {
    ElMessage.error('加载章节版本失败')
    versionsDialog.value = false
  }
}
async function selectVersion(v) {
  try {
    await ElMessageBox.confirm(`确定回退到第${versionsNo.value}章的 v${v} 版本？当前选中将切换为该版本。`, '切换版本', { type: 'warning' })
  } catch { return }
  try {
    const res = await selectChapterVersion(pid, versionsNo.value, v)
    ElMessage.success(`已切换到 v${v}（${res.words}字）`)
    versionList.value = (await getChapterVersions(pid, versionsNo.value)).versions || []
    loadChapters()
  } catch (e) {
    ElMessage.error(e?.message || '切换版本失败')
  }
}
async function deleteVersion(v) {
  try {
    await ElMessageBox.confirm(`确定删除第${versionsNo.value}章的 v${v} 版本？删除后不可恢复。`, '删除版本', { type: 'warning' })
  } catch { return }
  try {
    const res = await deleteChapterVersion(pid, versionsNo.value, v)
    versionList.value = res.versions || []
    ElMessage.success(`已删除 v${v}`)
  } catch (e) {
    ElMessage.error(e?.message || '删除版本失败')
  }
}
function addCharacter() {
  worldDraft.value.characters.push({ name: "", role: "", desc: "", appearance: "", personality: "", desire: "", wound: "", arc: "", speech_style: "", relations: "" })
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
    hadOutline.value = hadOutline.value || !!(outline.value?.chapters?.length)
    chapters.value = (await getChapters(pid)).chapters || []
    genTotal.value = outline.value?.chapters?.length || 0
    genDoneCount.value = chapters.value.length
    world.value = await getWorld(pid)
    worldDraft.value = JSON.parse(JSON.stringify(world.value))
    normalizeWorldDraft()
    try { lock.value = await getLock(pid) } catch { lock.value = {} }
    await checkStatus()
  } catch (e) {
    ElMessage.error('加载失败')
  } finally { loading.value = false }
}

const PHASE_LABEL = {
  research: '正在调研热门题材',
  outline: '正在生成大纲（分批进行，约几分钟）',
  editor_review: '正在审稿优化大纲',
  world: '正在生成人物与世界观',
  writing: '正在逐章写作',
  done: '已完成'
}
function phaseText(s) {
  const ph = s.state && s.state.phase
  const label = PHASE_LABEL[ph] || '正在生成'
  if (genStartTs) {
    const m = Math.floor((Date.now() - genStartTs) / 60000)
    if (m >= 1) return `${label}（已 ${m} 分钟）`
  }
  return label + '…'
}

async function checkStatus() {
  try {
    const s = await getStatus(pid)
    generating.value = s.running
    genDone.value = !s.running && s.chapters_done > 0
    currentPhase.value = (s.state && s.state.phase) || ''
    if (s.chapters_total) {
      genTotal.value = s.chapters_total
      genPct.value = Math.round(s.chapters_done / s.chapters_total * 100)
    }
    genDoneCount.value = s.chapters_done ?? genDoneCount.value
    genError.value = s.error || ''
    genStatus.value = s.running && !s.error ? phaseText(s) : ''
    if (s.running) startPolling()
  } catch {}
}

function startPolling() {
  if (!genStartTs) genStartTs = Date.now()
  if (pollTimer) clearInterval(pollTimer)
  let lastDone = -1
  pollTimer = setInterval(async () => {
    try {
      const s = await getStatus(pid)
      generating.value = s.running
      currentPhase.value = (s.state && s.state.phase) || ''
      if (s.chapters_total) {
        genTotal.value = s.chapters_total
        genPct.value = Math.round(s.chapters_done / s.chapters_total * 100)
      }
      genDoneCount.value = s.chapters_done ?? genDoneCount.value
      genError.value = s.error || ''
      genStatus.value = s.running && !s.error ? phaseText(s) : ''
      // 每生成完一章就刷新章节列表，让用户实时看到进度
      if (s.chapters_done !== lastDone) {
        lastDone = s.chapters_done
        loadChapters()
      }
      if (!s.running) {
        genDone.value = true
        clearInterval(pollTimer)
        // 生成结束后全量刷新大纲/世界观/章节/锁，否则切到对应 tab 仍是旧空数据
        if (s.error) {
          ElMessage.error('生成失败：' + s.error)
        } else {
          await refreshAfterGen()
        }
      }
    } catch {}
  }, 3000)
}

// 生成完成后的静默刷新：不弹全屏 loading，只重拉数据并强制重挂大纲编辑器
async function refreshAfterGen() {
  try {
    outline.value = await getOutline(pid)
    hadOutline.value = hadOutline.value || !!(outline.value?.chapters?.length)
    world.value = await getWorld(pid)
    worldDraft.value = JSON.parse(JSON.stringify(world.value))
    normalizeWorldDraft()
    try { lock.value = await getLock(pid) } catch { lock.value = {} }
    chapters.value = (await getChapters(pid)).chapters || []
    genDoneCount.value = chapters.value.length
    outlineReloadKey.value++
    loadSnapshots()
    ElMessage.success('任务已完成，数据已刷新')
  } catch (e) {
    ElMessage.error('数据刷新失败，请手动刷新页面')
  }
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

// 小墨AI「定位到第 X 章」：切回章节 tab 并打开阅读器
function locateChapter(no) {
  const ch = chapters.value.find(c => Number(c.no) === Number(no))
  if (ch) { tab.value = 'chapters'; openChapter(ch) }
  else ElMessage.warning(`第 ${no} 章还未生成`)
}

function startEdit() {
  editing.value = true
}
function cancelEdit() {
  editing.value = false
}

// === 选中段落扩写/续写（恢复自旧版 webui 的浮动按钮入口） ===
const selFloat = ref({ show: false, x: 0, y: 0, text: '', fromEdit: false, editPos: -1 })
const expanding = ref(false)

function hideSelFloat() { selFloat.value.show = false }

function showSelFloat(e, text, fromEdit, editPos) {
  const maxX = Math.max(8, window.innerWidth - 130)
  const x = Math.min(Math.max(8, e.clientX), maxX)
  const y = Math.max(8, e.clientY - 44)
  selFloat.value = { show: true, x, y, text, fromEdit, editPos }
}

// 阅读模式：选中 v-html 渲染的正文
function onReaderMouseup(e) {
  const sel = window.getSelection()
  const text = sel ? sel.toString().trim() : ''
  if (!text || text.length < 5) { hideSelFloat(); return }
  showSelFloat(e, text, false, -1)
}

// 编辑模式：选中 textarea 内的文字
function onEditMouseup(e) {
  const ta = e.target
  if (!ta || ta.tagName !== 'TEXTAREA') { hideSelFloat(); return }
  const text = ta.value.substring(ta.selectionStart, ta.selectionEnd).trim()
  if (!text || text.length < 5) { hideSelFloat(); return }
  showSelFloat(e, text, true, ta.selectionEnd)
}

// 点击浮动条之外的区域时收起
function onDocMouseDown(e) {
  if (selFloat.value.show && !(e.target && e.target.closest && e.target.closest('.sel-float'))) hideSelFloat()
}

async function doExpand(mode) {
  if (expanding.value) return
  const { text, fromEdit, editPos } = selFloat.value
  hideSelFloat()
  if (!text) return
  expanding.value = true
  try {
    const res = await expandSelection(pid, { text, mode })
    const action = mode === 'continue' ? '续写' : '扩写'
    const ok = await ElMessageBox.confirm(
      h('pre', { style: 'white-space:pre-wrap;margin:0;max-height:300px;overflow:auto;font-size:13px;line-height:1.7;' }, res.result),
      `${action}结果`,
      { confirmButtonText: '插入到文中', cancelButtonText: '放弃', type: 'info' }
    ).then(() => true).catch(() => false)
    if (!ok) return
    // 插入位置：编辑态取光标处；阅读态取选中文本在原内容中的结束位置，找不到则追加到末尾
    let pos = fromEdit && editPos >= 0 ? editPos : -1
    if (pos < 0) {
      const idx = editContent.value.indexOf(text)
      pos = idx >= 0 ? idx + text.length : editContent.value.length
    }
    if (!editing.value) editing.value = true
    const prefix = pos === 0 || editContent.value[pos - 1] === '\n' ? '' : '\n'
    editContent.value = editContent.value.slice(0, pos) + prefix + res.result + editContent.value.slice(pos)
    ElMessage.success(`${action}内容已插入，可继续编辑后保存`)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '扩写/续写失败')
  } finally { expanding.value = false }
}

async function saveEdit() {
  saving.value = true
  try {
    await updateChapterContent(pid, currentCh.value.no, { content: editContent.value })
    currentContent.value = editContent.value.replace(/\n/g, '<br>')
    editing.value = false
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally { saving.value = false }
}

async function runOutline() {
  try { await genOutline(pid); ElMessage.success('已开始生成大纲与世界观，完成后可在「大纲」「人物/世界观」查看/编辑'); beginGenUI('正在启动大纲生成…'); startPolling() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '启动失败') }
}
async function doRegenOutline() {
  try { await ElMessageBox.confirm('重生成大纲会覆盖现有总纲/分卷，已锁定内容不受影响。继续？', '重生成大纲', { type: 'warning' }) } catch { return }
  try { await regenOutline(pid); ElMessage.success('已开始重生成大纲'); beginGenUI('正在启动重生成大纲…'); startPolling(); loadSnapshots() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '失败') }
}
async function doRegenWorld() {
  try { await ElMessageBox.confirm('重生成世界观会覆盖现有世界设定，已锁定内容不受影响。继续？', '重生成世界观', { type: 'warning' }) } catch { return }
  try { await regenWorld(pid); ElMessage.success('已开始重生成世界观'); beginGenUI('正在启动重生成世界观…'); startPolling() }
  catch (e) { ElMessage.error(e?.response?.data?.detail || '失败') }
}
async function startGen() {
  try {
    await startGenerate(pid, {})
    ElMessage.success('已开始生成')
    beginGenUI('正在启动写作…')
    startPolling()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动失败')
  }
}

async function regenAllChapters() {
  try { await ElMessageBox.confirm('将重新生成全部章节正文（保留大纲/世界观，已锁定章节自动跳过，未锁定章节内容将被覆盖）。继续？', '重新生成全部章节', { type: 'warning' }) } catch { return }
  try {
    await regenerateChapters(pid)
    ElMessage.success('已开始重新生成全部章节')
    beginGenUI('正在启动整本重写…')
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

// blob 错误响应（如 404 detail 以 JSON 返回时，axios 会把它包成 Blob）转成可读信息
async function blobErrorDetail(e) {
  try {
    const d = e?.response?.data
    if (d && typeof d.text === 'function') {
      const t = await d.text()
      try { return JSON.parse(t)?.detail || t } catch { return t }
    }
    return e?.response?.data?.detail || e?.message || ''
  } catch { return '' }
}

async function doDownload(fmt) {
  exporting.value = true
  try {
    const blob = await downloadProject(pid, fmt)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const title = (project.value?.config?.title || pid).replace(/[\\/:*?"<>|]/g, '_')
    a.href = url
    a.download = `${title}.${fmt}`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    ElMessage.success(`已导出 ${fmt.toUpperCase()}`)
  } catch (e) {
    const detail = await blobErrorDetail(e)
    ElMessage.error(detail || `导出 ${fmt.toUpperCase()} 失败，请确认已生成章节`)
  } finally { exporting.value = false }
}

function statusType(s) {
  if (s === 'done') return 'success'
  if (s === 'generating') return 'warning'
  if (s === 'failed') return 'danger'
  return 'info'
}
const STATUS_TEXT = { done: '已完成', generating: '生成中', pending: '待生成', failed: '失败' }
function statusText(s) { return STATUS_TEXT[s] || s }

const importVisible = ref(false)
const importText = ref('')
const importFileMsg = ref('')
const importFileInput = ref(null)
const reimporting = ref(false)
const snapshots = ref([])

function openImport() {
  importText.value = ''
  importFileMsg.value = ''
  importVisible.value = true
}

function onImportFilePick(e) {
  const f = e.target.files[0]
  if (!f) return
  if (f.size > 5 * 1024 * 1024) { importFileMsg.value = '文件超过 5MB 限制，请精简后上传'; e.target.value = ''; return }
  const ext = ((f.name.match(/\.([^.]+)$/) || [])[1] || '').toLowerCase()
  if (!['txt', 'md', 'docx'].includes(ext)) { importFileMsg.value = '仅支持 .txt / .md / .docx 格式'; e.target.value = ''; return }
  importFileMsg.value = '正在解析…'
  const fd = new FormData()
  fd.append('file', f)
  parseContent(fd).then(res => {
    importText.value = res.full_text || ''
    const n = res.chapters?.length || 0
    importFileMsg.value = n ? `已识别 ${n} 个章节，已填入下方输入框` : '解析完成，已填入（未识别到章节标题）'
  }).catch(err => {
    importFileMsg.value = err.response?.data?.detail || '解析失败，请检查文件格式'
    e.target.value = ''
  })
}

async function doImport() {
  const text = importText.value.trim()
  if (!text) { ElMessage.warning('请粘贴或上传大纲内容'); return }
  if (text.length > 100000) { ElMessage.warning('粘贴内容超过 10 万字上限，请拆分或改用文件上传'); return }
  reimporting.value = true
  try {
    await importOutline(pid, { text })
    ElMessage.success('已导入（以你的内容为准）')
    importVisible.value = false
    loadSnapshots()
    loadAll()
  } catch (e) {
    ElMessage.warning(e.response?.data?.detail || '导入失败')
  } finally { reimporting.value = false }
}

async function loadSnapshots() {
  try { snapshots.value = (await listSnapshots(pid))?.snapshots || [] } catch (e) { snapshots.value = [] }
}

async function onSnapCmd(cmd) {
  if (!cmd || !cmd.startsWith('restore:')) return
  const name = cmd.slice(8)
  try { await ElMessageBox.confirm(`从快照「${name}」恢复大纲？当前大纲将被该快照覆盖。`, '恢复大纲', { type: 'warning' }) } catch { return }
  try {
    await restoreSnapshot(pid, name)
    ElMessage.success('已恢复')
    loadSnapshots()
    loadAll()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '恢复失败') }
}

onMounted(() => { document.addEventListener('mousedown', onDocMouseDown); loadAll(); loadSnapshots() })
onUnmounted(() => { document.removeEventListener('mousedown', onDocMouseDown); if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.detail-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.step-actions { display:flex; gap:8px; align-items:center; }
.gen-status { font-size: 13px; color: #909399; margin-left: 12px; }
.gen-error { display:inline-block; margin-top:6px; font-size:13px; color:#f56c6c; background:#fef0f0; border:1px solid #fbc4c4; border-radius:6px; padding:6px 10px; max-width:640px; word-break:break-all; }
.gen-error-wrap { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.ch-list { display: flex; flex-direction: column; gap: 8px; }
.ch-group-title { font-weight: 600; font-size: 14px; color: #1f3a5f; margin: 10px 2px 4px; padding-left: 2px; border-left: 3px solid #c9a961; }
.ch-item { display: flex; align-items: center; gap: 12px; padding: 10px 14px; background: #fff; border-radius: 8px; cursor: pointer; }
.ver-list { display: flex; flex-direction: column; gap: 8px; max-height: 420px; overflow: auto; }
.ver-item { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; padding: 10px 12px; border: 1px solid #e4e7ed; border-radius: 8px; }
.ver-item.active { border-color: #67c23a; background: #f0f9eb; }
.ver-main { flex: 1; min-width: 0; }
.ver-info { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ver-no { font-weight: 600; font-size: 14px; }
.ver-words { color: #909399; font-size: 12px; }
.ver-ts { color: #909399; font-size: 12px; }
.ver-actions { display: flex; flex-direction: column; gap: 4px; align-items: flex-end; flex-shrink: 0; }
.ver-issues { margin-top: 8px; padding: 8px 10px; background: #fdf6ec; border: 1px solid #faecd8; border-radius: 6px; }
.ver-issues-head { font-size: 12px; font-weight: 600; color: #b88230; margin-bottom: 4px; }
.ver-issue { font-size: 12px; color: #606266; line-height: 1.6; margin-top: 2px; }
.ver-issue .lv-big { color: #f56c6c; font-weight: 600; margin-right: 4px; }
.ver-issue .lv-mid { color: #e6a23c; font-weight: 600; margin-right: 4px; }
.ver-issue .lv-small { color: #909399; margin-right: 4px; }
.ver-issue-type { color: #409eff; margin-right: 4px; }
.ver-issue-more { font-size: 12px; color: #909399; margin-top: 4px; }
.ver-noq { margin-top: 6px; font-size: 12px; color: #c0c4cc; }
.ver-tip { margin-top: 12px; color: #909399; font-size: 12px; line-height: 1.6; }
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
.file-msg { font-size: 12px; line-height: 1.6; margin-top: 4px; }
.file-ok { color: #67c23a; }
.file-err { color: #f56c6c; }
.import-hint { font-size: 13px; color: #606266; line-height: 1.7; margin-bottom: 8px; }
.sel-float { position: fixed; z-index: 9999; display: flex; align-items: center; gap: 10px; background: #5a4a3a; color: #f5f1e2; padding: 6px 12px; border-radius: 8px; font-size: 13px; box-shadow: 0 2px 12px rgba(0,0,0,.3); user-select: none; }
.sel-float-item { cursor: pointer; }
.sel-float-item:hover { color: #e6c98a; }
.sel-float-sep { opacity: .4; }
</style>
