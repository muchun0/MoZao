<template>
  <div>
    <h2>个人中心</h2>
    <el-tabs v-model="tab">
      <!-- ============ 概览 ============ -->
      <el-tab-pane label="概览" name="overview">
        <el-row :gutter="16">
          <el-col :span="8">
            <el-card class="user-card">
              <div class="avatar">{{ (me.username || '?').slice(0, 1) }}</div>
              <div class="uname">{{ me.username }}</div>
              <div class="muted">{{ me.email || '未绑定邮箱' }}</div>
              <div class="muted" style="margin-top:6px">邀请码：<b>{{ me.invite_code || '-' }}</b></div>
            </el-card>
          </el-col>
          <el-col :span="16">
            <el-row :gutter="16">
              <el-col :span="6"><el-card class="stat-card"><div class="stat-val">¥{{ (me.balance ?? 0).toFixed(2) }}</div><div class="stat-label">余额</div></el-card></el-col>
              <el-col :span="6"><el-card class="stat-card"><div class="stat-val">¥{{ (refundable ?? 0).toFixed(2) }}</div><div class="stat-label">可退金额</div></el-card></el-col>
              <el-col :span="6"><el-card class="stat-card"><div class="stat-val">¥{{ (referral.total_commission ?? 0).toFixed(2) }}</div><div class="stat-label">累计返佣</div></el-card></el-col>
              <el-col :span="6"><el-card class="stat-card"><div class="stat-val">{{ referral.downline_count ?? 0 }}</div><div class="stat-label">下级数</div></el-card></el-col>
            </el-row>
            <el-alert v-if="me.is_agent" type="success" :closable="false" style="margin-top:12px">
              你是代理用户，享受 <b>{{ Math.round((me.agent_discount ?? 1) * 100) }}%</b> 消费折扣，推广返佣比例 {{ Math.round((referral.commission_rate ?? 0) * 100) }}%
            </el-alert>
            <el-alert v-else type="info" :closable="false" style="margin-top:12px">
              邀请好友注册可获返佣，详见「推广返佣」tab
            </el-alert>
          </el-col>
        </el-row>

        <el-row :gutter="16" style="margin-top:16px">
          <el-col :span="14">
            <el-card>
              <template #header>
                <div class="ov-head">
                  <b>最近作品</b>
                  <el-button link type="primary" @click="$router.push('/')">全部作品</el-button>
                </div>
              </template>
              <div v-if="recentProjects.length" class="ov-list">
                <div v-for="p in recentProjects" :key="p.id" class="ov-row" @click="$router.push(`/project/${p.id}/write`)">
                  <span class="ov-name">{{ p.title }}</span>
                  <span class="muted">{{ recentProjectMeta(p) }}</span>
                </div>
              </div>
              <div v-else class="muted" style="padding:8px 0">还没有作品，去书架新建一本。</div>
            </el-card>
          </el-col>
          <el-col :span="10">
            <el-card>
              <template #header>
                <div class="ov-head">
                  <b>最近账务</b>
                  <el-button link type="primary" @click="tab = 'account'">查看账单</el-button>
                </div>
              </template>
              <div v-if="recentBills.length" class="ov-list">
                <div v-for="b in recentBills" :key="b.key" class="ov-row">
                  <span>{{ b.label }}</span>
                  <span class="muted">{{ b.time ? fmtTime(b.time) : '' }}</span>
                </div>
              </div>
              <div v-else class="muted" style="padding:8px 0">暂无收支记录。</div>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ============ 账户与账单 ============ -->
      <el-tab-pane label="账户与账单" name="account">
        <el-row :gutter="16" style="margin-bottom:12px">
          <el-col :span="6"><el-card class="stat-card"><div class="stat-val">¥{{ (me.balance ?? 0).toFixed(2) }}</div><div class="stat-label">余额</div></el-card></el-col>
          <el-col :span="6"><el-card class="stat-card"><div class="stat-val">¥{{ (refundable ?? 0).toFixed(2) }}</div><div class="stat-label">可退金额 <span class="stat-sub">仅充值部分，赠金/返佣不可退</span></div></el-card></el-col>
          <el-col :span="12" style="display:flex;align-items:center;gap:10px">
            <el-button type="primary" @click="rechargeRef.open()">充值</el-button>
            <el-tooltip :disabled="(refundable || 0) > 0" content="当前没有可退金额（只退充值部分，赠金/返佣不可退）" placement="top">
              <span><el-button :disabled="(refundable || 0) <= 0" @click="openRefund">申请退款</el-button></span>
            </el-tooltip>
          </el-col>
        </el-row>

        <el-card style="margin-bottom:12px">
          <template #header><b>收支流水</b></template>
          <el-table :data="recharges" stripe size="small" empty-text="还没有收支记录">
            <el-table-column label="类型" width="110">
              <template #default="{ row }">{{ billType(row) }}</template>
            </el-table-column>
            <el-table-column prop="amount" label="金额" width="120">
              <template #default="{ row }">
                <span :class="row.amount < 0 ? 'bill-out' : 'bill-in'">
                  {{ row.amount < 0 ? '-' : '+' }}¥{{ Math.abs(row.amount || 0).toFixed(2) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" />
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card>
          <template #header><b>我的退款申请</b></template>
          <el-table :data="refundRequests" stripe size="small" empty-text="还没有退款申请">
            <el-table-column prop="amount" label="金额" width="120">
              <template #default="{ row }">¥{{ row.amount.toFixed(2) }}</template>
            </el-table-column>
            <el-table-column prop="reason" label="原因" />
            <el-table-column prop="status" label="状态" width="100" />
            <el-table-column prop="created_at" label="时间" width="180">
              <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <!-- ============ 推广返佣 ============ -->
      <el-tab-pane label="推广返佣" name="referral">
        <el-card style="margin-bottom:12px">
          <template #header><b>我的推广</b></template>
          <div class="ref-row">
            <span>邀请码：<b>{{ referral.invite_code || '-' }}</b></span>
            <el-button size="small" @click="copyInvite">复制邀请码</el-button>
          </div>
          <div class="ref-row">
            <span>邀请链接：<span class="link">{{ inviteLink }}</span></span>
            <el-button size="small" @click="copyInviteLink">复制链接</el-button>
          </div>
          <el-alert v-if="!me.referrer_id" type="warning" :closable="false" style="margin-top:10px">
            你还没有绑定上级。输入邀请码可绑定（仅一次）：
            <el-input v-model="inviteInput" placeholder="上级邀请码" size="small" style="width:220px;margin:0 8px" />
            <el-button size="small" type="primary" @click="doBindReferrer">绑定</el-button>
          </el-alert>
          <el-alert v-else type="success" :closable="false" style="margin-top:10px">已绑定上级</el-alert>
        </el-card>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-card>
              <template #header><b>我的下级（{{ referral.downline_count ?? 0 }}）</b></template>
              <el-table :data="referral.downline || []" stripe size="small" empty-text="还没有下级">
                <el-table-column prop="username" label="用户" />
                <el-table-column prop="created_at" label="时间" width="180">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
          <el-col :span="12">
            <el-card>
              <template #header><b>返佣明细</b></template>
              <el-table :data="referral.commissions || []" stripe size="small" empty-text="还没有返佣记录">
                <el-table-column prop="amount" label="金额" width="100">
                  <template #default="{ row }">¥{{ row.amount.toFixed(2) }}</template>
                </el-table-column>
                <el-table-column prop="kind" label="类型" width="100" />
                <el-table-column prop="note" label="备注" />
                <el-table-column prop="created_at" label="时间" width="170">
                  <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                </el-table-column>
              </el-table>
            </el-card>
          </el-col>
        </el-row>
      </el-tab-pane>

      <!-- ============ 模型配置 ============ -->
      <el-tab-pane label="模型配置" name="llm">
        <el-card>
          <template #header><b>我的模型配置（BYOK）</b></template>
          <p class="muted" style="margin-bottom:12px">
            配置自备 Key 后，生成走你的 Key，<b>平台不扣费</b>；不配置则按 AI 实际生成字数从余额扣费（统一 3 元/万字）。
          </p>
          <el-button type="primary" @click="llmRef.open()">配置 / 修改模型</el-button>
        </el-card>
      </el-tab-pane>

      <!-- ============ 技能管理（我的/平台/市场 统一入口，REQ-技能统一 9.7） ============ -->
      <el-tab-pane label="技能管理" name="skills">
        <el-card>
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <b>我的自定义技能</b>
              <el-button size="small" type="primary" @click="showSkillDlg = true">+ 新建技能</el-button>
            </div>
          </template>
          <div v-for="s in mySkills" :key="s.id" class="fs-row">
            <div>
              <b>{{ s.name }}</b> <el-tag size="small" type="success">我的</el-tag>
              <div class="muted" style="font-size:12px">{{ s.description }}</div>
              <div class="muted" style="font-size:12px">角色：{{ roleLabel(s) }} · 适用阶段：{{ stageLabel(s) }} · 发布：{{ s.market_id ? '已上架市场' : '未上架' }}</div>
            </div>
            <span style="display:flex;gap:6px">
              <el-button v-if="!s.market_id" size="small" link type="primary" @click="publishSkill(s)">发布到市场</el-button>
              <el-button size="small" link type="danger" @click="delMySkill(s.id)">删</el-button>
            </span>
          </div>
          <div v-if="!mySkills.length" class="muted" style="padding:12px 0">还没有自定义技能，点右上角新建</div>
        </el-card>

        <el-card style="margin-top:12px">
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <b>平台技能（平台内置）</b>
              <el-button size="small" link @click="loadPlatformSkills()">刷新</el-button>
            </div>
          </template>
          <p class="muted" style="font-size:12px;margin-bottom:8px">平台维护的写作技法，只读浏览；可在「风格角色」中挂载，或建书时直接选用。</p>
          <div v-for="s in platformSkills" :key="s.id" class="fs-row">
            <div>
              <b>{{ s.name }}</b> <el-tag size="small" type="info">平台</el-tag>
              <div class="muted" style="font-size:12px">{{ s.description }}</div>
              <div class="muted" style="font-size:12px">角色：{{ roleLabel(s) }} · 适用阶段：{{ stageLabel(s) }}</div>
            </div>
            <el-button size="small" link type="primary" @click="tab = 'style'">去挂载</el-button>
          </div>
          <div v-if="!platformSkills.length" class="muted" style="padding:12px 0">暂无平台技能</div>
        </el-card>

        <el-card style="margin-top:12px">
          <template #header><b>技能市场（安装）</b></template>
          <div v-for="m in marketSkills" :key="m.id" class="fs-row">
            <div>
              <b>{{ m.name }}</b> <el-tag size="small" type="warning">市场</el-tag>
              <div class="muted" style="font-size:12px">{{ m.description }}</div>
              <div class="muted" style="font-size:12px">角色：{{ roleLabel(m) }} · 适用阶段：{{ stageLabel(m) }} · by {{ m.author_name || '匿名' }} · 下载 {{ m.downloads }}</div>
            </div>
            <el-button v-if="m.installed" size="small" disabled>已安装</el-button>
            <el-button v-else size="small" type="primary" @click="installSkill(m)">安装</el-button>
          </div>
          <div v-if="!marketSkills.length" class="muted" style="padding:12px 0">市场还没有技能</div>
        </el-card>
      </el-tab-pane>

      <!-- ============ 风格角色 ============ -->
      <el-tab-pane label="风格角色" name="roles">
        <el-card>
          <template #header>
            <div style="display:flex;justify-content:space-between;align-items:center">
              <b>我的风格角色</b>
              <el-button size="small" type="primary" @click="openRoleDlg()">+ 新建风格角色</el-button>
            </div>
          </template>
          <div v-for="r in styleRoles" :key="r.id" class="fs-row">
            <div>
              <b>{{ r.name }}</b>
              <small class="muted">{{ r.user_id ? '（我的）' : '（平台）' }}</small>
              <div v-if="r.reference_text" class="muted" style="font-size:12px">参考文风：{{ r.reference_text.slice(0, 50) }}{{ r.reference_text.length > 50 ? '…' : '' }}</div>
              <div v-if="(r.skills || []).length" style="margin-top:4px">
                <el-tag v-for="s in r.skills" :key="s.skill_type + '-' + s.skill_id" size="small" type="info" style="margin-right:4px">
                  {{ s.skill_name }}
                </el-tag>
              </div>
              <div v-else class="muted" style="font-size:12px">未挂载技能</div>
            </div>
            <span v-if="r.user_id" style="display:flex;gap:6px">
              <el-button size="small" link type="primary" @click="openRoleDlg(r)">挂载 / 编辑</el-button>
              <el-button size="small" link type="danger" @click="delRole(r)">删</el-button>
            </span>
            <el-tooltip v-else content="平台角色是公共配置，不能直接改；复制一份成「我的角色」后即可挂载技能" placement="top">
              <el-button size="small" link type="primary" @click="copyPlatformRole(r)">复制后挂载</el-button>
            </el-tooltip>
          </div>
          <div v-if="!styleRoles.length" class="muted" style="padding:12px 0">还没有风格角色</div>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 新建技能弹窗 -->
    <el-dialog v-model="showSkillDlg" title="新建自定义技能" width="560px" append-to-body>
      <el-form :model="skillForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="skillForm.name" placeholder="如：克制冷峻风格" /></el-form-item>
        <el-form-item label="适用角色">
          <el-select v-model="skillForm.role" placeholder="全部角色" clearable style="width:100%">
            <el-option label="全部角色" value="" />
            <el-option label="调研(Researcher)" value="researcher" />
            <el-option label="架构师(Architect)" value="architect" />
            <el-option label="写手(Writer)" value="writer" />
            <el-option label="审校(Reviewer)" value="reviewer" />
            <el-option label="读者(Reader)" value="reader" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用阶段">
          <el-select v-model="skillForm.stage_list" multiple clearable placeholder="不选=全部阶段" style="width:100%">
            <el-option label="调研(research)" value="research" />
            <el-option label="大纲(outline)" value="outline" />
            <el-option label="世界观(world)" value="world" />
            <el-option label="正文(chapter)" value="chapter" />
            <el-option label="审校(review)" value="review" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述"><el-input v-model="skillForm.description" placeholder="一句话说明这个Skill的作用" /></el-form-item>
        <el-form-item label="提示词内容">
          <el-input v-model="skillForm.prompt_append" type="textarea" :rows="6" placeholder="写给AI的写作要求" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showSkillDlg = false">取消</el-button>
        <el-button type="primary" @click="saveMySkill">保存</el-button>
      </template>
    </el-dialog>

    <!-- 风格角色弹窗 -->
    <el-dialog v-model="showRoleDlg" :title="roleForm.id ? '编辑风格角色' : '新建风格角色'" width="620px" append-to-body>
      <el-form :model="roleForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="roleForm.name" placeholder="如：乌贼·克苏鲁悬疑克制风" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="roleForm.description" placeholder="一句话说明这个风格的调性" /></el-form-item>
        <el-form-item label="人设提示词">
          <el-input v-model="roleForm.role_prompt" type="textarea" :rows="6"
            placeholder="写给AI的文风总纲：叙事视角、句式节奏、氛围营造、克制边界…" />
        </el-form-item>
        <el-form-item label="参考文风">
          <el-input v-model="roleForm.reference_text" type="textarea" :rows="4"
            placeholder="贴 2-3 段你喜欢的范文片段（同题材最佳），AI 会模仿其选词、句式与节奏，不抄袭内容（P2-1）" />
        </el-form-item>
        <el-form-item label="挂载技能">
          <div v-for="(sk, i) in roleForm.skills" :key="i" class="fs-row">
            <span>{{ sk.skill_type === 'user' ? '📝' : '⭐' }} {{ sk.skill_name }}
              <small class="muted">{{ sk.skill_type === 'user' ? '我的技能' : '平台技能' }}</small>
            </span>
            <el-button size="small" link type="danger" @click="roleForm.skills.splice(i, 1)">卸载</el-button>
          </div>
          <el-select v-model="newMount" placeholder="选择要挂载的技能" clearable filterable style="width:100%;margin-top:6px">
            <el-option-group label="我的Skills">
              <el-option v-for="s in mySkills" :key="'user-' + s.id" :label="s.name" :value="'user:' + s.id" />
            </el-option-group>
            <el-option-group label="平台技能">
              <el-option v-for="s in platformSkills" :key="'admin-' + s.id" :label="s.name" :value="'admin:' + s.id" />
            </el-option-group>
          </el-select>
          <el-button size="small" style="margin-top:6px" @click="addMount">挂载</el-button>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showRoleDlg = false">取消</el-button>
        <el-button type="primary" @click="saveRole">保存</el-button>
      </template>
    </el-dialog>

    <!-- 申请退款弹窗 -->
    <el-dialog v-model="refundDialog" title="申请退款" width="480px" append-to-body>
      <el-form :model="refundForm" label-width="80px">
        <el-form-item label="退款金额">
          <el-input-number v-model="refundForm.amount" :min="0.01" :max="refundable || 0.01" :precision="2" />
        </el-form-item>
        <el-form-item label="退款原因">
          <el-input v-model="refundForm.reason" type="textarea" :rows="3" placeholder="请说明退款原因" />
        </el-form-item>
        <el-alert type="warning" :closable="false" style="margin-bottom:10px">
          可退 {{ (refundable ?? 0).toFixed(2) }} 元（仅充值部分，赠金/返佣不可退）
        </el-alert>
      </el-form>
      <template #footer>
        <el-button @click="refundDialog = false">取消</el-button>
        <el-button type="primary" @click="doSubmitRefund">提交申请</el-button>
      </template>
    </el-dialog>

    <RechargeDialog ref="rechargeRef" />
    <LlmSettingsDialog ref="llmRef" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as meApi from '../api/me'
import * as toolsApi from '../api/tools'
import { listProjects } from '../api/project'
import RechargeDialog from '../components/RechargeDialog.vue'
import LlmSettingsDialog from '../components/LlmSettingsDialog.vue'

const tab = ref('overview')
const me = ref({})
const refundable = ref(0)
const recharges = ref([])
const refundRequests = ref([])
const referral = ref({})
const inviteInput = ref('')
const inviteLink = computed(() => `https://mozao.click/?invite=${referral.value.invite_code || ''}`)

const rechargeRef = ref(null)
const llmRef = ref(null)
const recentProjects = ref([])
// 账目类型：金额符号 + 备注关键字，避免把生成扣费一律写成「充值」
function billType(row) {
  const note = String(row?.note || '')
  const amount = Number(row?.amount || 0)
  if (note.includes('退款')) return amount >= 0 ? '退款退回' : '退款支出'
  if (note.includes('返佣') || note.includes('推广')) return '推广返佣'
  if (amount < 0) return '生成扣费'
  return '充值'
}
const recentBills = computed(() => {
  const items = []
  for (const r of (recharges.value || []).slice(0, 4)) {
    const type = billType(r)
    const amount = Math.abs(Number(r.amount || 0)).toFixed(2)
    const sign = Number(r.amount || 0) < 0 ? '-' : '+'
    items.push({ key: `rc-${r.id ?? r.created_at}`, label: `${type} ${sign}¥${amount}`, time: r.created_at })
  }
  for (const r of (refundRequests.value || []).slice(0, 3)) {
    items.push({ key: `rf-${r.id ?? r.created_at}`, label: `退款申请 ¥${(r.amount || 0).toFixed(2)}（${r.status || '处理中'}）`, time: r.created_at })
  }
  return items.sort((a, b) => (b.time || 0) - (a.time || 0)).slice(0, 4)
})

// 书架接口字段是 chapters_done/updated；时间缺失时不留孤立的「·」
function recentProjectMeta(p) {
  const chapters = `${p.chapters_done ?? p.chapter_count ?? 0} 章`
  const time = fmtTime(p.updated || p.updated_at || p.created || p.created_at)
  return time ? `${chapters} · ${time}` : chapters
}

async function loadRecentProjects() {
  try {
    const r = await listProjects({ page: 1, page_size: 4 })
    const list = r.projects || r.items || []
    recentProjects.value = list.slice(0, 4)
  } catch {}
}

async function loadMe() {
  try { const r = await meApi.getMe(); me.value = r.user || r || {} } catch {}
}
async function loadAccount() {
  try {
    const [refund, rgs, rrs] = await Promise.all([
      meApi.getRefundable(), meApi.listMyRecharges(), meApi.listRefundRequests(),
    ])
    refundable.value = refund.refundable ?? 0
    recharges.value = rgs.recharges || []
    refundRequests.value = rrs.requests || []
  } catch {}
}
async function loadReferral() {
  try { referral.value = await meApi.getReferral() } catch {}
}

function fmtTime(t) {
  if (!t) return ''
  const d = new Date((t < 1e12 ? t * 1000 : t))
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function copyInvite() {
  try { await navigator.clipboard.writeText(referral.value.invite_code || ''); ElMessage.success('已复制邀请码') }
  catch { ElMessage.warning('复制失败，请手动复制') }
}
async function copyInviteLink() {
  try { await navigator.clipboard.writeText(inviteLink.value); ElMessage.success('已复制邀请链接') }
  catch { ElMessage.warning('复制失败，请手动复制') }
}
async function doBindReferrer() {
  if (!inviteInput.value.trim()) { ElMessage.warning('请输入邀请码'); return }
  try {
    await meApi.bindReferrer(inviteInput.value.trim())
    ElMessage.success('绑定成功')
    loadReferral(); loadMe()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '绑定失败') }
}

const refundDialog = ref(false)
const refundForm = ref({ amount: 0, reason: '' })
function openRefund() {
  if ((refundable.value || 0) <= 0) { ElMessage.warning('当前没有可退金额（只退充值部分，赠金/返佣不可退）'); return }
  // 默认填满可退金额，避免默认 1 元与“可退 0 元”矛盾
  refundForm.value = { amount: Number(refundable.value.toFixed(2)), reason: '' }
  refundDialog.value = true
}
async function doSubmitRefund() {
  const amount = Number(refundForm.value.amount || 0)
  if (!amount) { ElMessage.warning('请填写退款金额'); return }
  if (!refundForm.value.reason.trim()) { ElMessage.warning('请填写退款原因'); return }
  if (amount > (refundable.value || 0)) {
    ElMessage.warning(`退款金额超过可退上限 ¥${(refundable.value || 0).toFixed(2)}（仅充值部分，赠金/返佣不可退）`)
    return
  }
  try {
    await meApi.submitRefund(refundForm.value)
    ElMessage.success('退款申请已提交，请等待站长处理')
    refundDialog.value = false
    refundForm.value = { amount: 0, reason: '' }
    loadAccount()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '提交失败') }
}

// ---- 技能管理（我的/平台/市场 统一入口） ----
const STAGE_LABELS = { research: '调研', outline: '大纲', world: '世界观', chapter: '正文', review: '审校' }
const ROLE_LABELS = { researcher: '调研', architect: '架构师', writer: '写手', reviewer: '审校', reader: '读者', validator: '逻辑校验' }
const roleLabel = s => (s.role ? (ROLE_LABELS[s.role] || s.role) : '全部角色')
const stageLabel = s => {
  const list = (s.applicable_stages || '').split(/[,，]/).map(x => x.trim()).filter(Boolean)
  return list.length ? list.map(x => STAGE_LABELS[x] || x).join('、') : '全部阶段'
}
const mySkills = ref([])
const marketSkills = ref([])
const showSkillDlg = ref(false)
const skillForm = ref({ name: '', role: '', description: '', prompt_append: '', stage_list: [] })
async function loadMySkills() {
  try { const r = await toolsApi.listMySkills(); mySkills.value = r.skills || [] } catch {}
}
async function loadMarket() {
  try { const r = await toolsApi.listMarketSkills(); marketSkills.value = r.skills || [] } catch {}
}
async function saveMySkill() {
  if (!skillForm.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  try {
    const payload = { ...skillForm.value }
    payload.applicable_stages = (payload.stage_list || []).join(',')
    await toolsApi.saveMySkill(payload)
    ElMessage.success('技能已保存')
    showSkillDlg.value = false
    skillForm.value = { name: '', role: '', description: '', prompt_append: '', stage_list: [] }
    loadMySkills()
  } catch { ElMessage.error('保存失败') }
}
async function delMySkill(id) {
  try { await ElMessageBox.confirm('确定删除？', '删除 Skill', { type: 'warning' }) } catch { return }
  await toolsApi.delMySkill(id)
  ElMessage.success('已删除')
  loadMySkills()
}
const publishSkill = async (s) => {
  try {
    await toolsApi.publishToMarket(s.id)
    ElMessage.success('已发布到市场')
    loadMySkills(); loadMarket()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '发布失败') }
}
const installSkill = async (m) => {
  try {
    await toolsApi.installMarketSkill(m.id)
    ElMessage.success('已安装到我的Skills')
    m.installed = true
    loadMySkills()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '安装失败') }
}

