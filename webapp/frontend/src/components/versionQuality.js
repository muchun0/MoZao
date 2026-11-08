// R14 单章重检的前端纯函数口径（便于 node --test 直接单测，不依赖 DOM）
// 说明：改写（source=rewrite）生成的新版本没有质检结果，需给用户「未质检」+「重新质检」入口。

/** 是否需要在版本卡片上提供「重新质检」入口（仅改写版且尚无质检结果）。 */
export function needsRecheck(v) {
  return !!v && v.source === 'rewrite' && !v.quality
}

/** 无问题区文案：区分「未质检」与「质检后确实没问题」，避免把空质检说成「无问题」。 */
export function noIssueText(v) {
  if (!v) return ''
  if (!v.quality) {
    return needsRecheck(v) ? '该版本尚未质检（改写生成）' : '该版本未质检，暂无问题记录'
  }
  const score = v.quality.score
  return typeof score === 'number' ? `该版本本次质检未发现问题（${score} 分）` : '该版本本次质检未发现问题'
}

/** 点击前的确认文案：必须如实告知会调用模型、消耗 token、不按字数计费。 */
export function recheckConfirmText(version) {
  return `将对 v${version} 重新质检：会调用模型（读者体验 / 章内一致性 / 规则 / 情节评委，约 3~4 次评审），`
    + '会消耗模型 token；不按字数计费。是否继续？'
}

/** 成功后的提示：复用与真正重跑要区分，避免用户误以为又花了一次模型调用。 */
export function recheckResultText(res) {
  const v = res?.version
  const score = res?.score
  const count = res?.issue_count ?? 0
  if (res?.reused) return `v${v} 已有质检结果（${score} 分、${count} 处问题），本次未重复调用模型`
  return `v${v} 质检完成：${score} 分、${count} 处问题`
}
