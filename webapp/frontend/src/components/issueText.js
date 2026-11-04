// R1 改稿建议展示口径：证据只认真实原文引用，改法缺失时明确留白，均不编造。
export const EVIDENCE_EMPTY_TEXT = '未记录原文'
export const FIX_EMPTY_TEXT = '未给出改法'

function _text(value) {
  return String(value || '').trim()
}

/** 问题卡「证据」：evidence 缺失或与 detail 相同（问题描述冒充原文）时留白。 */
export function evidenceInfo(issue) {
  const evidence = _text(issue && issue.evidence)
  const detail = _text(issue && issue.detail)
  if (!evidence || evidence === detail) {
    return { text: EVIDENCE_EMPTY_TEXT, empty: true }
  }
  return { text: evidence, empty: false }
}

/** 问题卡「改法」：fix_suggestion 缺失时留白。 */
export function fixInfo(issue) {
  const fix = _text(issue && issue.fix_suggestion)
  if (!fix) return { text: FIX_EMPTY_TEXT, empty: true }
  return { text: fix, empty: false }
}
