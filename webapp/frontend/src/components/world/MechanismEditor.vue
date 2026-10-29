<template>
  <!-- 机制初始值区块（REQ-通用机制类型适配）：按本书的机制类型分派渲染。
       类型判定的唯一事实来源是后端 `novel_gen/pipeline/mechanism_types.py`：
       直接用它下发的 `mechanism_types_resolved`（= resolve_types，含中文别名归一与
       旧书推断），前端不复制别名表、不自造口径（§11.2/§11.3）。
         numeric_panel → mechanism_baseline 数字标尺
         rule_set      → mechanism_ruleset 条款清单
         ability       → goldfinger_rules 能力规则五要素
         power_level   → power_system 境界阶梯
         contract/temporal → 复用人物/物品/事实账本，此处只展示机制说明文本
         none          → 不显示数值区块（但该类型仍有数据时兜底展示，防改不了） -->
  <div v-if="draft">
    <div class="mech-head">
      <el-tag v-for="t in types" :key="t" size="small" :type="t === 'none' ? 'info' : 'warning'" style="margin-right:6px">
        {{ TYPE_LABELS[t] || t }}
      </el-tag>
      <span v-if="inferred" class="mech-hint">（由既有设定推断，未显式声明）</span>
      <!-- 类型标签编辑入口：让用户能显式声明/纠正类型，不必依赖推断（P2-3）。
           不选 = 交回后端按既有设定推断；选中值由后端 normalize_types 清洗后落库。 -->
      <el-select v-model="draft.mechanism_types" multiple collapse-tags collapse-tags-tooltip
                 size="small" style="min-width:260px;margin-left:8px"
                 placeholder="显式声明机制类型（不选=由既有设定推断）">
        <el-option v-for="(label, key) in TYPE_LABELS" :key="key" :label="label" :value="key" />
      </el-select>
    </div>

    <!-- 数字面板/状态型：正文数字的唯一标准答案 -->
    <div v-if="show('numeric_panel')" class="mech-block">
      <div class="mech-title">
        数字标尺（初始值 · 正文数字唯一标准答案）
        <span v-if="isFallback('numeric_panel')" class="mech-hint">（数据兜底展示：未纳入类型判定）</span>
      </div>
      <div class="char-fields">
        <div class="char-row"><span class="char-label">标记起点</span>
          <el-input v-model="draft.mechanism_baseline.scar_start" size="small" placeholder="机制计数/进度的起始值" /></div>
        <div class="char-row"><span class="char-label">每次触发增量</span>
          <el-input v-model="draft.mechanism_baseline.scar_per_death" size="small" placeholder="触发一次的变化量" /></div>
        <div class="char-row"><span class="char-label">标记当前</span>
          <el-input v-model="draft.mechanism_baseline.scar_current" size="small" placeholder="开书时的当前值" /></div>
        <div class="char-row"><span class="char-label">终局</span>
          <el-input v-model="draft.mechanism_baseline.scar_end" size="small" placeholder="到达什么值=进入终局" /></div>
        <div class="char-row"><span class="char-label">面板唯一写法</span>
          <el-input v-model="draft.mechanism_baseline.panel_format" size="small" placeholder="如：今日可死次数：N/3" /></div>
        <div class="char-row"><span class="char-label">重置规则</span>
          <el-input v-model="draft.mechanism_baseline.reset_rule" size="small" placeholder="何时归零/恢复到多少" /></div>
      </div>
    </div>

    <!-- 规则约束型：条款一经公布在其作用域内不得改写 -->
    <div v-if="show('rule_set')" class="mech-block">
      <div class="mech-title">
        规则条款清单（{{ (draft.mechanism_ruleset || []).length }} 条 · 作用域内绝对不可违背）
        <span v-if="isFallback('rule_set')" class="mech-hint">（数据兜底展示：未纳入类型判定）</span>
      </div>
      <div v-for="(r, i) in (draft.mechanism_ruleset || [])" :key="'rule' + i" class="rule-card">
        <div class="rule-row">
          <span class="char-label">编号</span>
          <el-input v-model="r.rule_id" size="small" style="width:100px" placeholder="R1" />
          <span class="char-label">适用范围</span>
          <!-- scope 形如 global / instance:副本名 / character:角色名 / ability:能力名：
               拆成"前缀 + 对象名"两控件拼装，避免用户选中残缺前缀存成畸形 scope -->
          <el-select v-model="r._scopeKind" size="small" style="width:140px" @change="onScopeKind(r)">
            <el-option v-for="(v, k) in SCOPE_KINDS" :key="k" :label="v" :value="k" />
          </el-select>
          <el-input v-if="r._scopeKind !== 'global'" v-model="r._scopeName" size="small"
                    style="width:160px" :placeholder="SCOPE_NAME_PLACEHOLDER[r._scopeKind] || '对象名'"
                    @change="syncScope(r)" />
          <el-button type="danger" size="small" link @click="removeRule(i)">删除</el-button>
        </div>
        <div class="char-row"><span class="char-label">条款内容</span>
          <el-input v-model="r.statement" size="small" placeholder="规则正文，如：夜间不得回应敲门声" /></div>
        <div class="char-row"><span class="char-label">触发条件</span>
          <el-input v-model="r.condition" size="small" placeholder="什么情况下该条款启动" /></div>
        <div class="char-row"><span class="char-label">违反后果</span>
          <el-input v-model="r.consequence" size="small" placeholder="违反后发生什么" /></div>
        <div class="rule-row">
          <span class="char-label">变更政策</span>
          <el-select v-model="r.change_policy" size="small" style="width:150px">
            <el-option v-for="(v, k) in POLICY_LABELS" :key="k" :label="v" :value="k" />
          </el-select>
          <span class="char-label">首现章</span>
          <el-input-number v-model="r.first_ch" size="small" :min="0" controls-position="right" style="width:120px" />
          <span class="char-label">状态</span>
          <el-select v-model="r.status" size="small" style="width:120px">
            <el-option v-for="(v, k) in STATUS_LABELS" :key="k" :label="v" :value="k" />
          </el-select>
        </div>
      </div>
      <el-button size="small" @click="addRule">+ 添加条款</el-button>
    </div>

    <!-- 能力/概念金手指型：五要素 -->
    <div v-if="show('ability')" class="mech-block">
      <div class="mech-title">
        能力规则（五要素 · 缺项会在阶段准出被打回）
        <span v-if="isFallback('ability')" class="mech-hint">（数据兜底展示：未纳入类型判定）</span>
      </div>
      <div class="char-fields">
        <div class="char-row"><span class="char-label">触发条件</span>
          <el-input v-model="draft.goldfinger_rules.trigger" size="small" placeholder="什么条件下可发动" /></div>
        <div class="char-row"><span class="char-label">能力上限</span>
          <el-input v-model="draft.goldfinger_rules.upper_limit" size="small" placeholder="最强能到什么程度" /></div>
        <div class="char-row"><span class="char-label">能力下限</span>
          <el-input v-model="draft.goldfinger_rules.lower_limit" size="small" placeholder="最弱/初始表现" /></div>
        <div class="char-row"><span class="char-label">使用代价</span>
          <el-input v-model="draft.goldfinger_rules.cost" size="small" placeholder="每次使用的反噬/消耗" /></div>
        <div class="char-row"><span class="char-label">成长路径</span>
          <el-input v-model="draft.goldfinger_rules.growth_path" size="small" placeholder="如何变强、分几阶段" /></div>
      </div>
    </div>

    <!-- 等级境界型：阶梯 + 主角当前境界 -->
    <div v-if="show('power_level')" class="mech-block">
      <div class="mech-title">
        等级/境界阶梯（防后期战力崩坏）
        <span v-if="isFallback('power_level')" class="mech-hint">（数据兜底展示：未纳入类型判定）</span>
      </div>
      <div class="char-row"><span class="char-label">主角当前境界</span>
        <el-input v-model="draft.power_system.protagonist_level" size="small" placeholder="开书时主角所处境界" /></div>
      <div class="char-row"><span class="char-label">突破规则</span>
        <el-input v-model="draft.power_system.progression_rule" size="small" placeholder="如何升级/是否可跳跃/瓶颈限制" /></div>
      <div v-for="(lv, i) in (draft.power_system.levels || [])" :key="'lv' + i" class="rule-card">
        <div class="rule-row">
          <span class="char-label">境界名</span>
          <el-input v-model="lv.name" size="small" style="width:140px" placeholder="如 炼气" />
          <span class="char-label">区间</span>
          <el-input v-model="lv.range" size="small" style="width:140px" placeholder="如 1-9 重" />
          <el-button type="danger" size="small" link @click="removeLevel(i)">删除</el-button>
        </div>
        <div class="char-row"><span class="char-label">突破条件</span>
          <el-input v-model="lv.requirement" size="small" /></div>
        <div class="char-row"><span class="char-label">能力上限</span>
          <el-input v-model="lv.capability" size="small" /></div>
      </div>
      <el-button size="small" @click="addLevel">+ 添加境界</el-button>
    </div>

    <!-- 契约/时间循环型：无独立数值结构，展示机制说明（动态调整走既有账本） -->
    <div v-if="show('contract') || show('temporal') || draft.mechanism_rules" class="mech-block">
      <div class="mech-title">机制说明（契约/时间循环类机制的数值走人物·物品·事实账本）</div>
      <el-input v-model="draft.mechanism_rules" type="textarea" :rows="4" placeholder="用自然语言写清契约/循环的初始状态与结算口径" />
    </div>
  </div>
