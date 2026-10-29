// 机制类型元数据与判定（REQ-通用机制类型适配）——前端唯一事实来源。
//
// 口径与后端 novel_gen/pipeline/mechanism_types.py 严格对齐：
//   显式 mechanism_types 优先 → 缺失时按既有字段推断 → 仍无则 none。
// 展示标签（TYPE_LABELS / 变更政策 / 状态）同样沿用后端口径，避免前后端两套说法。

export const MECHANISM_TYPES = [
  'numeric_panel', 'power_level', 'rule_set', 'ability', 'contract', 'temporal', 'none',
]

export const TYPE_LABELS = {
  numeric_panel: '数字面板/状态型', power_level: '等级境界型', rule_set: '规则约束型',
  ability: '能力/概念金手指型', contract: '契约/关系/身份型', temporal: '时间/循环/因果型',
  none: '无机制',
}

/** 折叠面板标题用的短名（叠加类型用「 + 」连接）。 */
export const TYPE_TITLES = {
  numeric_panel: '数字面板', power_level: '等级境界', rule_set: '规则条款',
  ability: '能力规则', contract: '契约关系', temporal: '时间循环', none: '无机制',
}

export const POLICY_LABELS = {
  fixed: '不可改写', discoverable: '随剧情揭示', negotiable: '可谈判', layered: '分层揭示',
}

export const STATUS_LABELS = { active: '生效', resolved: '已解除', broken: '已被打破' }

/** 与后端 normalize_types 同口径：去空、去重保序、剔除非法值、none 不可叠加。 */
export function normalizeTypes(raw) {
  const list = Array.isArray(raw) ? raw : (typeof raw === 'string' ? String(raw).split(/[,，、/;；]/) : [])
  const out = []
  for (const item of list) {
    const key = String(item ?? '').trim().toLowerCase()
    if (key && MECHANISM_TYPES.includes(key) && !out.includes(key)) out.push(key)
  }
  return out.length > 1 ? out.filter(t => t !== 'none') : out
}

/** 与后端 infer_types_from_world 同口径的旧书兜底推断（本书无显式类型声明时）。 */
export function inferTypes(d) {
  const draft = d || {}
  const types = []
  const mb = draft.mechanism_baseline || {}
  if (Object.values(mb).some(v => String(v ?? '').trim())) types.push('numeric_panel')
  if ((draft.mechanism_ruleset || []).length) types.push('rule_set')
  const gf = draft.goldfinger_rules || {}
  if (String(gf.trigger || '').trim() && String(gf.upper_limit || '').trim()) types.push('ability')
  const ps = draft.power_system || {}
  if ((ps.levels || []).length || String(ps.protagonist_level || '').trim()) types.push('power_level')
  return types.length ? types : ['none']
}

/** resolve_types 前端版：显式优先，缺失回退推断，再缺回退 none。 */
export function resolveTypes(d) {
  const declared = normalizeTypes(d?.mechanism_types)
  return declared.length ? declared : inferTypes(d)
}

/** 是否存在至少一个非 none 的机制（决定「机制初始值」区块是否显示）。 */
export function hasMechanism(d) {
  return resolveTypes(d).some(t => t !== 'none') || String(d?.mechanism_rules || '').trim() !== ''
}
