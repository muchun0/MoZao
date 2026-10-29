// 机制类型元数据与判定（REQ-通用机制类型适配）——前端展示层。
//
// 【口径来源（重要）】类型判定的唯一事实来源是后端
// `novel_gen/pipeline/mechanism_types.py`：`GET/PUT /api/projects/{id}/world`
// 下发的 `mechanism_types_resolved`（= `resolve_types(world)`，含中文别名归一与
// 旧书按字段推断）是权威结果。本模块**不复制**后端别名表、也不自造判定口径
// （§11.2 跨模块状态收敛到单一权威源 / §11.3 同一份规则不写两处）：
//   - 有 `mechanism_types_resolved` → 直接采用（只做枚举白名单防御性过滤）
//   - 无该字段（旧版本响应缓存）→ 退化为「显式枚举值 + 本地兜底推断」
// 展示标签（TYPE_LABELS / 变更政策 / 状态）沿用后端口径用词，避免两套说法。

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

/** 枚举清洗（防御性）：去空、小写、去重保序、剔除非法值、none 不可叠加。
 *  中文别名归一只发生在后端，前端不镜像别名表——避免两处词表漂移。 */
export function normalizeTypes(raw) {
  const list = Array.isArray(raw) ? raw : (typeof raw === 'string' ? String(raw).split(/[,，、/;；]/) : [])
  const out = []
  for (const item of list) {
    const key = String(item ?? '').trim().toLowerCase()
    if (key && MECHANISM_TYPES.includes(key) && !out.includes(key)) out.push(key)
  }
  return out.length > 1 ? out.filter(t => t !== 'none') : out
}

/** 旧书兜底推断，字段子集与后端 `infer_types_from_world` 严格一致：
 *  baseline 只认 scar_start / scar_current / panel_format / reset_rule 四个字段；
 *  ruleset 非空 → rule_set；goldfinger 有 trigger 且 upper_limit → ability；否则 none。
 *  仅在响应里没有 `mechanism_types_resolved` 时才会走到这里。 */
export function inferTypes(d) {
  const draft = d || {}
  const types = []
  const mb = draft.mechanism_baseline || {}
  const BASELINE_FIELDS = ['scar_start', 'scar_current', 'panel_format', 'reset_rule']
  if (BASELINE_FIELDS.some(f => String(mb[f] ?? '').trim())) types.push('numeric_panel')
  if ((draft.mechanism_ruleset || []).length) types.push('rule_set')
  const gf = draft.goldfinger_rules || {}
  if (String(gf.trigger || '').trim() && String(gf.upper_limit || '').trim()) types.push('ability')
  return types.length ? types : ['none']
}

/** 生效类型集合：优先用后端权威结果，其次显式声明，最后本地兜底推断。 */
export function resolveTypes(d) {
  const resolved = normalizeTypes(d?.mechanism_types_resolved)
  if (resolved.length) return resolved
  const declared = normalizeTypes(d?.mechanism_types)
  return declared.length ? declared : inferTypes(d)
}

/** 是否由推断得出（用于界面标注「由既有设定推断」）。 */
export function isInferred(d) {
  const resolved = normalizeTypes(d?.mechanism_types_resolved)
  if (resolved.length) return !normalizeTypes(d?.mechanism_types).length
  return !normalizeTypes(d?.mechanism_types).length
}

/** 数据兜底：世界里**确实存在**某类机制的数据（不参与类型分派，只用于"有数据就让你
 *  看得见、改得动"）。与 resolveTypes 的区别是它不区分后端是否纳入推断口径——
 *  例：只填 scar_end 的旧书后端判 none，但用户面板里那条数据仍要能编辑。 */
export function dataTypes(d) {
  const draft = d || {}
  const types = []
  const mb = draft.mechanism_baseline || {}
  if (Object.values(mb).some(v => String(v ?? '').trim())) types.push('numeric_panel')
  if ((draft.mechanism_ruleset || []).length) types.push('rule_set')
  const gf = draft.goldfinger_rules || {}
  if (Object.values(gf).some(v => String(v ?? '').trim())) types.push('ability')
  const ps = draft.power_system || {}
  if ((ps.levels || []).length || String(ps.protagonist_level || '').trim()
      || String(ps.progression_rule || '').trim()) types.push('power_level')
  return types
}

/** 某个区块是否渲染：类型判定命中，或该类型的数据确实存在（兜底，防止数据改不了）。 */
export function showBlock(d, name) {
  return resolveTypes(d).includes(name) || dataTypes(d).includes(name)
}

/** 「机制初始值」区块是否显示：有任一非 none 类型，或存在机制说明文本/机制数据。 */
export function hasMechanism(d) {
  const types = resolveTypes(d)
  if (types.some(t => t !== 'none')) return true
  if (String(d?.mechanism_rules || '').trim()) return true
  return dataTypes(d).length > 0
}