</template>

<script setup>
/**
 * 机制初始值编辑器（自 WorldEditor 拆出，§11.1 文件规模红线）。
 *
 * 类型判定**不自算**：直接用后端下发的 `mechanism_types_resolved`
 * （= novel_gen/pipeline/mechanism_types.py 的 resolve_types，含中文别名归一与旧书
 * 推断），前端只负责展示与编辑（§11.2 单一权威源、§11.3 同一份规则不写两处）。
 */
import { computed, watch } from 'vue'
import {
  POLICY_LABELS, STATUS_LABELS, TYPE_LABELS, dataTypes, isInferred,
  resolveTypes, showBlock,
} from './mechanism'

const props = defineProps({
  draft: { type: Object, required: true },  // 世界观草稿（整体对象，内部字段增量编辑）
})

// scope 拆成「前缀 + 对象名」两控件拼装，避免用户选中残缺前缀存成畸形 scope
const SCOPE_KINDS = { global: '全书通用', 'instance:': '副本专属', 'character:': '角色专属', 'ability:': '能力专属' }
const SCOPE_NAME_PLACEHOLDER = { 'instance:': '副本名', 'character:': '角色名', 'ability:': '能力名' }

const types = computed(() => resolveTypes(props.draft || {}))
const fallbackTypes = computed(() => dataTypes(props.draft || {}))
const inferred = computed(() => isInferred(props.draft || {}))

