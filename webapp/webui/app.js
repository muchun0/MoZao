/* AI 小说生成器 - 前端逻辑（原生 JS，无框架依赖）多用户版 */
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
    $("#balance-label").textContent = "余额 " + me.balance.toFixed(1) + " 元";
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
      $("#login-error").classList.add("hidden");
      $("#register-error").classList.add("hidden");
    });
  });

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

  $("#btn-logout").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (e) { /* ignore */ }
    setToken("");
    me = null;
    stopPolling();
    showLogin();
  });

  // 充值弹层
  $("#btn-recharge").addEventListener("click", () => {
    if (me) $("#recharge-username").textContent = me.username;
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
      box.innerHTML = '<hr style="margin:14px 0;border:none;border-top:1px solid var(--border)">' +
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
      (r.referral_bonus > 0 ? "；每个新用户通过你的链接注册，你获得 " + r.referral_bonus.toFixed(1) + " 元奖励" : "") +
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
      if (r.register_bonus > 0) {
        $("#bonus-note").textContent = "新用户注册即送 " + r.register_bonus + " 元体验金";
      }
    } catch (e) { /* ignore */ }
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
  async function loadProjects() {
    try {
      const { projects } = await api("/api/projects");
      const grid = $("#project-list");
      if (!projects.length) {
        grid.innerHTML = '<div class="empty">还没有项目，点击「新建小说」开始创作</div>';
        return;
      }
      grid.innerHTML = projects.map((p) => {
        const pct = p.chapters_total ? Math.round((p.chapters_done / p.chapters_total) * 100) : 0;
        const badge = p.running ? '<span class="badge running">生成中</span>'
          : p.has_error ? '<span class="badge error">出错</span>'
          : p.chapters_done >= p.chapters_total && p.chapters_total > 0 ? '<span class="badge done">已完成</span>'
          : p.chapters_done > 0 ? '<span class="badge running">进行中</span>'
          : '<span class="badge ready">未开始</span>';
        return (
          '<div class="project-card" data-id="' + esc(p.id) + '">' +
            '<h3>' + esc(p.title) + '</h3>' +
            '<div class="genre">' + esc(p.genre) + ' · ' + fmtWords(p.total_words) + ' · ' + fmtTime(p.created) + '</div>' +
            '<div class="p-meta"><span>' + p.chapters_done + '/' + p.chapters_total + ' 章（' + pct + '%）</span>' + badge + '</div>' +
          '</div>'
        );
      }).join("");
      grid.querySelectorAll(".project-card").forEach((card) => {
        card.addEventListener("click", () => {
          currentProjectId = card.dataset.id;
          switchView("detail");
        });
      });
    } catch (e) {
      toast("加载项目失败：" + e.message);
    }
  }

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
    // 价格提示（随字数变化）
    const wordsInput = document.querySelector('#create-form input[name="total_words"]');
    const priceTip = $("#price-tip");
    // 价格格式化：整数/一位小数显示一位，两位小数（如 0.99）显示两位
    function fmtPrice(v) {
      const n = Number(v);
      return (Math.round(n * 100) % 10 === 0) ? n.toFixed(1) : n.toFixed(2);
    }
    const updatePrice = async () => {
      try {
        const words = parseInt(wordsInput.value, 10) || 100000;
        const r = await api("/api/pricing");
        const tiers = r.tiers || [];
        let tier = tiers[tiers.length - 1];
        for (const t of tiers) {
          if (t.max_words == null || words <= t.max_words) { tier = t; break; }
        }
        let priceHtml = "本书为<strong>" + esc(tier.name) + "</strong>，";
        if (tier.original_price != null && tier.original_price > tier.price) {
          priceHtml += '<span class="price-original">原价 ' + fmtPrice(tier.original_price) + " 元</span>，现价 <strong class=\"price-now\">" + fmtPrice(tier.price) + " 元</strong> <span class=\"price-tag\">限时优惠</span>。";
        } else {
          priceHtml += "定价 <strong>" + fmtPrice(tier.price) + " 元</strong>。";
        }
        priceHtml += "创建项目时将从余额扣费。";
        priceTip.innerHTML = priceHtml;
      } catch (e) { /* ignore */ }
    };
    if (wordsInput) {
      wordsInput.addEventListener("input", updatePrice);
      updatePrice();
    }
    // 模式切换：auto=智能生成（现有工作流），expand=AI 扩写
    const modeRadios = $$('#create-form input[name="mode"]');
    const expandWrap = $("#expand-input-wrap");
    const expandHideEls = $$('#create-form [data-expand-hide]');
    const applyMode = () => {
      const expand = (document.querySelector('#create-form input[name="mode"]:checked') || {}).value === "expand";
      expandWrap.classList.toggle("hidden", !expand);
      expandHideEls.forEach((el) => el.classList.toggle("hidden", expand));
      const lbl = expand ? "AI 扩写模式（提供大纲/简要内容，直接扩写成文）" : "智能生成模式（联网调研 → 自动大纲 → 逐章）";
      const meta = $("#create-form .mode-label");
      if (meta) meta.textContent = lbl;
    };
    modeRadios.forEach((r) => r.addEventListener("change", applyMode));
    applyMode();
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

    if (mode === "expand" && !expandInput) {
      errBox.textContent = "创建失败：AI 扩写模式必须填写『大纲或简要内容』";
      errBox.classList.remove("hidden");
      return;
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
      confirm: false,
      memory_window: 5,
      mode,
      expand_input: expandInput,
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
      toast("项目「" + res.title + "」创建成功，已扣费 " + res.price.toFixed(1) + " 元");
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
      if (!d.running && d.chapters_total > 0 && d.chapters_done >= d.chapters_total) {
        stopPolling();
      }
    } catch (e) {
      // 忽略瞬时错误
    }
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
      coverImg.src = API + "/api/projects/" + encodeURIComponent(currentProjectId) + "/cover?t=" + Date.now();
    } else {
      coverImg.classList.add("hidden");
      coverEmpty.classList.remove("hidden");
    }

    const pct = d.chapters_total ? Math.round((d.chapters_done / d.chapters_total) * 100) : 0;
    $("#progress-text").textContent = d.chapters_done + " / " + d.chapters_total + " 章";
    $("#progress-pct").textContent = pct + "%";
    $("#progress-fill").style.width = pct + "%";

    const running = d.running;
    $("#btn-start").disabled = running;
    $("#btn-stop").disabled = !running;
    // 下载：有任一格式即可用；未生成的格式现场导出
    $("#btn-download").disabled = !(d.has_txt || d.has_epub || d.has_docx || d.chapters_done > 0);
    $("#dl-format").disabled = $("#btn-download").disabled;
    $("#run-status").textContent = running
      ? "⏳ 正在生成…（自动刷新）"
      : d.error
        ? "⚠ 出错：" + d.error
        : d.chapters_done >= d.chapters_total && d.chapters_total > 0
          ? "✅ 已完成"
          : d.chapters_done > 0 ? "⏸ 已暂停（进度已保存，可继续）" : "未开始";

    const errBox = $("#detail-error");
    if (d.error) { errBox.textContent = "生成出错：" + d.error; errBox.classList.remove("hidden"); }
    else errBox.classList.add("hidden");

    // 加载大纲/世界观一次
    if (!window._detailLoaded) {
      window._detailLoaded = true;
      loadOutlineAndWorld();
    }
  }

  async function loadOutlineAndWorld() {
    try {
      const o = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/outline");
      const ob = $("#outline-box");
      if (o.title) {
        ob.innerHTML = '<div class="ol-intro"><strong>《' + esc(o.title) + '》</strong><br>' + esc(o.intro) + "</div>";
      }
      ob.innerHTML += o.chapters.map((c) =>
        '<div class="ol-ch">第' + c.no + '章《' + esc(c.title) + '》<span class="goal">——' + esc(c.goal) + "</span></div>"
      ).join("");

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
    const total = detailCache ? detailCache.chapters_total : 0;
    const list = $("#chapter-list");
    const items = [];
    for (let i = 1; i <= total; i++) {
      const c = chaptersCache.find((x) => x.no === i);
      if (c) {
        items.push(
          '<div class="ch-item" data-no="' + i + '">' +
            '<div class="ch-no">第 ' + i + ' 章</div>' +
            '<div class="ch-title">' + esc(c.title) + "</div>" +
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

  async function openReader(no) {
    try {
      const ch = await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/chapters/" + no);
      $("#reader-title").textContent = "第 " + ch.no + " 章 " + ch.title;
      const body = $("#reader-body");
      body.innerHTML = cleanContent(ch.content).split(/\n+/).map((p) => p.trim()).filter(Boolean).map((p) => "<p>" + esc(p) + "</p>").join("");
      $("#reader-modal").classList.remove("hidden");
      body.scrollTop = 0;
      updateReaderNav(no);
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

  $("#btn-download").addEventListener("click", () => {
    const fmt = $("#dl-format").value || "txt";
    const a = document.createElement("a");
    a.href = API + "/api/projects/" + encodeURIComponent(currentProjectId) + "/download?format=" + encodeURIComponent(fmt);
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });

  // 生成封面
  $("#btn-gen-cover").addEventListener("click", async () => {
    try {
      await api("/api/projects/" + encodeURIComponent(currentProjectId) + "/cover", { method: "POST" });
      toast("封面已生成");
      await refreshDetail();
    } catch (e) {
      toast("生成封面失败：" + e.message);
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
