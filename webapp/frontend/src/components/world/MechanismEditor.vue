<template>
  <!-- 机制初始值区块（REQ-通用机制类型适配）：按 books 的机制类型分派渲染。
       类型 Source of Truth 在 novel_gen/pipeline/mechanism_types.py，这里只做展示/编辑：
         numeric_panel → mechanism_baseline 数字标尺
         rule_set      → mechanism_ruleset 条款清单
         ability       → goldfinger_rules 能力规则五要素
         power_level   → power_system 境界阶梯
         contract/temporal → 复用人物/物品/事实账本，此处只展示机制说明文本
         none          → 不显示本区块
       旧 world（无 mechanism_types）按后端同一套口径推断，保证不白屏（详见 resolveTypes）。 -->
  <div v-if="draft">
    <div class="mech-head">
      <el-tag v-for="t in types" :key="t" size="small" :type="t === 'none' ? 'info' : 'warning'" style="margin-right:6px">
        {{ TYPE_LABELS[t] || t }}
      </el-tag>
      <span v-if="inferred" class="mech-hint">（由既有设定推断，未显式声明）</span>
    </div>

    <!-- 数字面板/状态型：正文数字的唯一标准答案 -->
    <div v-if="hasType('numeric_panel')" class="mech-block">
      <div class="mech-title">数字标尺（初始值 · 正文数字唯一标准答案）</div>
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
    <div v-if="hasType('rule_set')" class="mech-block">
      <div class="mech-title">规则条款清单（{{ (draft.mechanism_ruleset || []).length }} 条 · 作用域内绝对不可违背）</div>
      <div v-for="(r, i) in (draft.mechanism_ruleset || [])" :key="'rule' + i" class="rule-card">
        <div class="rule-row">
          <span class="char-label">编号</span>
          <el-input v-model="r.rule_id" size="small" style="width:100px" placeholder="R1" />
          <span class="char-label">适用范围</span>
          <el-select v-model="r.scope" size="small" filterable allow-create style="width:220px"
                     placeholder="global / instance:副本名 / character:角色名 / ability:能力名">
            <el-option label="全书通用" value="global" />
            <el-option label="副本专属（instance:副本名）" value="instance:" />
            <el-option label="角色专属（character:角色名）" value="character:" />
            <el-option label="能力专属（ability:能力名）" value="ability:" />
          </el-select>
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
    <div v-if="hasType('ability')" class="mech-block">
      <div class="mech-title">能力规则（五要素 · 缺项会在阶段准出被打回）</div>
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
    <div v-if="hasType('power_level')" class="mech-block">
      <div class="mech-title">等级/境界阶梯（防后期战力崩坏）</div>
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
    <div v-if="hasType('contract', 'temporal') || draft.mechanism_rules" class="mech-block">
      <div class="mech-title">机制说明（契约/时间循环类机制的数值走人物·物品·事实账本）</div>
      <el-input v-model="draft.mechanism_rules" type="textarea" :rows="4" placeholder="用自然语言写清契约/循环的初始状态与结算口径" />
    </div>
  </div>
</template>

<script setup>
/**
 * 机制初始值编辑器（自 WorldEditor 拆出，§11.1 文件规模红线）。
 * 类型判定口径与 novel_gen/pipeline/mechanism_types.py 保持一致：
 * 显式 mechanism_types 优先 → 缺失时按既有字段推断 → 仍无则 none。
 */
import { computed } from 'vue'
import { POLICY_LABELS, STATUS_LABELS, TYPE_LABELS, normalizeTypes, inferTypes } from './mechanism'

const props = defineProps({
  draft: { type: Object, required: true },  // 世界观草稿（整体对象，内部字段增量编辑）
})

const declaredTypes = computed(() => normalizeTypes(props.draft?.mechanism_types))
const inferred = computed(() => declaredTypes.value.length === 0)
const types = computed(() => declaredTypes.value.length ? declaredTypes.value : inferTypes(props.draft || {}))

const hasType = (...names) => names.some(n => types.value.includes(n))

function addRule() {
  const list = props.draft.mechanism_ruleset || (props.draft.mechanism_ruleset = [])
  list.push({
    rule_id: `R${props.draft.mechanism_ruleset.length + 1}`, scope: 'global', statement: '',
    condition: '', consequence: '', change_policy: 'fixed', source: 'user', first_ch: 0, status: 'active',
  })
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