/** 区块是否渲染：类型判定命中，或该类型数据确实存在（兜底，防"改不了"）。 */
const show = name => showBlock(props.draft || {}, name)
/** 是否属于"没被类型判定纳入、但有数据"的兜底展示。 */
const isFallback = name => !types.value.includes(name) && fallbackTypes.value.includes(name)

/** 加载/刷新后把 scope 字符串拆回两控件，保证编辑态与库值一致。 */
function syncScopeFromRaw(r) {
  const raw = String(r.scope || 'global')
  const idx = raw.indexOf(':')
  if (idx > 0 && SCOPE_KINDS[raw.slice(0, idx + 1)]) {
    r._scopeKind = raw.slice(0, idx + 1)
    r._scopeName = raw.slice(idx + 1)
  } else {
    r._scopeKind = 'global'
    r._scopeName = ''
  }
}
function syncScope(r) {
  r.scope = r._scopeKind === 'global' ? 'global' : `${r._scopeKind}${String(r._scopeName || '').trim()}`
}
function onScopeKind(r) {
  if (r._scopeKind === 'global') r._scopeName = ''
  syncScope(r)
}

// 草稿被整体替换（重新加载/保存后回填）时也要重拆，故监听数组引用而非长度
watch(
  () => props.draft?.mechanism_ruleset,
  list => (list || []).forEach(syncScopeFromRaw),
  { immediate: true },
)

function addRule() {
  const list = props.draft.mechanism_ruleset || (props.draft.mechanism_ruleset = [])
  const rule = {
    rule_id: `R${list.length + 1}`, scope: 'global', statement: '',
    condition: '', consequence: '', change_policy: 'fixed', source: 'user', first_ch: 0, status: 'active',
  }
  syncScopeFromRaw(rule)
  list.push(rule)
}
function removeRule(i) { (props.draft.mechanism_ruleset || []).splice(i, 1) }

function addLevel() {
  const levels = props.draft.power_system?.levels || (props.draft.power_system = { ...props.draft.power_system, levels: [] }).levels
  levels.push({ name: '', range: '', requirement: '', capability: '' })
}
function removeLevel(i) { (props.draft.power_system?.levels || []).splice(i, 1) }
</script>

<style scoped>
.mech-head { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.mech-hint { color: #909399; font-size: 12px; }
.mech-block { margin-bottom: 14px; border-left: 2px solid #d4b886; padding-left: 10px; }
.mech-title { font-size: 13px; font-weight: 600; color: #c8a86a; margin-bottom: 6px; }
.rule-card { border: 1px solid #ebeef5; border-radius: 4px; padding: 8px; margin-bottom: 8px; }
.rule-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }
</style>