// ---- 风格角色 ----
const styleRoles = ref([])
const platformSkills = ref([])
const showRoleDlg = ref(false)
const roleForm = ref({ id: 0, name: '', description: '', role_prompt: '', reference_text: '', skills: [] })
const newMount = ref('')
async function loadStyleRoles() {
  try { const r = await toolsApi.listStyleRoles(); styleRoles.value = r.roles || [] } catch {}
}
async function loadPlatformSkills() {
  try { const r = await toolsApi.listPlatformSkills(); platformSkills.value = r.skills || [] } catch {}
}
function openRoleDlg(r) {
  roleForm.value = r
    ? { id: r.id, name: r.name, description: r.description || '', role_prompt: r.role_prompt || '', reference_text: r.reference_text || '', skills: [...(r.skills || [])] }
    : { id: 0, name: '', description: '', role_prompt: '', reference_text: '', skills: [] }
  newMount.value = ''
  showRoleDlg.value = true
}
/** 平台角色只读：复制成「我的」角色再挂载，不动公共配置。 */
function copyPlatformRole(r) {
  openRoleDlg({
    id: 0, name: `${r.name}（我的）`, description: r.description || '',
    role_prompt: r.role_prompt || '', reference_text: r.reference_text || '',
    skills: [...(r.skills || [])],
  })
  ElMessage.info('已复制平台角色，保存后会成为你自己的角色，可继续挂载技能')
}
function addMount() {
  if (!newMount.value) return
  const [type, id] = newMount.value.split(':')
  const list = type === 'user' ? mySkills.value : platformSkills.value
  const sk = list.find(x => x.id == id)
  if (!sk || roleForm.value.skills.some(x => x.skill_type === type && x.skill_id == id)) return
  roleForm.value.skills.push({ skill_type: type, skill_id: sk.id, skill_name: sk.name })
  newMount.value = ''
}
async function saveRole() {
  if (!roleForm.value.name.trim()) { ElMessage.warning('请输入名称'); return }
  try {
    const base = { name: roleForm.value.name, description: roleForm.value.description, role_prompt: roleForm.value.role_prompt, reference_text: roleForm.value.reference_text || '', is_active: 1, sort_order: 0 }
    let rid = roleForm.value.id
    if (rid) {
      await toolsApi.updateStyleRole(rid, base)
      const old = styleRoles.value.find(x => x.id === rid)?.skills || []
      for (const o of old) {
        if (!roleForm.value.skills.some(x => x.skill_type === o.skill_type && x.skill_id === o.skill_id)) {
          await toolsApi.unmountStyleRoleSkill(rid, o.skill_type, o.skill_id)
        }
      }
      for (const n of roleForm.value.skills) {
        if (!old.some(x => x.skill_type === n.skill_type && x.skill_id === n.skill_id)) {
          await toolsApi.mountStyleRoleSkill(rid, { skill_type: n.skill_type, skill_id: n.skill_id })
        }
      }
    } else {
      const r = await toolsApi.createStyleRole(base)
      rid = r.id
      for (const n of roleForm.value.skills) {
        await toolsApi.mountStyleRoleSkill(rid, { skill_type: n.skill_type, skill_id: n.skill_id })
      }
    }
    ElMessage.success('已保存')
    showRoleDlg.value = false
    loadStyleRoles()
  } catch (e) { ElMessage.error(e?.response?.data?.detail || '保存失败') }
}
async function delRole(r) {
  try { await ElMessageBox.confirm(`确定删除风格角色「${r.name}」？`, '删除', { type: 'warning' }) } catch { return }
  await toolsApi.deleteStyleRole(r.id)
  loadStyleRoles()
}

