// 全书体检展示口径（纯函数，前端与后端 severity 约定保持一致：high/medium/low/info）
// 原则：用户可见文案一律用作者能懂的中文口语，不暴露内部视图代号 / 字段名 / 状态英文键。

export const SEVERITY_ORDER = { high: 3, medium: 2, low: 1, info: 0 }

// 严重度 → 标签样式与作者视角文案
export const SEVERITY_META = {
  high: { label: '必须处理', type: 'danger' },
  medium: { label: '建议处理', type: 'warning' },
  low: { label: '了解一下', type: 'primary' },
  info: { label: '提示', type: 'info' },
}

// 严重度 → 界面分组（high=必须先处理 / medium=建议处理 / 其余=了解一下）
export const SEVERITY_GROUP = { high: 'must', medium: 'suggest', low: 'later', info: 'later' }

export function severityGroup(severity) {
  return SEVERITY_GROUP[severity] || 'later'
}

// 账本覆盖：内部视图代号 → 作者能理解的「设定小抄」名称
export const PROJECTION_VIEW_LABELS = {
  chapter_commit: '章节要点记录',
  chapter_state: '全书进度记录',
  reveal_status: '伏笔回收记录',
  foreshadow_commitment: '伏笔回收计划',
  payoff_quota: '爽点节奏记录',
  summaries: '章节摘要',
  scenes: '场景清单',
  beat_gate: '关键剧情节点',
  character_states: '人物当前状态',
  book_memory: '全书前情提要',
}

export function projectionViewLabel(view) {
  return PROJECTION_VIEW_LABELS[view] || '这项记录'
}

// 覆盖状态 → 作者口语（不再直接显示「同步 / 落后 / 超前 / 未知」）
export const COVERAGE_STATE_LABELS = {
  synced: '跟上了', lagging: '落后了', ahead: '超前了', unknown: '还没记录',
}
export const COVERAGE_STATE_TYPES = {
  synced: 'success', lagging: 'danger', ahead: 'warning', unknown: 'info',
}

export function coverageStateLabel(status) {
  return COVERAGE_STATE_LABELS[status] || '还没记录'
}

export function coverageStateType(status) {
  return COVERAGE_STATE_TYPES[status] || 'info'
}

// 伏笔状态 → 作者口语（后端仍返回 state / state_label，前端只换展示口径）
export const FORESHADOW_STATE_LABELS = {
  overdue: '早该收了',
  stalled: '很久没推进',
  due_soon: '近期该收',
  planted: '已埋下',
  revealed: '已经收了',
}

export function foreshadowStateLabel(state) {
  return FORESHADOW_STATE_LABELS[state] || '已埋下'
}

// 伏笔筛选选项（顺序即「先处理要紧的」）
export const FORESHADOW_STATES = [
  { value: 'overdue', label: '早该收了' },
  { value: 'stalled', label: '很久没推进' },
  { value: 'due_soon', label: '近期该收' },
  { value: 'planted', label: '已埋下' },
  { value: 'revealed', label: '已经收了' },
]

export const ENTITY_KIND_OPTIONS = [
  { value: 'character', label: '人物' },
  { value: 'location', label: '地点' },
  { value: 'faction', label: '势力' },
  { value: 'item', label: '物件' },
  { value: 'enemy', label: '生物' },
  { value: 'term', label: '术语' },
]

export function severityMeta(severity) {
  return SEVERITY_META[severity] || SEVERITY_META.info
}

// 伏笔：一句话问题（界面三要素之一「问题是什么」）
export function foreshadowProblem(item = {}) {
  const name = item.name || '这条线索'
  switch (item.state) {
    case 'overdue':
      return `「${name}」埋下后一直没交代，已经超过原计划回收的时间`
    case 'stalled':
      return `「${name}」埋下后很久没再提到，读者可能已经忘了`
    case 'due_soon':
      return `「${name}」快到该收的时候了，别再往后拖`
    case 'revealed':
      return `「${name}」已经在正文里收掉了`
    default:
      return `「${name}」已埋下，目前按计划推进`
  }
}

