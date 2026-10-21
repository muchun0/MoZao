<template>
  <!-- 确定性卡片：全部数值来自后端，前端只做展示，不推导评分 -->
  <div class="a-card" :class="'card-' + card.type">
    <div class="a-card-head">
      <span>{{ card.title }}</span>
      <span v-if="card.type === 'quality'" class="a-card-sub">质检扣分（缺陷分）· 仅反映缺陷扣减，不代表质量</span>
      <span v-if="card.type === 'positive'" class="a-card-sub">警示线参考，非质量评分</span>
    </div>

    <!-- 质检卡 -->
    <template v-if="card.type === 'quality'">
      <div class="a-kv">
        <span>字数：{{ card.words }}</span>
        <span>自动重写：{{ card.auto_revised ? '是' : '否' }}</span>
        <span>质检扣分：{{ card.defect_score ?? '—' }}</span>
        <span>通过：{{ card.passed === true ? '是' : card.passed === false ? '否' : '—' }}</span>
        <span v-if="card.hook_score != null">追更欲：{{ card.hook_score }}/5</span>
      </div>
      <div v-if="!card.has_record" class="a-muted">本章尚无质检记录（未跑质检或旧版本）。</div>
    </template>

    <!-- 问题清单 -->
    <template v-else-if="card.type === 'issues'">
      <div v-for="it in card.items" :key="it.id" class="a-issue">
        <span class="a-issue-id">{{ it.id }}</span>
        <span class="a-issue-lv" :class="lvClass(it.level)">{{ it.level }}</span>
        <span v-if="it.type" class="a-issue-type">[{{ it.type }}]</span>
        <span class="a-issue-detail">{{ it.detail }}</span>
      </div>
    </template>

    <!-- 版本对比 -->
    <template v-else-if="card.type === 'versions'">
      <div class="a-kv">
        <span>当前 v{{ card.current.v }}：{{ card.current.words }} 字 · 扣分 {{ card.current.defect_score ?? '—' }}</span>
        <span>上一版 v{{ card.previous.v }}：{{ card.previous.words }} 字 · 扣分 {{ card.previous.defect_score ?? '—' }}</span>
      </div>
      <div class="a-muted">
        字数变化 {{ signed(card.word_delta) }}
        <template v-if="card.defect_delta != null"> · 扣分变化 {{ signed(card.defect_delta) }}（负值=缺陷减少）</template>
      </div>
    </template>

    <!-- 正向评估 -->
    <template v-else-if="card.type === 'positive'">
      <div v-for="row in card.metrics" :key="row.key" class="a-metric">
        <span class="a-metric-name">{{ row.name }}</span>
        <span class="a-metric-value">{{ row.value }}</span>
        <span class="a-metric-th">{{ row.threshold }}</span>
        <el-tag size="small" :type="row.ok ? 'success' : 'warning'">{{ row.ok ? '达标' : '超限' }}</el-tag>
        <span class="a-metric-hint">{{ row.hint }}</span>
      </div>
      <div v-if="card.zero_direct" class="a-metric">
        <span class="a-metric-name">直白点破</span>
        <span class="a-metric-value">{{ card.zero_direct.ok ? '无' : '有' }}</span>
        <span class="a-metric-hint">{{ card.zero_direct.hint }}</span>
      </div>
      <div v-if="card.goods.length" class="a-goods">
        <div class="a-goods-head">本作范本（手法标签 · 规则粗分）</div>
        <div v-for="(g, i) in card.goods" :key="i" class="a-goods-item">
          <span class="a-goods-cat">{{ g.cat }}</span>
          <span class="a-goods-src">{{ sourceLabel(g) }}</span>
          <div class="a-goods-text">{{ g.text }}</div>
        </div>
      </div>
      <div v-for="(e, i) in card.errors" :key="'e' + i" class="a-muted">注意：{{ e }}</div>
      <div class="a-muted">{{ card.notice }}</div>
    </template>
  </div>
</template>

<script setup>
defineProps({ card: { type: Object, required: true } })

const lvClass = (lv) => (lv === '大改' ? 'lv-big' : lv === '中改' ? 'lv-mid' : 'lv-small')
const signed = (n) => (n > 0 ? '+' + n : String(n))
// 范本出处：本作第 N 章 / 外部范文 / 用户上传
const sourceLabel = (g) => {
  if (g.src_type === 'external') return '外部范文'
  if (g.src_type === 'user') return '用户上传'
  return g.src_ch ? `本作第${g.src_ch}章` : '本作'
}
</script>

<style scoped>
.a-card { border: 1px solid #e4e7ed; border-radius: 8px; padding: 10px 12px; margin-top: 8px; background: #fff; }
.a-card-head { display: flex; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 600; color: #1f3a5f; margin-bottom: 6px; }
.a-card-sub { font-size: 11px; font-weight: 400; color: #909399; }
.a-kv { display: flex; flex-wrap: wrap; gap: 10px; font-size: 12px; color: #606266; }
.a-muted { margin-top: 6px; font-size: 12px; color: #909399; line-height: 1.6; }
.a-issue { display: flex; gap: 6px; font-size: 12px; line-height: 1.7; color: #606266; margin-top: 2px; }
.a-issue-id { color: #909399; flex-shrink: 0; }
.a-issue-lv { flex-shrink: 0; font-weight: 600; }
.lv-big { color: #f56c6c; }
.lv-mid { color: #e6a23c; }
.lv-small { color: #909399; }
.a-issue-type { color: #409eff; flex-shrink: 0; }
.a-issue-detail { flex: 1; }
.a-metric { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #606266; margin-top: 4px; flex-wrap: wrap; }
.a-metric-name { flex: 0 0 108px; color: #1f3a5f; }
.a-metric-value { font-weight: 600; }
.a-metric-th { color: #909399; }
.a-metric-hint { color: #c0c4cc; font-size: 11px; }
.a-goods { margin-top: 8px; padding: 8px 10px; background: #fdf6ec; border: 1px solid #faecd8; border-radius: 6px; }
.a-goods-head { font-size: 12px; font-weight: 600; color: #b88230; margin-bottom: 4px; }
.a-goods-item { font-size: 12px; color: #606266; margin-top: 4px; }
.a-goods-cat { font-weight: 600; margin-right: 6px; }
.a-goods-src { color: #909399; }
.a-goods-text { line-height: 1.7; }
</style>