onMounted(() => {
  loadMe(); loadAccount(); loadReferral(); loadMySkills(); loadMarket(); loadStyleRoles(); loadPlatformSkills(); loadRecentProjects()
})
</script>

<style scoped>
.user-card { text-align: center; padding-top: 18px; }
.avatar {
  width: 56px; height: 56px; border-radius: 50%; margin: 0 auto 10px;
  background: linear-gradient(140deg, #c9a227, #8a6d1e); color: #f5efdc;
  font-size: 24px; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 3px 8px rgba(201,162,39,.4);
}
.uname { font-size: 18px; font-weight: 700; }
.stat-card { text-align: center; }
.stat-val { font-size: 22px; font-weight: 700; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.stat-sub { display: block; font-size: 12px; color: #7a8699; margin-top: 2px; }
.muted { color: #909399; font-size: 13px; }
.bill-in { color: #67c23a; font-weight: 600; }
.bill-out { color: #f56c6c; font-weight: 600; }
.fs-row { display: flex; justify-content: space-between; align-items: center; padding: 6px 0; font-size: 13px; border-bottom: 1px solid #f2f3f5; }
.ref-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 14px; }
.ov-head { display: flex; align-items: center; justify-content: space-between; }
.ov-list { display: flex; flex-direction: column; }
.ov-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 7px 0; border-bottom: 1px solid #f2f3f5; font-size: 13px; cursor: default; }
.ov-row:last-child { border-bottom: none; }
.ov-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ov-row[data-clickable] { cursor: pointer; }
.link { color: #409eff; }
</style>