// 伏笔：位置（三要素之二）
export function foreshadowWhere(item = {}) {
  const parts = []
  if (item.buried_ch) parts.push(`埋在第 ${item.buried_ch} 章`)
  if (item.reveal_ch) parts.push(`原计划第 ${item.reveal_ch} 章收`)
  if (item.revealed_ch) parts.push(`第 ${item.revealed_ch} 章已收`)
  return parts.join(' · ') || '埋设位置还没记录'
}

// 伏笔：建议怎么处理（三要素之三）
export function foreshadowAdvice(item = {}) {
  switch (item.state) {
    case 'overdue':
      return '翻回最早埋下它的那一章，找机会收掉；如果这条线本来就不打算用了，就在后面章节里明说放弃或转成别的用途，免得越拖越像烂尾。'
    case 'stalled':
      return '在接下来一两章里让它再露一次面，哪怕只提一句，读者才不会把它忘掉。'
    case 'due_soon':
      return '接下来两三章里找个时机收掉，别拖过原计划。'
    case 'revealed':
      return '已经收掉了，不用再管。'
    default:
      return '不用动，按计划收就行。'
  }
}

// ---------- 设定小抄覆盖：缺口事实与影响 ----------

// 某一项「小抄」缺的到底是什么（用于「第 X 章的…还没记进去」）
export const COVERAGE_MISSING_SUBJECT = {
  chapter_commit: '章节要点',
  summaries: '章节摘要',
  scenes: '场景',
  beat_gate: '剧情节点',
}

// 某一项落后会影响什么（作者视角的后果，不是系统术语）
export const COVERAGE_VIEW_EFFECT = {
  chapter_commit: '后面写新章节时缺少「这一章发生过什么」的依据，容易接不上前文。',
  summaries: '写后面的章节时看不到这几章发生过什么，容易把已经处理过的事再写一遍。',
  scenes: '后面写新章节时可能重复用同一类场景，或漏掉应有的过场。',
  beat_gate: '后面写章节时摸不准节奏，可能连着几章都很平。',
  chapter_state: '对全书进度的判断会失准，可能提前或重复推进剧情。',
  foreshadow_commitment: '伏笔该收的时间点会失准，容易拖着不收。',
  payoff_quota: '节奏记录会失真，后面可能连着多章没有起伏。',
  reveal_status: '可能重复回收同一条伏笔，或者该收的忘了收。',
  character_states: '人物状态会停在旧版本，容易写崩人设或关系。',
  book_memory: '写新章节时对前情把握不准，容易和前面矛盾。',
}

// ---------- 数据可见性决策（2026-11-05） ----------
// R2 结论依赖的底层记录，作者在界面上到底能不能看到、能不能自己维护。
// show：体检页直接列出条目并可跳章（A 补展示）；degrade：系统内部记录，只作参考，不进「必须先处理」（C 保留降级）。
export const COVERAGE_VISIBILITY = {
  scenes: {
    decision: 'show',
    hint: '系统每写完一章自动记下的场景清单。界面上没有单独的清单页，你不用手动维护；下面就是它记下的内容。',
  },
  summaries: {
    decision: 'show',
    hint: '系统每写完一章自动写下的这一章摘要。界面上没有单独的摘要页，你不用手动维护；下面就是它记下的内容。',
  },
  chapter_commit: {
    decision: 'degrade',
    hint: '这是系统内部的章节要点账本，界面上不展示，也不用你维护。',
  },
  beat_gate: {
    decision: 'degrade',
    hint: '这是系统内部用来自检节奏的剧情节点记录，界面上不展示，也不用你维护。',
  },
  chapter_state: {
    decision: 'degrade',
    hint: '这是系统内部的全书进度水位，界面上不展示，也不用你维护。',
  },
  foreshadow_commitment: {
    decision: 'degrade',
    hint: '这是系统内部的伏笔回收计划，界面上只在「伏笔回收」里看得到结果，你不用手动维护。',
  },
  payoff_quota: {
    decision: 'degrade',
    hint: '这是系统内部的爽点节奏记录，界面上不展示，也不用你维护。',
  },
  reveal_status: {
    decision: 'degrade',
    hint: '这是系统内部的伏笔揭示记录，界面上不展示，也不用你维护。',
  },
}

