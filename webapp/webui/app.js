/* 墨造 · AI 小说创作平台 - 前端逻辑（原生 JS，无框架依赖）多用户版 */
(function () {
  "use strict";

  const API = ""; // 同源部署；若前后端分离可改为 "http://127.0.0.1:8000"
  const TOKEN_KEY = "novel_web_token";

  let currentView = "projects";
  let currentProjectId = null;
  let currentChapterNo = null;
  let pollTimer = null;
  let detailCache = null; // 详情页缓存
  let chaptersCache = []; // 章节列表（含 no/title/words/summary）
  let me = null; // 当前登录用户
  let projectsCache = []; // 项目列表缓存（供搜索/筛选）
  let pricingCache = null; // 定价缓存
  let readerFont = parseInt(localStorage.getItem("novel_reader_font") || "17", 10);
  let readerTheme = localStorage.getItem("novel_reader_theme") || "paper";

  // ---------- 工具 ----------
  function $(sel) { return document.querySelector(sel); }
  function $$(sel) { return document.querySelectorAll(sel); }

  function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
  function setToken(t) { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY); }

  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {});
    const token = getToken();
    if (token) options.headers["Authorization"] = "Bearer " + token;
    const resp = await fetch(API + path, options);
    if (resp.status === 401 && currentView !== "login") {
      // 登录失效：回到登录页
      setToken("");
      showLogin();
      throw new Error("登录已失效，请重新登录");
    }
    if (!resp.ok) {
      let detail = "请求失败 (" + resp.status + ")";
      try {
        const j = await resp.json();
        if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch (e) { /* ignore */ }
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

  function fmtWords(n) {
    if (n >= 10000) return (n / 10000).toFixed(1) + " 万字";
    return n + " 字";
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  function fmtPrice(v) {
    const n = Number(v);
    return n.toFixed(2);
  }

  // ---------- 登录 / 用户 ----------
  function showLogin() {
    currentView = "login";
    $("#view-login").classList.remove("hidden");
    $("#app-main").classList.add("hidden");
    $$(".view").forEach((v) => v.classList.remove("active"));
    $("#view-login").classList.add("active");
  }

  function showApp() {
    $("#view-login").classList.add("hidden");
    $("#app-main").classList.remove("hidden");
    // 加载价格提示
    loadPricing();
    switchView("projects");
  }

  async function loadMe() {
    try {
      const r = await api("/api/me");
      me = r.user;
      renderUserBar();
      return true;
    } catch (e) {
      return false;
    }
  }

  function renderUserBar() {
    if (!me) return;
    $("#balance-label").textContent = "余额 " + me.balance.toFixed(2) + " 元";
    $("#username-label").textContent = me.username + (me.is_admin ? "（站长）" : "") + (me.is_agent ? "（代理）" : "");
    $("#btn-admin-panel").style.display = me.is_admin ? "" : "none";
  }

  // 登录/注册 tab 切换
  $$(".login-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".login-tabs .tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      const isLogin = tab.dataset.ltab === "login";
      $("#login-form").classList.toggle("hidden", !isLogin);
      $("#register-form").classList.toggle("hidden", isLogin);
      $("#reset-form").classList.add("hidden");
      $("#login-error").classList.add("hidden");
      $("#register-error").classList.add("hidden");
      $("#reset-error").classList.add("hidden");
    });
  });

  // 忘记密码 / 返回登录
  $("#btn-forgot").addEventListener("click", () => {
    $("#login-form").classList.add("hidden");
    $("#register-form").classList.add("hidden");
    $("#reset-form").classList.remove("hidden");
    $("#login-error").classList.add("hidden");
    $("#reset-error").classList.add("hidden");
    $$(".login-tabs .tab").forEach((t) => t.classList.remove("active"));
  });
  $("#btn-reset-back").addEventListener("click", () => {
    $("#reset-form").classList.add("hidden");
    $("#login-form").classList.remove("hidden");
    $$(".login-tabs .tab").forEach((t) => t.classList.remove("active"));
    document.querySelector('.login-tabs .tab[data-ltab="login"]').classList.add("active");
  });

  // 获取验证码（注册 / 重置共用）：60 秒倒计时防刷
  function bindSendCode(btnSel, apiPath, errSel) {
    const btn = $(btnSel);
    btn.addEventListener("click", async () => {
      const form = btn.closest("form");
      const email = (form.email ? form.email.value : "").trim();
      if (!email) {
        toast("请先填写邮箱");
        return;
      }
      btn.disabled = true;
      try {
        const r = await api(apiPath, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        toast(r.msg || "验证码已发送");
      } catch (err) {
        if (errSel) { const eb = $(errSel); eb.textContent = "获取验证码失败：" + err.message; eb.classList.remove("hidden"); }
        else toast("获取验证码失败：" + err.message);
        btn.disabled = false;
        return;
      }
      let left = 60;
      const origin = btn.textContent;
      btn.textContent = left + "s 后重发";
      const timer = setInterval(() => {
        left -= 1;
        if (left <= 0) {
          clearInterval(timer);
          btn.textContent = origin;
          btn.disabled = false;
        } else {
          btn.textContent = left + "s 后重发";
        }
      }, 1000);
    });
  }
  bindSendCode("#btn-reg-send-code", "/api/send-register-code", "#register-error");
  bindSendCode("#btn-reset-send-code", "/api/send-reset-code", "#reset-error");

  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("#login-error");
    errBox.classList.add("hidden");
    try {
      const r = await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: e.target.username.value.trim(), password: e.target.password.value }),
      });
      setToken(r.token);
      me = r.user;
      showApp();
      toast("欢迎回来，" + me.username);
    } catch (err) {
      errBox.textContent = "登录失败：" + err.message;
      errBox.classList.remove("hidden");
    }
  });

  $("#register-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("#register-error");
    errBox.classList.add("hidden");
    try {
      const r = await api("/api/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: e.target.username.value.trim(),
          email: (e.target.email ? e.target.email.value.trim() : ""),
          code: (e.target.code ? e.target.code.value.trim() : ""),
          password: e.target.password.value,
          invite_code: (e.target.invite_code ? e.target.invite_code.value : "").trim(),
        }),
      });
      setToken(r.token);
      me = r.user;
      showApp();
      toast("注册成功，欢迎 " + me.username);
      if (r.note) toast(r.note);
    } catch (err) {
      errBox.textContent = "注册失败：" + err.message;
      errBox.classList.remove("hidden");
    }
  });

  $("#reset-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("#reset-error");
    errBox.classList.add("hidden");
    try {
      const r = await api("/api/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: e.target.email.value.trim(),
          code: e.target.code.value.trim(),
          new_password: e.target.new_password.value,
        }),
      });
      toast(r.msg || "密码已重置");
      e.target.reset();
      $("#reset-form").classList.add("hidden");
      $("#login-form").classList.remove("hidden");
      $$(".login-tabs .tab").forEach((t) => t.classList.remove("active"));
      document.querySelector('.login-tabs .tab[data-ltab="login"]').classList.add("active");
    } catch (err) {
      errBox.textContent = "重置失败：" + err.message;
      errBox.classList.remove("hidden");
    }
  });

  $("#btn-logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    setToken("");
    me = null;
    stopPolling();
    showLogin();
  });

  // 充值弹层
  $("#btn-recharge").addEventListener("click", () => {
    renderRechargeTiers();
    $("#recharge-modal").classList.remove("hidden");
  });
  $("#btn-close-recharge").addEventListener("click", () => $("#recharge-modal").classList.add("hidden"));

  // ---------- 退款申请 ----------
  $("#btn-open-refund").addEventListener("click", async () => {
    $("#recharge-modal").classList.add("hidden");
    $("#refund-modal").classList.remove("hidden");
    $("#refund-error").classList.add("hidden");
    try {
      const r = await api("/api/me/refundable");
      $("#refund-available").textContent = r.refundable.toFixed(2);
    } catch (e) {
      $("#refund-available").textContent = "—";
    }
    loadRefundHistory();
  });
  $("#btn-close-refund").addEventListener("click", () => $("#refund-modal").classList.add("hidden"));

  async function loadRefundHistory() {
    const box = $("#refund-history");
    try {
      const r = await api("/api/me/refund-requests");
      if (!r.requests.length) { box.innerHTML = ""; return; }
      const statusNames = { pending: "⏳ 待处理", approved: "✅ 已退款", rejected: "❌ 已拒绝" };
      box.innerHTML = '<hr style="margin:14px 0;border:none;border-top:1px solid var(--card-line)">' +
        "<div class='muted' style='margin-bottom:6px'>我的退款申请：</div>" +
        r.requests.map((q) =>
          "<div class='refund-item'><span>" + fmtTime(q.created_at) + " · " + q.amount.toFixed(2) + " 元 · " +
          (statusNames[q.status] || q.status) + "</span>" +
          (q.admin_note ? "<div class='muted'>站长备注：" + esc(q.admin_note) + "</div>" : "") +
          "</div>"
        ).join("");
    } catch (e) { /* ignore */ }
  }

  $("#btn-submit-refund").addEventListener("click", async () => {
    const amount = parseFloat($("#refund-amount").value);
    const reason = $("#refund-reason").value.trim();
    if (!amount || amount <= 0) { $("#refund-error").textContent = "请输入正确的退款金额"; $("#refund-error").classList.remove("hidden"); return; }
    const btn = $("#btn-submit-refund");
    btn.disabled = true;
    try {
      const r = await api("/api/me/refund-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount: amount, reason: reason }),
      });
      toast(r.message + (r.email_sent ? "（已通知站长）" : ""));
      $("#refund-amount").value = "";
      $("#refund-reason").value = "";
      loadRefundHistory();
    } catch (e) {
      $("#refund-error").textContent = "提交失败：" + e.message;
      $("#refund-error").classList.remove("hidden");
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- 代理中心 ----------
  $("#btn-referral").addEventListener("click", async () => {
    $("#referral-modal").classList.remove("hidden");
    $("#referral-body").textContent = "加载中…";
    try {
      const r = await api("/api/me/referral");
      renderReferral(r);
    } catch (e) {
      $("#referral-body").textContent = "加载失败：" + e.message;
    }
  });
  $("#btn-close-referral").addEventListener("click", () => $("#referral-modal").classList.add("hidden"));

  function renderReferral(r) {
    const kindNames = { consume: "消费返佣", recharge: "充值返佣", register: "拉新奖励" };
    const pct = Math.round((r.agent_discount || 1) * 100);
    const link = location.origin + r.invite_link;
    let html = "";
    // 我的邀请码 + 推广链接
    html += '<div class="rf-card">' +
      '<h4>我的推广</h4>' +
      '<div class="rf-row"><span>邀请码</span><code id="rf-code">' + esc(r.invite_code) + "</code></div>" +
      '<div class="rf-row"><span>推广链接</span><div class="rf-link-row">' +
      '<input id="rf-link" class="rf-link-input" value="' + esc(link) + '" readonly>' +
      '<button class="btn small" id="btn-copy-link">复制</button></div></div>' +
      (r.is_agent
        ? '<div class="rf-badge agent">你是代理：消费享 ' + pct + ' 折</div>'
        : '<div class="rf-badge">你暂不是代理（联系站长开通后可享折扣 + 返佣）</div>') +
      "</div>";

    // 我的上级
    if (r.referrer) {
      html += '<div class="rf-card"><h4>我的上级</h4><p>' + esc(r.referrer.username) +
        (r.referrer.is_agent ? "（代理）" : "") + "</p></div>";
    }

    // 我的下级
    html += '<div class="rf-card"><h4>我的下级（' + r.downline_count + " 人）</h4>";
    if (r.downline.length) {
      html += '<table class="rf-table"><thead><tr><th>用户名</th><th>加入时间</th></tr></thead><tbody>' +
        r.downline.map((d) =>
          "<tr><td>" + esc(d.username) + "</td><td>" + fmtTime(d.created_at) + "</td></tr>"
        ).join("") + "</tbody></table>";
    } else {
      html += "<p class='muted'>还没有人通过你的邀请码注册。把邀请码或推广链接发给朋友吧。</p>";
    }
    html += "</div>";

    // 返佣明细
    html += '<div class="rf-card"><h4>返佣明细（累计 ' + r.total_commission.toFixed(2) + " 元）</h4>";
    if (r.commissions.length) {
      html += '<table class="rf-table"><thead><tr><th>时间</th><th>金额</th><th>类型</th><th>备注</th></tr></thead><tbody>' +
        r.commissions.map((c) =>
          "<tr><td>" + fmtTime(c.created_at) + '</td><td class="money">+' + c.amount.toFixed(2) +
          "</td><td>" + (kindNames[c.kind] || c.kind) + "</td><td>" + esc(c.note || "") + "</td></tr>"
        ).join("") + "</tbody></table>";
    } else {
      html += "<p class='muted'>暂无返佣记录。你的下级消费后，返佣会自动到账。</p>";
    }
    html += "</div>";

    html += '<div class="rf-note">返佣规则：下级每笔消费，你获得 ' +
      Math.round((r.commission_rate || 0) * 100) + "% 返佣" +
      (r.referral_bonus > 0 ? "；每个新用户通过你的链接注册，你获得 " + r.referral_bonus.toFixed(2) + " 元奖励" : "") +
      "。返佣自动进入余额，可直接用于创作。</div>";

    $("#referral-body").innerHTML = html;
    const copyBtn = $("#btn-copy-link");
    if (copyBtn) {
      copyBtn.addEventListener("click", () => {
        const inp = $("#rf-link");
        inp.select();
        try {
          document.execCommand("copy");
        } catch (e) {
          navigator.clipboard && navigator.clipboard.writeText(inp.value);
        }
        toast("推广链接已复制");
      });
    }
  }

  async function loadPricing() {
    try {
      const r = await api("/api/pricing");
      pricingCache = r;
      if (r.register_bonus > 0) {
        const note = $("#bonus-note");
        note.innerHTML = "新用户注册即送 <b>" + r.register_bonus + " 元</b> 体验金";
        note.classList.remove("hidden");
      }
      renderRechargeTiers();
      const wordsInput = document.querySelector('#create-form input[name="total_words"]');
      if (wordsInput) updateFee();
    } catch (e) { /* ignore */ }
  }

  // 充值弹层：不再展示金额档位，统一引导联系站长人工充值
  function renderRechargeTiers() {
    const box = $("#recharge-tiers");
    const note = $("#recharge-note");
    if (!box) return;
    const hqEl = document.querySelector('#create-form input[name="high_quality_mode"]');
    const hqOn = hqEl && hqEl.checked;
    const per10k = hqOn
      ? ((pricingCache && pricingCache.price_per_10k_hq != null) ? pricingCache.price_per_10k_hq : 3)
      : ((pricingCache && pricingCache.price_per_10k != null) ? pricingCache.price_per_10k : 2);
    box.innerHTML =
      '<div class="recharge-contact">' +
        "<b>充值请联系站长</b>" +
      "</div>";
    note.textContent = "按字数计费：每万字 " + per10k + " 元，按目标 AI 字数封顶，多写不多收。";
  }

  // ---------- 视图切换 ----------
  function switchView(view) {
    currentView = view;
    $$(".view").forEach((v) => v.classList.remove("active"));
    $("#view-" + view).classList.add("active");
    $$(".nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    if (view === "projects") loadProjects();
    if (view === "create") initCreateForm();
    if (view === "detail") openDetail();
  }

  $$(".nav-btn").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
  $("#btn-new-top").addEventListener("click", () => switchView("create"));
  $("#btn-create-cancel").addEventListener("click", () => switchView("projects"));

  // ---------- 项目列表 ----------
  function projectState(p) {
    if (p.running) return "running";
    if (p.has_error) return "error";
    if (p.chapters_total && p.chapters_done >= p.chapters_total) return "done";
    if (p.chapters_done > 0) return "paused";
    return "ready";
  }
  function stateBadge(p) {
    const st = projectState(p);
    const names = { running: "生成中", done: "已完成", error: "出错", ready: "未开始", paused: "已暂停" };
    return '<span class="badge ' + st + '">' + names[st] + "</span>";
  }

  function applyProjectFilter() {
    const kw = ($("#project-search").value || "").trim().toLowerCase();
    const f = (document.querySelector("#project-filters button.active") || {}).dataset?.filter || "all";
    let list = projectsCache;
    if (f !== "all") list = list.filter((p) => projectState(p) === f);
    if (kw) {
      list = list.filter((p) =>
        (p.title || "").toLowerCase().includes(kw) ||
        (p.genre || "").toLowerCase().includes(kw) ||
        String(p.chapters_done).includes(kw)
      );
    }
    const grid = $("#project-list");
    if (!list.length) {
      grid.innerHTML = '<div class="empty"><span class="empty-seal">墨</span>' +
        (projectsCache.length ? "没有符合条件的作品" : "书案还空着，点击「＋ 新建小说」开始创作") + "</div>";
      return;
    }
    grid.innerHTML = list.map((p) => {
      const pct = p.chapters_total ? Math.round((p.chapters_done / p.chapters_total) * 100) : 0;
      return (
        '<div class="project-card" data-id="' + esc(p.id) + '">' +
          '<h3>' + esc(p.title) + '</h3>' +
          '<div class="genre">' + esc(p.genre) + ' · ' + fmtWords(p.total_words) + " · " + fmtTime(p.created) + '</div>' +
          '<div class="p-meta"><span>' + p.chapters_done + '/' + p.chapters_total + ' 章（' + pct + '%）</span>' + stateBadge(p) + '</div>' +
          (p.price > 0
            ? '<div class="p-price">创作扣费 <b>' + fmtPrice(p.price) + " 元</b></div>"
            : "") +
          (p.price > 0 && p.chapters_done === 0
            ? '<button class="btn small p-refund" data-id="' + esc(p.id) + '" data-title="' + esc(p.title) + '" data-price="' + esc(String(p.price)) + '">退费删除</button>'
            : '') +
        '</div>'
      );
    }).join("");
    grid.querySelectorAll(".project-card").forEach((card) => {
      card.addEventListener("click", () => {
        currentProjectId = card.dataset.id;
        switchView("detail");
      });
    });
    // 退费删除（仅未开始生成的项目）：阻止冒泡，确认后调用退费接口
    grid.querySelectorAll(".p-refund").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const pid = btn.dataset.id;
        const title = btn.dataset.title;
        const price = Number(btn.dataset.price || 0);
        if (!window.confirm("确认退费并删除《" + title + "》？\n将退回 " + price.toFixed(2) + " 元到余额，且删除该项目（不可恢复）。")) return;
        try {
          btn.disabled = true;
          const r = await api("/api/projects/" + pid + "/refund", "POST");
          toast("已退费 " + (r.refunded || 0).toFixed(2) + " 元，项目已删除");
          loadProjects();
          loadMe();
        } catch (err) {
          toast("退费失败：" + (err.message || "请稍后重试"));
          btn.disabled = false;
        }
      });
    });
  }

  async function loadProjects() {
    try {
      const { projects } = await api("/api/projects");
      projectsCache = projects;
      applyProjectFilter();
    } catch (e) {
      toast("加载项目失败：" + e.message);
    }
  }

  // 搜索 & 筛选
  $("#project-search").addEventListener("input", applyProjectFilter);
  document.querySelectorAll("#project-filters button").forEach((b) => {
    b.addEventListener("click", () => {
      document.querySelectorAll("#project-filters button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      applyProjectFilter();
    });
  });

  // ---------- 新建 ----------
  async function initCreateForm() {
    // 题材下拉
    const sel = $("#genre-select");
    if (!sel.dataset.loaded) {
      try {
        const { genres } = await api("/api/genres");
        sel.innerHTML = genres.map((g) => '<option value="' + esc(g) + '">' + esc(g) + "</option>").join("");
        sel.dataset.loaded = "1";
      } catch (e) { /* ignore */ }
    }
    // 写作风格下拉
    const skillSel = $("#skill-select");
    if (skillSel && !skillSel.dataset.loaded) {
      try {
        const { skills } = await api("/api/skills");
        skillSel.innerHTML = '<option value="0">默认（防同质化通用）</option>' +
          (skills || []).map((s) => '<option value="' + s.id + '">' + esc(s.name) + (s.description ? " · " + esc(s.description) : "") + "</option>").join("");
        skillSel.dataset.loaded = "1";
      } catch (e) { /* ignore */ }
    }
    // 价格/费用联动（随字数变化）
    const wordsInput = document.querySelector('#create-form input[name="total_words"]');
    if (wordsInput && !wordsInput.dataset.bound) {
      wordsInput.dataset.bound = "1";
      wordsInput.addEventListener("input", updateFee);
    }
    // 已有内容 textarea 字数变化时同步刷新费用（AI 生成字数 = 目标 - 已有）
    const userTaBind = document.querySelector('#create-form textarea[name="user_content"]');
    if (userTaBind && !userTaBind.dataset.bound) {
      userTaBind.dataset.bound = "1";
      userTaBind.addEventListener("input", updateFee);
    }
    updateFee();
    const hqSwitch = document.querySelector('#create-form input[name="high_quality_mode"]');
    if (hqSwitch && !hqSwitch.dataset.bound) {
      hqSwitch.dataset.bound = '1';
      hqSwitch.addEventListener('change', () => {
        const hint = $('#hq-hint');
        if (hint) hint.classList.toggle('hidden', !hqSwitch.checked);
        updateFee();
      });
    }
    // 模式切换：auto=智能生成（现有工作流），expand=AI 扩写（含续写/扩写已有内容）
    const modeRadios = $$('#create-form input[name="mode"]');
    const expandWrap = $("#expand-input-wrap");
    const expandHideEls = $$('#create-form [data-expand-hide]');
    const applyMode = () => {
      const expand = (document.querySelector('#create-form input[name="mode"]:checked') || {}).value === "expand";
      expandWrap.classList.toggle("hidden", !expand);
      expandHideEls.forEach((el) => el.classList.toggle("hidden", expand));
      const lbl = expand ? "AI 扩写模式（大纲扩写 / 续写已有内容 / 扩写已有章节）" : "智能生成模式（联网调研 → 自动大纲 → 逐章）";
      const meta = $("#create-form .mode-label");
      if (meta) meta.textContent = lbl;
      applyContentType();
    };
    // 内容类型切换：outline=大纲扩写；chapters=续写；chapters_expand=扩写已有章节
    const applyContentType = () => {
      const ct = (document.querySelector('#create-form input[name="content_type"]:checked') || {}).value || "outline";
      $$('#create-form [data-ct]').forEach((el) => el.classList.toggle("hidden", el.dataset.ct !== ct));
    };
    $$('#create-form input[name="content_type"]').forEach((r) => r.addEventListener("change", applyContentType));
    modeRadios.forEach((r) => r.addEventListener("change", applyMode));
    applyMode();
  }

  // 费用明细：右侧面板 + 顶部简提示（按字数实时计费：每万字 price_per_10k 元）
  async function updateFee() {
    if (!pricingCache) {
      try {
        const r = await api("/api/pricing");
        pricingCache = r;
      } catch (e) { return; }
    }
    const wordsInput = document.querySelector('#create-form input[name="total_words"]');
    const totalWords = parseInt(wordsInput && wordsInput.value, 10) || 100000;
    // 已有内容字数（续写/扩写模式下用户粘贴或上传的正文），这部分不计费
    const userTa = document.querySelector('#create-form textarea[name="user_content"]');
    const userWords = userTa ? (userTa.value || "").length : 0;
    const aiWords = Math.max(0, totalWords - userWords);
    const billWords = Math.max(1, aiWords);
    const per10k = (pricingCache && pricingCache.price_per_10k != null) ? pricingCache.price_per_10k : 2;
    const price = Math.round(billWords / 10000 * per10k * 100) / 100;
    const tiers = pricingCache.tiers || [];
    let tier = tiers[tiers.length - 1];
    for (const t of tiers) {
      if (t.max_words == null || billWords <= t.max_words) { tier = t; break; }
    }
    if (!tier) return;
    const hasSale = tier.original_price != null && tier.original_price > price;
    // 顶部简提示：已按用户要求去掉，费用信息统一在右侧面板展示
    const tip = $("#price-tip");
    if (tip) { tip.innerHTML = ""; tip.classList.add("hidden"); }
    // 右侧费用明细
    const fee = $("#fee-detail");
    if (fee) {
      let rows =
        '<div class="fee-row"><span>目标总字数</span><b>' + fmtWords(totalWords) + "</b></div>" +
        (userWords > 0
          ? '<div class="fee-row"><span>已有内容</span><b>' + fmtWords(userWords) + "</b></div>" +
            '<div class="fee-row"><span>AI 需生成</span><b>' + fmtWords(aiWords) + "</b></div>"
          : '<div class="fee-row"><span>AI 需生成</span><b>' + fmtWords(aiWords) + "</b></div>") +
        '<div class="fee-row"><span>计费方式</span><b>每万字 ' + per10k + " 元</b></div>";
      rows += '<div class="fee-row"><span>本次费用</span><b class="gold">' + fmtPrice(price) + " 元</b></div>";
      rows += '<div class="fee-total"><span>本次扣费</span><b>' + fmtPrice(price) + "</b></div>" +
        '<div class="fee-hint">按目标 AI 字数封顶计费（每万字 ' + per10k + " 元），已有内容不重复计费；创建时不扣费，生成完成后按目标 AI 字数结算。🎉 福利：AI 多写的部分不另收费，写超了也是您赚。未生成的项目可随时删除，不产生费用。</div>";
      fee.innerHTML = rows;
    }
  }

  // ---------- 上传大纲智能解析 ----------
  const outlineFile = $("#outline-file");
  const parseBtn = $("#btn-parse-outline");
  const parseStatus = $("#parse-status");
  const setParseStatus = (msg, isErr) => {
    parseStatus.textContent = msg;
    parseStatus.className = "parse-status" + (isErr ? " parse-error" : "");
  };
  const setFormVal = (name, val) => {
    const el = document.querySelector('#create-form [name="' + name + '"]');
    if (el && val != null && String(val) !== "") {
      el.value = val;
      // 触发价格联动（total_words 变化时）
      if (name === "total_words") el.dispatchEvent(new Event("input"));
    }
  };
  // ---------- 上传/粘贴已有内容 → 切分预览（续写/扩写模式） ----------
  const contentFile = $("#content-file");
  const contentParseBtn = $("#btn-parse-content");
  const contentParseStatus = $("#content-parse-status");
  const contentPreview = $("#content-preview");
  const userContentTa = document.querySelector('#create-form textarea[name="user_content"]');
  const setContentStatus = (msg, isErr) => {
    if (!contentParseStatus) return;
    contentParseStatus.textContent = msg;
    contentParseStatus.className = "parse-status" + (isErr ? " parse-error" : "");
  };
  const renderContentPreview = (r) => {
    if (!contentPreview) return;
    if (!r || !r.chapters || !r.chapters.length) {
      contentPreview.classList.add("hidden");
      return;
    }
    const totalChars = (r.total_chars || 0);
    let html = '<div class="content-preview-head">识别到 <b>' + r.chapters.length +
      "</b> 个章节，共 " + (totalChars >= 10000 ? (totalChars / 10000).toFixed(1) + " 万字" : totalChars + " 字") + "</div>";
    html += r.chapters.map((c) =>
      '<div class="cp-row"><span class="cp-no">' + c.no + '.</span>' +
      '<span class="cp-title">' + esc(c.title) + "</span>" +
      '<span class="cp-chars">' + c.chars + " 字</span>" +
      '<div class="cp-preview">' + esc(c.preview || "") + "…</div></div>"
    ).join("");
    html += '<div class="cp-tip">识别结果仅供参考，实际以服务端切分为准；粘贴/上传后可直接创建。</div>';
    contentPreview.innerHTML = html;
    contentPreview.classList.remove("hidden");
  };
  if (contentParseBtn && contentFile) {
    contentParseBtn.addEventListener("click", async () => {
      const file = contentFile.files[0];
      if (!file) {
        if (userContentTa && userContentTa.value.trim()) {
          setContentStatus("解析粘贴内容中…", false);
          try {
            const resp = await fetch("/api/parse-content", {
              method: "POST",
              headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": "Bearer " + getToken(),
              },
              body: "text=" + encodeURIComponent(userContentTa.value),
            });
            const r = await resp.json();
            if (!resp.ok || !r.ok) throw new Error(r.detail || "解析失败");
            renderContentPreview(r);
            setContentStatus("已解析粘贴内容", false);
          } catch (e) {
            setContentStatus("解析失败：" + e.message, true);
          }
          return;
        }
        setContentStatus("请先粘贴内容或选择文件", true);
        return;
      }
      if (file.size > 5 * 1024 * 1024) { setContentStatus("文件超过 5MB 限制", true); return; }
      contentParseBtn.disabled = true;
      setContentStatus("解析中…", false);
      try {
        // 1) 上传解析（docx/txt/md 由服务端统一提取文本，避免前端按 UTF-8 读二进制导致乱码）
        const fd = new FormData();
        fd.append("file", file);
        const resp = await fetch("/api/parse-content", {
          method: "POST",
          headers: { "Authorization": "Bearer " + getToken() },
          body: fd,
        });
        const r = await resp.json();
        if (!resp.ok || !r.ok) throw new Error(r.detail || "解析失败");
        // 2) 用服务端解析出的完整文本填入"已有内容"（创建时以 JSON 提交全文）
        if (userContentTa && r.full_text) userContentTa.value = r.full_text;
        renderContentPreview(r);
        updateFee();
        setContentStatus("解析完成：识别到 " + r.chapters.length + " 个章节", false);
      } catch (e) {
        setContentStatus("解析失败：" + e.message, true);
      } finally {
        contentParseBtn.disabled = false;
      }
    });
  }

  if (parseBtn && outlineFile) {
    parseBtn.addEventListener("click", async () => {
      const file = outlineFile.files[0];
      if (!file) { setParseStatus("请先选择大纲文件", true); return; }
      if (file.size > 2 * 1024 * 1024) { setParseStatus("文件超过 2MB 限制", true); return; }
      parseBtn.disabled = true;
      setParseStatus("AI 解析中…（约 10-30 秒）", false);
      try {
        const fd = new FormData();
        fd.append("file", file);
        const resp = await fetch("/api/parse-outline", {
          method: "POST",
          headers: { "Authorization": "Bearer " + getToken() },
          body: fd,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          setParseStatus((data.detail || "解析失败，请稍后重试"), true);
          return;
        }
        const d = data.data || {};
        // 题材：解析值在列表中则选中，否则追加为自定义选项
        const genreSel = $("#genre-select");
        const g = (d.genre || "").trim();
        if (g) {
          let hit = Array.from(genreSel.options).find((o) => o.value === g);
          if (!hit) {
            const opt = document.createElement("option");
            opt.value = g; opt.textContent = g;
            genreSel.appendChild(opt);
          }
          genreSel.value = g;
        }
        setFormVal("title", d.title);
        setFormVal("total_words", d.total_words);
        setFormVal("chapter_words", d.chapter_words);
        setFormVal("style", d.style);
        setFormVal("premise", d.premise);
        setFormVal("background", d.background);
        setFormVal("hero", d.hero);
        if (Array.isArray(d.characters) && d.characters.length) {
          setFormVal("characters", d.characters.join("\n"));
        }
        setFormVal("plot_direction", d.plot_direction);
        // 扩写模式：把原文也放入扩写输入框，方便直接走 AI 扩写
        if (d.plot_direction || d.background) {
          const expandWrap = $("#expand-input-wrap");
          if (expandWrap && !expandWrap.classList.contains("hidden")) {
            const expandInput = document.querySelector('#create-form [name="expand_input"]');
            if (expandInput) {
              const parts = [d.premise, d.background, d.hero, d.plot_direction].filter(Boolean);
              expandInput.value = parts.join("\n\n");
            }
          }
        }
        setParseStatus("解析完成，已填充表单，请核对修改", false);
      } catch (e) {
        setParseStatus("网络错误：" + (e.message || "请重试"), true);
      } finally {
        parseBtn.disabled = false;
      }
    });
  }

  $("#create-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errBox = $("#create-error");
    errBox.classList.add("hidden");
    const f = e.target;
    const characters = f.characters.value.split("\n").map((s) => s.trim()).filter(Boolean);
    const forbidden = f.forbidden.value.split("\n").map((s) => s.trim()).filter(Boolean);
    const mode = (f.querySelector('input[name="mode"]:checked') || {}).value || "auto";
    const expandInput = f.expand_input ? f.expand_input.value.trim() : "";
    const content_type = mode === "expand"
      ? ((f.querySelector('input[name="content_type"]:checked') || {}).value || "outline") : "";
    const userContent = f.user_content ? f.user_content.value.trim() : "";

    if (mode === "expand") {
      if (content_type === "chapters" || content_type === "chapters_expand") {
        if (!userContent) {
          errBox.textContent = "创建失败：续写/扩写模式必须粘贴已有内容或上传文件";
          errBox.classList.remove("hidden");
          return;
        }
      } else if (!expandInput) {
        errBox.textContent = "创建失败：AI 扩写模式必须填写『大纲或简要内容』";
        errBox.classList.remove("hidden");
        return;
      }
    }

    const payload = {
      title: f.title.value.trim(),
      genre: f.genre.value,
      total_words: parseInt(f.total_words.value, 10),
      chapter_words: parseInt(f.chapter_words.value, 10),
      style: f.style.value.trim(),
      premise: f.premise.value.trim(),
      background: f.background.value.trim(),
      hero: f.hero.value.trim(),
      characters,
      plot_direction: f.plot_direction.value.trim(),
      forbidden,
      editor_review: f.editor_review.checked,
      de_ai: f.de_ai.checked,
      anti_similarity: f.anti_similarity.checked,
      character_depth: f.character_depth.checked,
      high_quality_mode: f.high_quality_mode ? f.high_quality_mode.checked : false,
      confirm: false,
      memory_window: 5,
      mode,
      expand_input: expandInput,
      user_content: content_type === "chapters" || content_type === "chapters_expand" ? userContent : "",
      content_type: content_type,
      expand_targets: [],
      skill_id: f.skill_id ? parseInt(f.skill_id.value, 10) || 0 : 0,
      llm: {
        provider: "deepseek",  // Key 由服务端统一管理
        model: "",
        base_url: "",
        temperature: 0.85,
        top_p: 0.8,
        max_tokens: 8000,
      },
      search: { provider: "bocha", api_key: "" },
    };

    try {
      const btn = $("#create-form button[type=submit]");
      btn.disabled = true;
      const res = await api("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      // 更新余额
      me.balance = res.balance;
      renderUserBar();
      toast("项目「" + res.title + "」创建成功（预估费用 " + fmtPrice(res.price) + " 元，生成完成后按实际字数扣费）");
      currentProjectId = res.id;
      switchView("detail");
    } catch (err) {
      errBox.textContent = "创建失败：" + err.message;
      errBox.classList.remove("hidden");
    } finally {
      $("#create-form button[type=submit]").disabled = false;
    }
  });

  // ---------- 详情 ----------
  async function openDetail() {
    if (!currentProjectId) return;
    $("#detail-title").textContent = "加载中…";
    $("#btn-download").disabled = true;
    chaptersCache = [];
    window._detailLoaded = false;
    $("#chapter-list").innerHTML = "";
    await refreshDetail();
    startPolling();
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(refreshDetail, 4000);
  }
  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  // 封面图片加载：/cover 接口需 Bearer 鉴权，<img src> 无法携带 Header，
  // 故用 fetch 拉取图片转 objectURL 后赋给 img（带时间戳防缓存、自动 revoke 旧 URL）。
  async function loadCover(projectId, imgEl) {
    try {
      const resp = await fetch(API + "/api/projects/" + encodeURIComponent(projectId) + "/cover?t=" + Date.now(), {
        headers: { Authorization: "Bearer " + getToken() },
      });
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const blob = await resp.blob();
      if (imgEl.dataset.coverUrl) URL.revokeObjectURL(imgEl.dataset.coverUrl);
      const url = URL.createObjectURL(blob);
      imgEl.dataset.coverUrl = url;
      imgEl.src = url;
      return true;
    } catch (e) {
      return false;
    }
  }

  async function refreshDetail() {
    try {
      const d = await api("/api/projects/" + encodeURIComponent(currentProjectId));
      detailCache = d;
      renderDetail(d);
      // 首次进入或章节数变化时刷新章节列表
      const chs = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters");
      if (!chaptersCache.length || chs.chapters.length !== chaptersCache.length) {
        chaptersCache = chs.chapters;
        renderChapters();
      }
      // 大纲从无到有时，重新加载大纲/世界观tab
      if (d.has_outline && !window._outlineLoadedOnce) {
        window._outlineLoadedOnce = true;
        loadOutlineAndWorld();
      }
      if (!d.running && d.chapters_total > 0 && d.chapters_done >= d.chapters_total) {
        stopPolling();
      }
    } catch (e) {
      // 忽略瞬时错误
    }
  }

  // 六阶段进度映射：调研 → 大纲 → 人物 → 正文 → 质检 → 导出
  function calcStage(d) {
    const total = d.chapters_total || 0;
    const done = d.chapters_done || 0;
    if (d.has_error) {
      return { stage: done > 0 ? 3 : 1, pct: done > 0 ? Math.round(done / total * 100) : 0, label: "生成出错" };
    }
    if (total === 0) return { stage: 0, pct: 0, label: "等待开始" };
    if (done >= total) {
      return { stage: d.running ? 4 : 5, pct: d.running ? 94 : 100, label: d.running ? "质检 / 导出中" : "已完结 · 可下载" };
    }
    if (done > 0) return { stage: 3, pct: Math.round(done / total * 100), label: "正文创作中" };
    return { stage: d.running ? 1 : 0, pct: d.running ? 30 : 0, label: d.running ? "世界构建中（调研 → 大纲 → 人物）" : "等待开始" };
  }

  function renderPipeline(d) {
    const s = calcStage(d);
    $("#pipe-fill").style.width = s.pct + "%";
    $("#progress-pct").textContent = s.pct + "%";
    $("#progress-text").textContent = (d.chapters_total ? d.chapters_done + " / " + d.chapters_total + " 章 · " : "") + s.label;
    document.querySelectorAll("#pipe-steps .pipe-step").forEach((el) => {
      const i = parseInt(el.dataset.s, 10);
      el.classList.toggle("done", i < s.stage);
      el.classList.toggle("active", i === s.stage);
    });
  }

  function renderDetail(d) {
    const cfg = d.config;
    $("#detail-title").textContent = cfg.title;
    const meta = $("#detail-meta");
    meta.innerHTML =
      '<span>题材：' + esc(cfg.genre) + "</span>" +
      '<span>模式：' + (cfg.mode === "expand" ? "AI 扩写" : "智能生成") + "</span>" +
      '<span>目标：' + fmtWords(cfg.total_words) + "</span>" +
      '<span>章节：' + cfg.num_chapters + " 章</span>" +
      '<span>单章：约 ' + cfg.chapter_words + " 字</span>" +
      '<span>模型：' + esc(cfg.llm.provider) + "</span>" +
      '<span>已写：' + fmtWords(d.generated_words) + "</span>";

    // 封面
    const coverImg = $("#cover-img");
    const coverEmpty = $("#cover-empty");
    if (d.has_cover) {
      coverEmpty.classList.add("hidden");
      coverImg.classList.remove("hidden");
      loadCover(currentProjectId, coverImg);
    } else {
      coverImg.classList.add("hidden");
      coverEmpty.classList.remove("hidden");
    }
    // 封面生成按钮：显示剩余次数
    const coverBtn = $("#btn-gen-cover");
    if (coverBtn) {
      const used = d.cover_used || 0;
      const max = d.cover_max || 5;
      const left = max - used;
      if (left > 0) {
        coverBtn.disabled = false;
        coverBtn.textContent = `🖼 生成封面（剩 ${left}/${max} 次）`;
      } else {
        coverBtn.disabled = true;
        coverBtn.textContent = `🖼 封面次数已用完（${used}/${max}）`;
      }
    }

    // 六阶段进度条
    renderPipeline(d);

    const running = d.running;
    const hasOutline = d.has_outline;
    const hasChapters = d.chapters_done > 0;
    // 生成大纲按钮：没在跑且没大纲时可用
    $("#btn-gen-outline").disabled = running || hasOutline;
    // 开始生成正文按钮：没在跑且有大纲时可用（没有大纲时先引导生成大纲）
    $("#btn-start").disabled = running || !hasOutline;
    $("#btn-stop").disabled = !running;
    // 下载：有任一格式即可用；未生成的格式现场导出
    $("#btn-download").disabled = !(d.has_txt || d.has_epub || d.has_docx || d.chapters_done > 0);
    $("#dl-format").disabled = $("#btn-download").disabled;
    const statusEl = $("#run-status");
    statusEl.className = "run-status";
    if (running) {
      statusEl.textContent = "⏳ 正在生成…（自动刷新）";
    } else if (d.error) {
      statusEl.textContent = "⚠ 出错：" + d.error;
      statusEl.classList.add("err");
    } else if (d.chapters_done >= d.chapters_total && d.chapters_total > 0) {
      statusEl.textContent = "✅ 已完成";
      statusEl.classList.add("ok");
    } else if (d.chapters_done > 0) {
      statusEl.textContent = "⏸ 已暂停（进度已保存，可继续）";
    } else if (!hasOutline) {
      statusEl.textContent = "📋 请先生成大纲，确认后再开始写正文";
    } else {
      statusEl.textContent = "✅ 大纲已就绪，可编辑后开始生成正文";
    }

    const errBox = $("#detail-error");
    if (d.error) { errBox.textContent = "生成出错：" + d.error; errBox.classList.remove("hidden"); }
    else errBox.classList.add("hidden");

    // 加载大纲/世界观（首次或大纲就绪后）
    if (!window._outlineLoadedOnce && d.has_outline) {
      window._outlineLoadedOnce = true;
      loadOutlineAndWorld();
    }
  }

  async function loadOutlineAndWorld() {
    try {
      const o = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline");
      const ob = $("#outline-box");
      ob.innerHTML = "";
      if (o.title) {
        ob.innerHTML = '<div class="ol-intro"><strong>《' + esc(o.title) + '》</strong><br>' + esc(o.intro) + "</div>";
      }
      ob.innerHTML += o.chapters.map((c) =>
        '<div class="ol-ch">第' + c.no + '章《' + esc(c.title) + '》<span class="goal">——' + esc(c.goal) + "</span></div>"
      ).join("");
      // 大纲与已生成章节差异提示（改动章节 + 废弃章节）
      try {
        const d = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline/diff");
        window._outlineDiff = d;
        const warns = [];
        if (d.changed && d.changed.length) warns.push("有 " + d.changed.length + " 章内容与新大纲不一致，可在章节列表重写");
        if (d.removed && d.removed.length) warns.push(d.removed.length + " 章已从大纲移除（正文文件保留，标记已废弃）");
        if (warns.length) ob.innerHTML = '<div class="ol-warn">' + warns.map((w) => "<div>⚠ " + esc(w) + "</div>").join("") + "</div>" + ob.innerHTML;
      } catch (e) { /* diff 不可用时忽略 */ }

      const w = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/world");
      const wb = $("#world-box");
      let html = "<h4>世界观</h4><p>" + esc(w.world_view) + "</p>";
      if (w.characters && w.characters.length) {
        html += "<h4>人物</h4>" + w.characters.map((c) =>
          '<div class="char"><span class="name">' + esc(c.name) + '</span><span class="role">' + esc(c.role) + '</span><div>' + esc(c.desc) + "</div></div>"
        ).join("");
      }
      if (w.foreshadowing && w.foreshadowing.length) {
        html += "<h4>伏笔</h4><ul>" + w.foreshadowing.map((f) => "<li>" + esc(f) + "</li>").join("") + "</ul>";
      }
      wb.innerHTML = html;
    } catch (e) { /* ignore */ }
  }

  async function renderChapters() {
    try {
      if (!chaptersCache.length) {
        const chs = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters");
        chaptersCache = chs.chapters;
      }
    } catch (e) { return; }
    // 大纲差异：changed 章节加"大纲已改"标记
    let diff = { changed: [], removed: [] };
    try {
      diff = window._outlineDiff || await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline/diff");
      window._outlineDiff = diff;
    } catch (e) { /* ignore */ }
    const total = detailCache ? detailCache.chapters_total : 0;
    const list = $("#chapter-list");
    const items = [];
    for (let i = 1; i <= total; i++) {
      const c = chaptersCache.find((x) => x.no === i);
      if (c) {
        items.push(
          '<div class="ch-item" data-no="' + i + '">' +
            '<div class="ch-no">第 ' + i + " 章</div>" +
            '<div class="ch-title">' + esc(c.title) +
            (diff.changed.indexOf(i) >= 0 ? ' <span class="ch-badge">⚡ 大纲已改</span>' : "") +
            (diff.removed.indexOf(i) >= 0 ? ' <span class="ch-badge danger">已废弃</span>' : "") + "</div>" +
            '<div class="ch-words">' + c.words + " 字 · " + (c.summary ? esc(c.summary.slice(0, 36)) + "…" : "") + "</div>" +
          "</div>"
        );
      } else {
        items.push(
          '<div class="ch-item placeholder" data-no="' + i + '">' +
            '<div class="ch-no">第 ' + i + " 章</div>" +
            '<div class="ch-title">尚未生成</div>' +
          "</div>"
        );
      }
    }
    list.innerHTML = items.join("");
    list.querySelectorAll(".ch-item[data-no]").forEach((el) => {
      el.addEventListener("click", async () => {
        const no = parseInt(el.dataset.no, 10);
        if (!chaptersCache.find((x) => x.no === no)) {
          toast("该章节还未生成");
          return;
        }
        currentChapterNo = no;
        await openReader(no);
      });
    });
    // 同步阅读器目录
    renderReaderToc();
  }

  // ---------- 阅读器 ----------
  // 把模型偶尔夹带的 HTML 标签（<p>/</p>/<br> 等）转成段落/换行，再常规渲染
  function cleanContent(raw) {
    let t = String(raw || "");
    // 块级/换行标签 → 换行
    t = t.replace(/<\/?(?:p|br|div|section|h[1-6])\s*\/?>/gi, "\n");
    // 其余标签直接删除
    t = t.replace(/<[^>]+>/g, "");
    // 压缩多余空行
    t = t.replace(/\n{3,}/g, "\n\n");
    return t;
  }

  function renderReaderToc() {
    const sel = $("#reader-toc");
    if (!sel) return;
    const total = detailCache ? detailCache.chapters_total : 0;
    let html = '<option value="">目录…</option>';
    for (let i = 1; i <= total; i++) {
      const c = chaptersCache.find((x) => x.no === i);
      html += '<option value="' + i + '">第 ' + i + " 章" + (c ? " " + esc(c.title) : "（未生成）") + "</option>";
    }
    sel.innerHTML = html;
    sel.value = currentChapterNo ? String(currentChapterNo) : "";
  }

  async function openReader(no) {
    try {
      const ch = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters/" + no);
      $("#reader-title").textContent = "第 " + ch.no + " 章 " + ch.title;
      const body = $("#reader-body");
      body.innerHTML = cleanContent(ch.content).split(/\n+/).map((p) => p.trim()).filter(Boolean).map((p) => "<p>" + esc(p) + "</p>").join("");
      // 应用字号与主题
      body.style.fontSize = readerFont + "px";
      body.className = "reader-body theme-" + readerTheme;
      document.querySelectorAll("#reader-themes .theme-btn").forEach((b) =>
        b.classList.toggle("active", b.dataset.theme === readerTheme));
      $("#reader-fontsize-label").textContent = readerFont;
      $("#reader-modal").classList.remove("hidden");
      body.scrollTop = 0;
      updateReaderNav(no);
      renderReaderToc();
      $("#btn-regen-from").disabled = false;
    } catch (e) {
      toast("读取章节失败：" + e.message);
    }
  }

  function updateReaderNav(no) {
    $("#btn-prev-ch").disabled = !chaptersCache.some((x) => x.no === no - 1);
    $("#btn-next-ch").disabled = !chaptersCache.some((x) => x.no === no + 1);
  }

  $("#btn-close-reader").addEventListener("click", () => $("#reader-modal").classList.add("hidden"));
  $("#btn-prev-ch").addEventListener("click", async () => {
    if (currentChapterNo > 1 && chaptersCache.some((x) => x.no === currentChapterNo - 1)) {
      currentChapterNo -= 1;
      await openReader(currentChapterNo);
    }
  });
  $("#btn-next-ch").addEventListener("click", async () => {
    if (chaptersCache.some((x) => x.no === currentChapterNo + 1)) {
      currentChapterNo += 1;
      await openReader(currentChapterNo);
    }
  });

  // 目录跳转
  $("#reader-toc").addEventListener("change", async (e) => {
    const no = parseInt(e.target.value, 10);
    if (!no) return;
    if (!chaptersCache.find((x) => x.no === no)) { toast("该章节还未生成"); return; }
    currentChapterNo = no;
    await openReader(no);
  });

  // 字号调节
  function applyReaderFont() {
    const body = $("#reader-body");
    if (body) body.style.fontSize = readerFont + "px";
    const lbl = $("#reader-fontsize-label");
    if (lbl) lbl.textContent = readerFont;
    localStorage.setItem("novel_reader_font", String(readerFont));
  }
  $("#btn-font-up").addEventListener("click", () => { readerFont = Math.min(28, readerFont + 1); applyReaderFont(); });
  $("#btn-font-down").addEventListener("click", () => { readerFont = Math.max(12, readerFont - 1); applyReaderFont(); });

  // 阅读主题
  document.querySelectorAll("#reader-themes .theme-btn").forEach((b) => {
    b.addEventListener("click", () => {
      readerTheme = b.dataset.theme;
      localStorage.setItem("novel_reader_theme", readerTheme);
      document.querySelectorAll("#reader-themes .theme-btn").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      const body = $("#reader-body");
      body.className = "reader-body theme-" + readerTheme;
    });
  });

  // 键盘翻页（阅读器打开时）
  document.addEventListener("keydown", (e) => {
    const modal = $("#reader-modal");
    if (!modal || modal.classList.contains("hidden")) return;
    if (e.key === "ArrowRight") $("#btn-next-ch").click();
    else if (e.key === "ArrowLeft") $("#btn-prev-ch").click();
  });

  // ---------- 单章人工修改后继续生成 ----------
  // 打开编辑弹层：加载当前章节内容
  $("#btn-edit-chapter").addEventListener("click", async () => {
    if (!currentChapterNo) return;
    try {
      const ch = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters/" + currentChapterNo);
      $("#edit-title").textContent = "第 " + ch.no + " 章 编辑";
      $("#edit-ch-title").value = ch.title;
      $("#edit-ch-content").value = cleanContent(ch.content);
      $("#edit-hint").textContent = "";
      $("#edit-modal").classList.remove("hidden");
    } catch (e) {
      toast("打开编辑失败：" + e.message);
    }
  });
  $("#btn-close-edit").addEventListener("click", () => $("#edit-modal").classList.add("hidden"));

  // 保存编辑：更新章节（自动重新生成摘要），然后刷新
  $("#btn-save-edit").addEventListener("click", async () => {
    const no = currentChapterNo;
    const title = $("#edit-ch-title").value.trim();
    const content = $("#edit-ch-content").value;
    if (!content.trim()) { toast("正文不能为空"); return; }
    const btn = $("#btn-save-edit");
    btn.disabled = true;
    try {
      const res = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters/" + no, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title, content: content, summary: "", regen_summary: true }),
      });
      $("#edit-hint").textContent = "已保存（" + res.words + " 字）。后续生成会基于修改后的内容。";
      $("#edit-modal").classList.add("hidden");
      toast("第 " + no + " 章已保存");
      await refreshDetail();
      await renderChapters();
    } catch (e) {
      toast("保存失败：" + e.message);
    } finally {
      btn.disabled = false;
    }
  });

  // 从此章重新生成：删除本章及之后并重新开跑
  $("#btn-regen-from").addEventListener("click", async () => {
    if (!currentChapterNo) return;
    const no = currentChapterNo;
    if (!confirm("将从第 " + no + " 章开始重新生成，本章及之后的旧内容会被覆盖。确认继续？")) return;
    try {
      await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/regenerate-from/" + no, { method: "POST" });
      toast("已开始从第 " + no + " 章重新生成");
      $("#reader-modal").classList.add("hidden");
      startPolling();
      await refreshDetail();
    } catch (e) {
      toast("操作失败：" + e.message);
    }
  });

  // ---------- 大纲编辑（卡片式节点） ----------
  let outlineDraft = [];

  const NODE_FIELDS = [
    { key: "goal", label: "剧情目标", rows: 2, placeholder: "一句话说清本章发生什么" },
    { key: "core_event", label: "核心事件", rows: 2, placeholder: "本章主要发生的事件" },
    { key: "conflict", label: "冲突设计", rows: 2, placeholder: "主要矛盾与冲突升级点" },
    { key: "character_moves", label: "人物动线", rows: 2, placeholder: "出场人物、人物行为与成长" },
    { key: "foreshadowing", label: "伏笔/回收", rows: 2, placeholder: "埋设或回收的伏笔" },
    { key: "emotion", label: "情绪价值", rows: 1, placeholder: "爽点/虐点/悬念/笑点" },
    { key: "transition", label: "转场提示", rows: 1, placeholder: "与前后章衔接方式" },
    { key: "tag", label: "标签", rows: 1, placeholder: "高潮/过渡/爽点/反转/伏笔" },
  ];

  function renderOutlineEdit() {
    const list = $("#outline-edit-list");
    if (!list) return;
    list.innerHTML = outlineDraft.map((c, i) => {
      let fieldsHtml = '<div class="ol-card-fields">';
      fieldsHtml += '<input class="ol-card-title" value="' + esc(c.title || "") + '" placeholder="章节标题" data-i="' + i + '" data-key="title">';
      for (const f of NODE_FIELDS) {
        fieldsHtml += '<div class="ol-card-field">';
        fieldsHtml += '<label class="ol-card-label">' + f.label + '</label>';
        fieldsHtml += '<textarea rows="' + f.rows + '" placeholder="' + f.placeholder + '" data-i="' + i + '" data-key="' + f.key + '" class="ol-card-input">' + esc(c[f.key] || "") + '</textarea>';
        fieldsHtml += '</div>';
      }
      fieldsHtml += '</div>';
      return '<div class="ol-card" data-i="' + i + '">' +
        '<div class="ol-card-head"><span class="ol-card-no">第' + (i + 1) + '章</span><button class="btn danger small ol-edit-del" title="删除本章">✕</button></div>' +
        fieldsHtml +
      '</div>';
    }).join("");
    list.querySelectorAll(".ol-card").forEach((row) => {
      const i = parseInt(row.dataset.i, 10);
      row.querySelectorAll(".ol-card-title, .ol-card-input").forEach((inp) => {
        inp.addEventListener("input", (e) => { outlineDraft[i][e.target.dataset.key] = e.target.value; });
      });
      row.querySelector(".ol-edit-del").addEventListener("click", () => {
        if (!confirm("删除第 " + (i + 1) + " 章？已生成的该章正文会保留并标记废弃。")) return;
        outlineDraft.splice(i, 1);
        renderOutlineEdit();
      });
    });
  }

  $("#btn-edit-outline").addEventListener("click", async () => {
    if (!currentProjectId) return;
    try {
      const o = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline");
      outlineDraft = (o.chapters || []).map((c) => ({
        no: c.no || 0, title: c.title || "", goal: c.goal || "",
        timeline: c.timeline || "", core_event: c.core_event || "",
        conflict: c.conflict || "", character_moves: c.character_moves || "",
        foreshadowing: c.foreshadowing || "", emotion: c.emotion || "",
        transition: c.transition || "", tag: c.tag || "",
      }));
      renderOutlineEdit();
      $("#outline-hint").textContent = "";
      $("#outline-modal").classList.remove("hidden");
    } catch (e) {
      toast("打开大纲编辑失败：" + e.message);
    }
  });
  $("#btn-close-outline-modal").addEventListener("click", () => $("#outline-modal").classList.add("hidden"));
  $("#btn-outline-add-ch").addEventListener("click", () => { outlineDraft.push({ title: "", goal: "", core_event: "", conflict: "", character_moves: "", foreshadowing: "", emotion: "", transition: "", tag: "" }); renderOutlineEdit(); });

  $("#btn-save-outline").addEventListener("click", async () => {
    const btn = $("#btn-save-outline");
    btn.disabled = true;
    try {
      const chapters = outlineDraft.map((c) => ({
        title: (c.title || "").trim(),
        goal: (c.goal || "").trim(),
        core_event: (c.core_event || "").trim(),
        conflict: (c.conflict || "").trim(),
        character_moves: (c.character_moves || "").trim(),
        foreshadowing: (c.foreshadowing || "").trim(),
        emotion: (c.emotion || "").trim(),
        transition: (c.transition || "").trim(),
        tag: (c.tag || "").trim(),
      }));
      const res = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapters: chapters }),
      });
      $("#outline-modal").classList.add("hidden");
      const warns = [];
      if (res.changed_chapters && res.changed_chapters.length) {
        warns.push(res.changed_chapters.map((n) => "第" + n + "章").join("、") + "内容与新大纲不一致，可重写");
      }
      if (res.removed_chapters && res.removed_chapters.length) {
        warns.push(res.removed_chapters.length + " 章已从大纲移除（正文文件保留）");
      }
      toast("大纲已保存（共 " + res.total_chapters + " 章）" + (warns.length ? "；" + warns.join("；") : ""));
      window._outlineDiff = null;
      chaptersCache = [];
      await refreshDetail();
      await loadOutlineAndWorld();
      await renderChapters();
    } catch (e) {
      toast("保存失败：" + e.message);
    } finally {
      btn.disabled = false;
    }
  });

  // ---------- 单章重写（仅本章，后续不动） ----------
  $("#btn-rewrite-ch").addEventListener("click", async () => {
    if (!currentChapterNo) return;
    const no = currentChapterNo;
    if (!confirm("将按当前大纲目标重新生成第 " + no + " 章正文（仅本章，后续章节保持不变）。\n\n注意：若本章情节有重大变化，后续章节可能衔接不上，此时建议用「从此章重新生成」。确认继续？")) return;
    const btn = $("#btn-rewrite-ch");
    const oldText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "重写中…（约 1-2 分钟）";
    try {
      const res = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters/" + no + "/rewrite", { method: "POST" });
      toast("第 " + no + " 章已重写（" + res.words + " 字）");
      window._outlineDiff = null;
      chaptersCache = [];
      await refreshDetail();
      await renderChapters();
      await openReader(no);
    } catch (e) {
      toast("重写失败：" + e.message);
    } finally {
      btn.disabled = false;
      btn.textContent = oldText;
    }
  });

  // ---------- 标签页 ----------
  $$(".tab").forEach((tab) => {
    if (tab.dataset.ltab) return; // 登录 tab 已单独绑定
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      $$(".tab-panel").forEach((p) => p.classList.remove("active"));
      tab.classList.add("active");
      $("#tab-" + tab.dataset.tab).classList.add("active");
      if (tab.dataset.tab === "chapters") renderChapters();
    });
  });

  // ---------- 操作按钮 ----------
  $("#btn-back").addEventListener("click", () => {
    stopPolling();
    window._detailLoaded = false;
    chaptersCache = [];
    currentProjectId = null;
    switchView("projects");
  });

  $("#btn-gen-outline").addEventListener("click", async () => {
    try {
      await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/generate-outline", { method: "POST" });
      toast("开始生成大纲（调研→大纲→世界观设计）…");
      $("#btn-gen-outline").disabled = true;
      $("#btn-start").disabled = true;
      startPolling();
    } catch (e) {
      toast("启动大纲生成失败：" + e.message);
    }
  });

  $("#btn-start").addEventListener("click", async () => {
    try {
      await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/start", { method: "POST" });
      toast("已开始生成");
      $("#btn-start").disabled = true;
      $("#btn-stop").disabled = false;
      startPolling();
    } catch (e) {
      toast("启动失败：" + e.message);
    }
  });

  $("#btn-stop").addEventListener("click", async () => {
    try {
      await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/stop", { method: "POST" });
      toast("已请求停止，当前章节完成后生效");
      $("#btn-stop").disabled = true;
    } catch (e) {
      toast("停止失败：" + e.message);
    }
  });

  $("#btn-download").addEventListener("click", async () => {
    const fmt = $("#dl-format").value || "txt";
    try {
      // /download 接口需 Bearer 鉴权，<a href> 直链会 401 返回 JSON，故用 fetch 拉 blob 触发下载
      const resp = await fetch(API + "/api/projects/" + encodeURIComponent(currentProjectId) + "/download?format=" + encodeURIComponent(fmt), {
        headers: { Authorization: "Bearer " + getToken() },
      });
      if (!resp.ok) {
        let msg = "HTTP " + resp.status;
        try { const j = await resp.json(); if (j.detail) msg = j.detail; } catch (e) { /* ignore */ }
        throw new Error(msg);
      }
      const blob = await resp.blob();
      // 从 Content-Disposition 解析服务端文件名，取不到则回退书名.格式
      const cd = resp.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename\*?=(?:UTF-8''|utf-8'')?["']?([^"';]+)/i);
      let fname = m ? decodeURIComponent(m[1].trim()) : "";
      if (!fname) {
        const title = (detailCache && detailCache.config && detailCache.config.title) || "novel";
        fname = title + "." + fmt;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      toast("下载失败：" + e.message);
    }
  });

  // 生成封面（每本书最多 5 次，剩余次数直接显示在按钮上）
  $("#btn-gen-cover").addEventListener("click", async () => {
    try {
      const res = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/cover", { method: "POST" });
      // 生成成功后立即刷新封面（fetch 带鉴权转 objectURL）
      const coverImg = $("#cover-img");
      const coverEmpty = $("#cover-empty");
      if (coverImg && coverEmpty) {
        coverEmpty.classList.add("hidden");
        coverImg.classList.remove("hidden");
        await loadCover(currentProjectId, coverImg);
      }
      await refreshDetail();  // 刷新后按钮文字自动更新剩余次数
    } catch (e) {
      toast(e.message || "生成封面失败");
    }
  });

  // ---------- 启动 ----------
  (async function init() {
    // 推广链接 ?invite=CODE：预填注册邀请码
    try {
      const q = new URLSearchParams(location.search);
      const inv = (q.get("invite") || "").trim();
      if (inv && $("#reg-invite")) $("#reg-invite").value = inv;
    } catch (e) { /* ignore */ }
    // 若已登录 token，尝试恢复登录
    if (getToken()) {
      const ok = await loadMe();
      if (ok) { showApp(); return; }
    }
    showLogin();
    loadPricing();
  })();
})();
