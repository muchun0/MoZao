/* 管理后台逻辑（原生 JS） */
(function () {
  "use strict";

  const API = "";
  const TOKEN_KEY = "novel_admin_token";
  let admin = null;
  let usersCache = [];
  let currentSide = "overview";

  function $(sel) { return document.querySelector(sel); }

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(t) { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); }

  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {});
    const token = getToken();
    if (token) options.headers["Authorization"] = "Bearer " + token;
    const resp = await fetch(API + path, options);
    if (resp.status === 401) { showLogin(); throw new Error("登录已失效"); }
    if (!resp.ok) {
      let detail = "请求失败 (" + resp.status + ")";
      try { const j = await resp.json(); if (j.detail) detail = j.detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const ct = resp.headers.get("content-type") || "";
    return ct.includes("json") ? resp.json() : resp;
  }

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
  }

  function esc(s) {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function fmtMoney(v) { return Number(v).toFixed(2); }

  // ---------- 视图 ----------
  function showLogin() {
    $("#view-login").classList.add("active");
    $("#view-main").classList.remove("active");
  }
  function showMain() {
    $("#view-login").classList.remove("active");
    $("#view-main").classList.add("active");
    switchSide("overview");
  }

  function switchSide(view) {
    currentSide = view;
    document.querySelectorAll(".side-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    $("#view-" + view).classList.add("active");
    if (view === "overview") loadStats();
    if (view === "users") loadUsers();
    if (view === "agents") loadAgents();
    if (view === "projects") loadProjects();
    if (view === "refunds") loadRefunds("");
    if (view === "recharges") loadRecharges();
    if (view === "logs") loadLogs();
    if (view === "settings") loadSettings();
  }

  document.querySelectorAll(".side-btn").forEach((b) =>
    b.addEventListener("click", () => switchSide(b.dataset.view)));

  // ---------- 登录 ----------
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("#login-error");
    errBox.classList.add("hidden");
    try {
      const r = await api("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: e.target.username.value.trim(), password: e.target.password.value }),
      });
      setToken(r.token);
      admin = r.user;
      $("#admin-name").textContent = admin.username + "（站长）";
      showMain();
      toast("欢迎回来，站长");
    } catch (err) {
      errBox.textContent = "登录失败：" + err.message;
      errBox.classList.remove("hidden");
    }
  });

  $("#btn-logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    setToken("");
    showLogin();
  });

  // ---------- 概览 ----------
  async function loadStats() {
    try {
      const { stats } = await api("/api/admin/stats");
      const cards = [
        ["用户总数", stats.users], ["代理数", stats.agents], ["项目总数", stats.projects],
        ["充值总额(元)", fmtMoney(stats.total_recharge)], ["消费总额(元)", fmtMoney(stats.total_consume)],
        ["累计返佣(元)", fmtMoney(stats.total_commission)], ["注册赠金(元)", fmtMoney(stats.total_bonus)],
      ];
      $("#stat-grid").innerHTML = cards.map(([l, n]) =>
        '<div class="stat-card"><div class="num">' + esc(n) + '</div><div class="lbl">' + esc(l) + "</div></div>"
      ).join("");
      renderChart(stats);
    } catch (e) {
      $("#stat-grid").textContent = "加载失败：" + e.message;
    }
  }

  // 资金对比图：充值 vs 消费 vs 返佣（纯 CSS 柱状，按最大值归一化）
  function renderChart(stats) {
    const items = [
      { key: "recharge", val: Number(stats.total_recharge || 0) },
      { key: "consume", val: Number(stats.total_consume || 0) },
      { key: "commission", val: Number(stats.total_commission || 0) },
    ];
    const max = Math.max.apply(null, items.map((i) => i.val));
    const barH = 150; // 最大柱高 px
    items.forEach((it) => {
      const bar = $("#bar-" + it.key);
      const val = $("#bar-" + it.key + "-val");
      if (!bar) return;
      const h = max > 0 ? Math.max(6, Math.round((it.val / max) * barH)) : 6;
      bar.style.height = h + "px";
      bar.textContent = it.val > 0 ? fmtMoney(it.val) : "0.00";
      if (val) val.textContent = fmtMoney(it.val);
    });
  }

  // ---------- 用户管理 ----------
  async function loadUsers() {
    try {
      const { users } = await api("/api/admin/users");
      usersCache = users;
      const body = $("#user-table tbody");
      body.innerHTML = users.map((u) => {
        const role = u.is_admin ? '<span class="tag admin">站长</span>'
          : u.is_agent ? '<span class="tag agent">代理</span>'
          : '<span class="tag normal">用户</span>';
        return "<tr>" +
          "<td>" + u.id + "</td>" +
          "<td>" + esc(u.username) + "</td>" +
          "<td>" + (u.email ? esc(u.email) : '<span class="muted">—</span>') + "</td>" +
          '<td class="money">' + fmtMoney(u.balance) + "</td>" +
          "<td>" + role + "</td>" +
          "<td>" + (u.is_agent ? Math.round(u.agent_discount * 100) + "%" : "—") + "</td>" +
          "<td>" + esc(u.invite_code || "") + "</td>" +
          "<td>" + u.downline_count + "</td>" +
          '<td class="money pos">' + fmtMoney(u.total_commission) + "</td>" +
          "<td>" + fmtTime(u.created_at) + "</td>" +
          "<td>" +
            '<button class="btn small" data-act="recharge" data-id="' + u.id + '">充值</button> ' +
            (u.is_agent
              ? '<button class="btn small danger" data-act="unagent" data-id="' + u.id + '">取消代理</button>'
              : '<button class="btn small" data-act="agent" data-id="' + u.id + '">设为代理</button>') +
            ' <button class="btn small ghost" data-act="downline" data-id="' + u.id + '">下级</button>' +
          "</td></tr>";
      }).join("");
      bindUserActions(body);
    } catch (e) {
      toast("加载用户失败：" + e.message);
    }
  }

  function bindUserActions(body) {
    body.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const act = btn.dataset.act;
        const uid = parseInt(btn.dataset.id, 10);
        const u = usersCache.find((x) => x.id === uid);
        if (act === "recharge") openRecharge(u);
        else if (act === "agent") openAgentModal(u);
        else if (act === "unagent") {
          if (!confirm("确认取消「" + u.username + "」的代理资格？")) return;
          try {
            await api("/api/admin/users/" + uid + "/unagent", { method: "POST" });
            toast("已取消代理：" + u.username);
            loadUsers();
          } catch (e) { toast("失败：" + e.message); }
        } else if (act === "downline") showDownline(u);
      });
    });
  }

  // ---------- 充值 ----------
  let rechargeTarget = null;
  function openRecharge(u) {
    rechargeTarget = u;
    $("#rm-title").textContent = "给「" + u.username + "」充值";
    $("#rm-user").textContent = u.username;
    $("#rm-balance").textContent = fmtMoney(u.balance);
    $("#rm-amount").value = "";
    $("#rm-error").classList.add("hidden");
    $("#recharge-modal").classList.remove("hidden");
  }
  $("#btn-close-rm").addEventListener("click", () => $("#recharge-modal").classList.add("hidden"));
  $("#btn-confirm-rm").addEventListener("click", async () => {
    const amount = parseFloat($("#rm-amount").value);
    if (!amount || amount <= 0) { $("#rm-error").textContent = "请输入正确的金额"; $("#rm-error").classList.remove("hidden"); return; }
    const btn = $("#btn-confirm-rm");
    btn.disabled = true;
    try {
      const r = await api("/api/admin/users/" + rechargeTarget.id + "/recharge", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: amount }),
      });
      toast("已给「" + rechargeTarget.username + "」到账 " + fmtMoney(amount) + " 元，余额 " + fmtMoney(r.balance));
      $("#recharge-modal").classList.add("hidden");
      loadUsers();
    } catch (e) {
      $("#rm-error").textContent = "失败：" + e.message;
      $("#rm-error").classList.remove("hidden");
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- 设代理 ----------
  let agentTarget = null;
  function openAgentModal(u) {
    agentTarget = u;
    $("#am-user").textContent = u.username;
    $("#am-discount").value = "0.8";
    $("#am-error").classList.add("hidden");
    $("#agent-modal").classList.remove("hidden");
  }
  $("#btn-close-am").addEventListener("click", () => $("#agent-modal").classList.add("hidden"));
  $("#btn-confirm-am").addEventListener("click", async () => {
    const discount = parseFloat($("#am-discount").value);
    if (!discount || discount <= 0 || discount > 1) {
      $("#am-error").textContent = "折扣需在 0.01 ~ 1 之间（0.8 = 8 折）";
      $("#am-error").classList.remove("hidden");
      return;
    }
    try {
      const r = await api("/api/admin/users/" + agentTarget.id + "/agent", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discount: discount }),
      });
      toast("已开通代理：「" + r.username + "」消费 " + Math.round(discount * 100) + "% 折扣");
      $("#agent-modal").classList.add("hidden");
      loadUsers();
    } catch (e) {
      $("#am-error").textContent = "失败：" + e.message;
      $("#am-error").classList.remove("hidden");
    }
  });

  // ---------- 下级 ----------
  async function showDownline(u) {
    try {
      const { downline } = await api("/api/admin/users/" + u.id + "/downline");
      if (!downline.length) { toast("「" + u.username + "」暂无下级"); return; }
      const names = downline.map((d) => d.username + "（" + fmtTime(d.created_at) + "）").join("、");
      toast("「" + u.username + "」下级 " + downline.length + " 人：" + names);
    } catch (e) { toast("失败：" + e.message); }
  }

  // ---------- 代理管理 ----------
  async function loadAgents() {
    try {
      const { users } = await api("/api/admin/users");
      const agents = users.filter((u) => u.is_agent);
      const body = $("#agent-table tbody");
      if (!agents.length) { body.innerHTML = '<tr><td colspan="7" class="muted">暂无代理</td></tr>'; return; }
      body.innerHTML = agents.map((u) =>
        "<tr>" +
        "<td>" + u.id + "</td>" +
        "<td>" + esc(u.username) + "</td>" +
        '<td class="money">' + fmtMoney(u.balance) + "</td>" +
        "<td>" + Math.round(u.agent_discount * 100) + "%</td>" +
        "<td>" + u.downline_count + "</td>" +
        '<td class="money pos">' + fmtMoney(u.total_commission) + "</td>" +
        '<td><button class="btn small" data-comm="' + u.id + '" data-name="' + esc(u.username) + '">返佣明细</button></td>' +
        "</tr>"
      ).join("");
      body.querySelectorAll("button[data-comm]").forEach((btn) =>
        btn.addEventListener("click", () => showCommissions(parseInt(btn.dataset.comm, 10), btn.dataset.name)));
      $("#agent-detail").innerHTML = "";
    } catch (e) {
      toast("加载代理失败：" + e.message);
    }
  }

  async function showCommissions(uid, name) {
    try {
      const { commissions, total } = await api("/api/admin/users/" + uid + "/commissions");
      const kindNames = { consume: "消费返佣", recharge: "充值返佣", register: "拉新奖励" };
      let html = "<h4>「" + esc(name) + "」返佣明细（累计 " + fmtMoney(total) + " 元）</h4>";
      if (!commissions.length) { html += '<p class="muted">暂无返佣记录</p>'; }
      else {
        html += "<table class='tbl'><thead><tr><th>时间</th><th>金额</th><th>类型</th><th>备注</th></tr></thead><tbody>" +
          commissions.map((c) =>
            "<tr><td>" + fmtTime(c.created_at) + '</td><td class="money pos">+' + fmtMoney(c.amount) +
            "</td><td>" + (kindNames[c.kind] || c.kind) + "</td><td>" + esc(c.note || "") + "</td></tr>"
          ).join("") + "</tbody></table>";
      }
      $("#agent-detail").innerHTML = html;
      $("#agent-detail").scrollIntoView({ behavior: "smooth" });
    } catch (e) { toast("加载返佣失败：" + e.message); }
  }

  // ---------- 项目总览 ----------
  async function loadProjects() {
    try {
      const { projects } = await api("/api/admin/projects");
      const body = $("#project-table tbody");
      if (!projects.length) { body.innerHTML = '<tr><td colspan="6" class="muted">暂无项目</td></tr>'; return; }
      body.innerHTML = projects.map((p) => {
        const status = p.running ? '<span class="tag running">生成中</span>'
          : p.chapters_total && p.chapters_done >= p.chapters_total ? '<span class="tag done">已完成</span>'
          : p.chapters_done > 0 ? '<span class="tag running">进行中</span>' : '<span class="tag normal">未开始</span>';
        return "<tr>" +
          "<td>" + esc(p.title) + "</td>" +
          "<td>" + esc(p.owner || "") + "</td>" +
          "<td>" + esc(p.genre) + "</td>" +
          "<td>" + p.chapters_done + "/" + p.chapters_total + "</td>" +
          "<td>" + status + "</td>" +
          "<td>" + fmtTime(p.created) + "</td></tr>";
      }).join("");
    } catch (e) {
      toast("加载项目失败：" + e.message);
    }
  }

  // ---------- 余额流水 ----------
  async function loadRecharges() {
    try {
      const { recharges } = await api("/api/admin/recharges");
      const body = $("#recharge-table tbody");
      if (!recharges.length) { body.innerHTML = '<tr><td colspan="4" class="muted">暂无流水</td></tr>'; return; }
      body.innerHTML = recharges.map((r) =>
        "<tr>" +
        "<td>" + fmtTime(r.created_at) + "</td>" +
        "<td>" + esc(r.username || "") + "</td>" +
        '<td class="money ' + (r.amount >= 0 ? "pos" : "neg") + '">' + (r.amount >= 0 ? "+" : "") + fmtMoney(r.amount) + "</td>" +
        "<td>" + esc(r.note || "") + "</td></tr>"
      ).join("");
    } catch (e) {
      toast("加载流水失败：" + e.message);
    }
  }

  // ---------- 退款管理 ----------
  let refundsCache = [];
  document.querySelectorAll("#refund-tabs .tab").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll("#refund-tabs .tab").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      loadRefunds(b.dataset.status);
    }));

  async function loadRefunds(status) {
    try {
      const { refunds, mail_configured } = await api("/api/admin/refunds" + (status ? "?status=" + status : ""));
      refundsCache = refunds;
      $("#refund-mail-status").textContent = mail_configured
        ? "（邮件通知已配置 ✓）" : "（⚠ 未配置 SMTP，邮件无法发出，请在 .env 配置）";
      const body = $("#refund-table tbody");
      if (!refunds.length) { body.innerHTML = '<tr><td colspan="9" class="muted">暂无退款申请</td></tr>'; return; }
      const statusTags = { pending: '<span class="tag running">待处理</span>', approved: '<span class="tag done">已退款</span>', rejected: '<span class="tag normal">已拒绝</span>' };
      body.innerHTML = refunds.map((q) =>
        "<tr>" +
        "<td>" + q.id + "</td>" +
        "<td>" + esc(q.username || "") + "</td>" +
        '<td class="money">' + fmtMoney(q.amount) + "</td>" +
        "<td>" + esc(q.reason || "—") + "</td>" +
        "<td>" + (statusTags[q.status] || q.status) + "</td>" +
        "<td>" + (q.created_at ? fmtTime(q.created_at) : "—") + "</td>" +
        "<td>" + (q.handled_at ? fmtTime(q.handled_at) : "—") + "</td>" +
        "<td>" + esc(q.admin_note || "—") + "</td>" +
        "<td>" + (q.status === "pending"
          ? '<button class="btn small" data-handle="' + q.id + '">处理</button>'
          : "—") + "</td></tr>"
      ).join("");
      body.querySelectorAll("button[data-handle]").forEach((b) =>
        b.addEventListener("click", () => {
          const q = refundsCache.find((x) => x.id === parseInt(b.dataset.handle, 10));
          if (q) openRefundHandle(q);
        }));
    } catch (e) {
      toast("加载退款申请失败：" + e.message);
    }
  }

  let refundTarget = null;
  function openRefundHandle(q) {
    refundTarget = q;
    $("#rhm-user").textContent = q.username || "";
    $("#rhm-amount").textContent = fmtMoney(q.amount);
    $("#rhm-reason").textContent = q.reason || "（未填写）";
    $("#rhm-note").value = "";
    $("#rhm-error").classList.add("hidden");
    $("#refund-handle-modal").classList.remove("hidden");
  }
  $("#btn-close-rhm").addEventListener("click", () => $("#refund-handle-modal").classList.add("hidden"));

  async function handleRefund(action) {
    if (!refundTarget) return;
    const btn = action === "approve" ? $("#btn-rhm-approve") : $("#btn-rhm-reject");
    const note = $("#rhm-note").value.trim();
    btn.disabled = true;
    try {
      const r = await api("/api/admin/refunds/" + refundTarget.id + "/handle", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: action, admin_note: note }),
      });
      toast(r.message);
      $("#refund-handle-modal").classList.add("hidden");
      const activeTab = document.querySelector("#refund-tabs .tab.active");
      loadRefunds(activeTab ? activeTab.dataset.status : "");
      loadUsers();
    } catch (e) {
      $("#rhm-error").textContent = "失败：" + e.message;
      $("#rhm-error").classList.remove("hidden");
    } finally {
      btn.disabled = false;
    }
  }
  $("#btn-rhm-approve").addEventListener("click", () => handleRefund("approve"));
  $("#btn-rhm-reject").addEventListener("click", () => handleRefund("reject"));

  // ---------- 系统日志 ----------
  async function loadLogs() {
    try {
      const r = await api("/api/admin/logs?lines=300");
      $("#log-file-label").textContent = "（" + r.log_file + "）";
      $("#log-viewer").textContent = r.logs;
      const v = $("#log-viewer");
      v.scrollTop = v.scrollHeight;
    } catch (e) {
      $("#log-viewer").textContent = "加载失败：" + e.message;
    }
  }
  $("#btn-refresh-logs").addEventListener("click", loadLogs);

  // ---------- 系统参数 ----------
  async function loadSettings() {
    try {
      const s = await api("/api/admin/settings");
      const pct = (x) => Math.round((x || 0) * 100) + "%";
      const tierHtml = s.tiers.map((t) =>
        '<div class="settings-row"><span class="k">' + esc(t.name) + "（" + (t.max_words ? "≤ " + t.max_words.toLocaleString() + " 字" : "不限字数") + "）</span>" +
        '<span class="v">' + fmtMoney(t.price) + " 元" +
        (t.original_price ? ' <s style="color:#9ca3af">' + fmtMoney(t.original_price) + "</s>" : "") + "</span></div>"
      ).join("");
      $("#settings-box").innerHTML =
        "<h4>定价档位</h4>" + tierHtml +
        "<h4>代理 / 返佣规则</h4>" +
        '<div class="settings-row"><span class="k">注册赠金</span><span class="v">' + fmtMoney(s.register_bonus) + " 元/人</span></div>" +
        '<div class="settings-row"><span class="k">代理消费折扣</span><span class="v">' + pct(s.agent_discount) + "</span></div>" +
        '<div class="settings-row"><span class="k">返佣比例</span><span class="v">' + pct(s.commission_rate) + "</span></div>" +
        '<div class="settings-row"><span class="k">返佣基数</span><span class="v">' + (s.commission_base === "consume" ? "按下级消费" : "按下级充值") + "</span></div>" +
        '<div class="settings-row"><span class="k">注册拉新奖励</span><span class="v">' + fmtMoney(s.referral_bonus) + " 元/人</span></div>" +
        "<h4>系统状态</h4>" +
        '<div class="settings-row"><span class="k">服务时间</span><span class="v">' + esc(s.server_time) + "</span></div>" +
        '<div class="settings-row"><span class="k">版本</span><span class="v">' + esc(s.version) + "</span></div>";
    } catch (e) {
      $("#settings-box").textContent = "加载失败：" + e.message;
    }
  }

  // ---------- 启动 ----------
  (async function init() {
    if (getToken()) {
      try {
        const r = await api("/api/me");
        if (r.user && r.user.is_admin) {
          admin = r.user;
          $("#admin-name").textContent = admin.username + "（站长）";
          showMain();
          return;
        }
      } catch (e) { /* 继续走登录页 */ }
      setToken("");
    }
    showLogin();
  })();
})();