export function coverageVisibility(row = {}) {
  return COVERAGE_VISIBILITY[row.view] || {
    decision: 'degrade',
    hint: '这是系统内部记录，界面上不展示，也不用你维护。',
  }
}

// 只有作者能看到内容、且缺了会影响写作的记录，才进「必须先处理」
export function coverageShowInMust(row = {}) {
  return coverageVisibility(row).decision === 'show'
}

export function coverageWhatText(row = {}) {
  return coverageVisibility(row).hint
}

// 章号压缩： [5,7] → "5、7"；[5,6,7] → "5–7"；[5,7,8,9] → "5、7–9"
export function formatChapterList(numbers = []) {
  const list = [...new Set((numbers || []).map(Number).filter((n) => Number.isFinite(n) && n > 0))]
    .sort((a, b) => a - b)
  if (!list.length) return ''
  const parts = []
  let start = list[0]
  let prev = list[0]
  const push = (a, b) => parts.push(a === b ? `${a}` : `${a}–${b}`)
  for (let i = 1; i < list.length; i += 1) {
    const n = list[i]
    if (n === prev + 1) { prev = n; continue }
    push(start, prev)
    start = n
    prev = n
  }
  push(start, prev)
  return parts.join('、')
}

// 已经把第几章写完（章号从 1 起）
function writtenRange(baseline) {
  const n = Math.max(0, Number(baseline) || 0)
  if (n <= 0) return '还没有写完的章节'
  return n === 1 ? '第 1 章已写' : `第 1–${n} 章已写`
}

// 覆盖项一句话事实：能逐章定位就给具体章号，不能就明说只能看到落后多少
export function coverageGap(row = {}, baseline = 0) {
  const name = projectionViewLabel(row.view)
  const subject = COVERAGE_MISSING_SUBJECT[row.view] || '记录'
  const written = writtenRange(baseline)
  const missing = row.missing_chapters || []
  const count = Number(row.missing_count) || 0

  if (row.locatable) {
    if (!count) {
      if (row.status === 'ahead') {
        return `${written}，这份${name}已经记到第 ${row.covered_ch} 章，属于旧记录，不影响阅读`
      }
      return `${written}，这份${name}逐章都有记录，和正文对得上`
    }
    if (count >= (Number(baseline) || 0)) {
      return `${written}；这份${name}还是空的，第 1–${baseline} 章都没有记录`
    }
    // 缺口正好落在「已记录章号之后」时，用「只更新到第 X 章」更贴近用户直觉
    const isSuffix = row.covered_ch && missing.length && Number(missing[0]) === Number(row.covered_ch) + 1
    const lead = isSuffix
      ? `这份${name}只更新到第 ${row.covered_ch} 章，`
      : `这份${name}里，`
    return `${written}；${lead}第 ${formatChapterList(missing)} 章的${subject}还没记进去（共缺 ${count} 章）`
  }
  if (row.status === 'ahead') {
    return `${written}，这份${name}已经记到第 ${row.covered_ch} 章，属于旧记录，不影响阅读`
  }
  if (row.status === 'synced') {
    return `${written}，这份${name}逐章都有记录，和正文对得上`
  }
  if (count > 0) {
    return `${written}，这一项只更新到第 ${row.covered_ch} 章，落后 ${count} 章；系统只知道它落后 ${count} 章，无法定位具体缺哪几章`
  }
  return `${written}，但这一项没有记录更新到第几章，系统无法判断缺了哪几章`
}

// 覆盖项的数据来源（可核对：这份记录是哪一步写下的）
export function coverageSource(row = {}) {
  return (row.record_source || '').trim()
}

// 覆盖项影响与处理建议
export function coverageEffect(row = {}) {
  return COVERAGE_VIEW_EFFECT[row.view] || '后面写新章节时参考不到这几章的记录。'
}

export function coverageAdvice(row = {}) {
  if (row.status === 'synced' || row.status === 'ahead') return '不用处理。'
  // 内部记录：作者看不到也改不了，不给「你去补记」这类做不到的建议
  if (!coverageShowInMust(row)) {
    return '不用管，也不需要你补。系统写下一章时会自己续上；如果它一直不动，可以在「工具 → 生成过程」里看下一章有没有正常更新。'
  }
  // 可见记录：缺的是已写完的章节，系统不会回头补，只能靠重新生成该章
  return '系统在你写下一章时会自动继续记。已经写完的章节不会回头补；要让这几章补上，只能打开对应章节重新生成（会产生正常的生成消耗）。'
}

// ---------- 一致性冲突：依据 / 预期 vs 实际 / 判定理由 ----------

// 来源换成作者能理解的部门，不显示内部 source 键
export function sourceLabel(source) {
  return source === 'verify' ? '全书通读检查' : '单章质检'
}

export function conflictCategory(item = {}) {
  if (item.category_label) return item.category_label
  if (item.category === 'repetition') return '雷同描写'
  if (item.category === 'insufficient') return '依据不足'
  return '前后矛盾'
}

// 标题：雷同类不复述两段原文，直接说清「是重复、不是设定矛盾」
export function conflictHeadline(item = {}) {
  if (item.category === 'repetition') return '两处同类动作写得几乎一样'
  if (item.category === 'duplicate_event') return '同一件事写了两遍'
  if (item.category === 'advisory') {
    return item.category_label === '文风/表达提醒'
      ? '这是一条文风提醒，不是设定矛盾'
      : '这是一条阅读/节奏建议'
  }
  if (item.category === 'insufficient') return '这条质检只写了结论，没给原文依据'
  return item.detail || '有一处前后对不上'
}

// 计数口径：把后端 sources 翻成作者能自己核对的一句话（来源不同，数字本来就不必相等）
export function conflictSourceText(sources = {}) {
  const verify = Number(sources?.verify?.included) || 0
  const quality = Number(sources?.chapter_quality?.included) || 0
  const merged = Number(sources?.merged?.total) || 0
  const deduped = Number(sources?.merged?.deduped) || 0
  const skipped = Number(sources?.verify?.skipped) || 0
  const parts = [`全书校验 ${verify} 条`, `单章质检 ${quality} 条`,
    deduped ? `合并去重后 ${merged} 条` : `合并后 ${merged} 条`]
  if (skipped) parts.push(`另有 ${skipped} 条无法定位章号`)
  return parts.join(' · ')
}

// 依据来源：检查记录 + 对照的设定条目（含设定原文）
export function conflictBasis(item = {}) {
  const out = []
  for (const b of item.basis || []) {
    if (b?.kind === 'setting') out.push({ kind: 'setting', name: b.name || '', text: `设定「${b.name || '未命名'}」`, excerpt: b.excerpt || '' })
    else out.push({ kind: 'check', name: b?.name || '检查记录', text: b?.name || '检查记录', excerpt: b?.excerpt || '' })
  }
  return out
}

// 预期 vs 实际的标签：雷同类讲「前一处/后一处」，真冲突讲「设定要求/正文写法」
export function conflictSideLabels(item = {}) {
  if (item.category === 'repetition') return { expected: '前一处', actual: '后一处' }
  return { expected: '设定要求', actual: '正文写法' }
}

export function conflictHasSides(item = {}) {
  return Boolean((item.expected || '').trim() || (item.actual || '').trim())
}

// 预期 vs 实际的可展示对：没有「预期」就不硬凑对照，避免只重复问题描述
export function conflictSides(item = {}) {
  const label = conflictSideLabels(item)
  const expected = (item.expected || '').trim()
  const actual = (item.actual || '').trim()
  if (!expected) return null
  return {
    expectedLabel: label.expected, expected,
    actualLabel: label.actual, actual,
  }
}

// 判定理由：没有 reason 的条目按「依据不足」处理，不冒充冲突
export function conflictReason(item = {}) {
  const reason = (item.reason || '').trim()
  if (reason) return reason
  return '这条记录没有写判定依据，系统无法确认它是不是真的冲突，先不当作冲突。'
}

export const CONFLICT_ADVICE_FALLBACK =
  '打开这一章核对，把说法统一成你现在的设定；如果设定本身改了，记得顺手更新世界观。'

export function conflictAdvice(item = {}) {
  if (item.category === 'repetition') {
    return '这不是设定矛盾，是写法重复。检查是不是给不同人物用了同一套动作模板：如果只是巧合可以不改；想让人物有区分度，给其中一处换一个标志性动作。'
  }
  if (item.category === 'duplicate_event') {
    return '这不是设定矛盾，是同一件事被写了两遍。删掉或合并其中一处，保留信息更完整的那一版；两处都要留就把后一处改成新的推进。'
  }
  if (item.category === 'advisory') {
    return (item.suggestion || '').trim() || '按建议酌情调整；不改也不影响设定一致性。'
  }
  if (item.category === 'insufficient') {
    return '先按「了解一下」看，不当作冲突。打开这一章核对原文；确认有问题后，在单章质检里补上原文对照，下次体检才能给出依据。'
  }
  return (item.suggestion || '').trim() || CONFLICT_ADVICE_FALLBACK
}

// 涉及人物/章节：把匹配到的实体名字列出来，没匹配到就明说
export function conflictScope(item = {}) {
  const names = (item.entities || []).filter(Boolean)
  const chapter = item.chapter ? `第 ${item.chapter} 章` : '章节未记录'
  return names.length ? `${names.join('、')} · ${chapter}` : `未匹配到具体人物 · ${chapter}`
}

// 检查项（具体是哪种检查判出来的）
export function conflictCheckType(item = {}) {
  return (item.check_type || item.type || '').trim()
}

// 人物/设定索引：一句话摘要（出现次数 + 覆盖区间；零命中明确说是「还没用上」）
export function entitySummary(row = {}) {
  const chapters = row.chapters || []
  if (!chapters.length) {
    return '正文里还没出现过——设定写了但还没用上，后续可以留意'
  }
  const first = row.first_ch ?? chapters[0].no
  const last = row.last_ch ?? chapters[chapters.length - 1].no
  const span = first === last ? `只在第 ${first} 章出现` : `第 ${first}–${last} 章都有`
  return `共出现 ${row.total_mentions || 0} 次，${span}`
}

// 后端 data_gaps 措辞偏系统：按内容改写成作者能懂的一句话；未知内容用兜底，不原样回显
export function humanizeGap(gap = '') {
  if (gap.includes('语义级')) {
    return '这里只汇总已有的通读检查和单章质检记录，不会逐句重读全书；没被记下来的矛盾，这里看不到。'
  }
  if (gap.includes('未记录')) {
    return '某一项显示「还没记录」时，说明这个环节还没生成数据，不代表全书没问题。'
  }
  return '有一小部分数据暂时没取到，结果可能不完整。'
}

// 按严重度降序排序（不改原数组；同严重度保持后端顺序，便于稳定浏览）
export function sortBySeverity(items = []) {
  return [...items].sort(
    (a, b) => (SEVERITY_ORDER[b?.severity] || 0) - (SEVERITY_ORDER[a?.severity] || 0)
  )
}

// 统计某一严重度的条目数（首屏徽标用）
export function countSeverity(items = []) {
  const out = { high: 0, medium: 0, low: 0, info: 0 }
  for (const it of items) {
    const key = SEVERITY_ORDER[it?.severity] ? it.severity : 'info'
    out[key] += 1
  }
  return out
}

// 章节跳转文案：未写到的章节不假装可跳转
export function chapterJumpLabel(no) {
  return no ? `第 ${no} 章` : '未记录'
}
