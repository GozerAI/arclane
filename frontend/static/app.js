const API = "/api";

const state = {
    currentBusiness: null,
    businesses: [],
    userEmail: null,
    currentPage: "work",
    currentTab: "work",
    currentContentFilter: "",
    pollTimer: null,
    toastTimer: null,
    metricsChart: null,
    chartLoader: null,
    opsStreamItems: [],
    feedItems: [],
    feedSource: null,
};

const els = {
    toast: document.getElementById("toast"),
    taskModal: document.getElementById("task-modal"),
    taskInput: document.getElementById("task-input"),
    taskSubmit: document.getElementById("task-submit"),
    workingDaysDisplay: document.getElementById("working-days-display") || document.createElement("span"),
    // Billing and settings now render into account tab containers
    billingContainer: document.getElementById("account-billing") || document.createElement("div"),
    settingsContainer: document.getElementById("account-settings") || document.createElement("div"),
    lowWorkingDayBanner: document.getElementById("low-working-day-banner") || document.createElement("div"),
    businessSwitcher: document.getElementById("business-switcher") || document.createElement("select"),
    newBusinessBtn: document.getElementById("new-business-btn") || document.createElement("button"),
    oauthGoogle: document.getElementById("oauth-google"),
    oauthGithub: document.getElementById("oauth-github"),
    // New dash header elements
    dashBizName: document.getElementById("dash-biz-name"),
    dashHealthBadge: document.getElementById("dash-health-badge"),
    dashWorkingDays: document.getElementById("dash-working-days"),
};

const PLANS = {
    preview: { name: "Preview", price: "Free", working_days: 3, companies: 1 },
    starter: { name: "Starter", price: "$49/mo", working_days: 10, companies: 1, trialDays: 3 },
    pro: { name: "Pro", price: "$99/mo", working_days: 20, companies: 1 },
    growth: { name: "Growth", price: "$249/mo", working_days: 75, companies: 3 },
    scale: { name: "Scale", price: "$499/mo", working_days: 150, companies: 5 },
};

const QUALITY_COPY = {
    report: "Operator-grade strategic output",
    social: "Publishable social copy",
    blog: "Long-form conversion asset",
    newsletter: "Audience-ready email draft",
};

if (window.location.hash === "#create") {
    document.addEventListener("DOMContentLoaded", () => {
        if (state.userEmail) {
            showView("create-view");
        } else {
            showView("signup-view");
        }
    });
}

function showView(id) {
    document.querySelectorAll(".view").forEach((view) => view.classList.add("hidden"));
    document.getElementById(id).classList.remove("hidden");
}

function showPage(name) {
    // Legacy shim: map old page names to new tabs
    const tabMap = {
        feed: "work", content: "work",
        billing: "account", settings: "account",
        roadmap: "plan", health: "plan", advisory: "plan",
        metrics: "plan",
    };
    showTab(tabMap[name] || "work");
}

function showTab(name) {
    state.currentTab = name;
    document.querySelectorAll(".dash-section").forEach((s) => s.classList.add("hidden"));
    const section = document.getElementById(`tab-${name}`);
    if (section) section.classList.remove("hidden");
    document.querySelectorAll(".dash-tab").forEach((t) => t.classList.remove("active"));
    const activeTab = document.querySelector(`.dash-tab[data-tab="${name}"]`);
    if (activeTab) activeTab.classList.add("active");

    if (name === "work") loadWork();
    if (name === "plan") loadPlan();
    if (name === "account") loadAccount();
}

async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...options.headers };

    const response = await fetch(`${API}${path}`, {
        credentials: "same-origin",
        ...options,
        headers,
    });
    if (response.status === 401) {
        clearAuthState();
        throw new Error("Session expired. Please sign in again.");
    }
    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: response.statusText }));
        throw new Error(error.detail || "Request failed");
    }
    if (response.status === 204) return null;
    return response.json();
}

function clearAuthState() {
    state.userEmail = null;
    state.currentBusiness = null;
    state.businesses = [];
    stopPolling();
    stopFeedStream();
    closeTaskModal();
    showView("login-view");
}

async function logout() {
    try {
        await fetch(`${API}/auth/logout`, {
            method: "POST",
            credentials: "same-origin",
        });
    } catch (_) {
        // Ignore logout transport failures and clear the local view state.
    }
    clearAuthState();
}

function setBusinesses(businesses) {
    state.businesses = businesses || [];
    populateBusinessSwitcher();
}

function populateBusinessSwitcher() {
    const switcher = els.businessSwitcher;
    if (!switcher) return;

    if (state.businesses.length <= 1) {
        switcher.classList.add("hidden");
        switcher.innerHTML = "";
        return;
    }

    switcher.classList.remove("hidden");
    switcher.innerHTML = state.businesses.map((business) => `
        <option value="${esc(business.slug)}">${esc(business.name)}</option>
    `).join("");

    if (state.currentBusiness) {
        switcher.value = state.currentBusiness.slug;
    }
}

function updateStoredBusiness(updatedBusiness) {
    state.businesses = state.businesses.map((business) =>
        business.slug === updatedBusiness.slug ? { ...business, ...updatedBusiness } : business
    );
    populateBusinessSwitcher();
}

function currentCompanyLimit() {
    if (!state.businesses.length) return 1;
    return state.businesses.reduce((limit, business) => {
        const plan = PLANS[business.plan] || PLANS.preview;
        return Math.max(limit, plan.companies || 1);
    }, 1);
}

function updatePortfolioSummary() {
    // Portfolio summary is now shown in the Account tab via loadBilling/loadSettings
}

function showToast(message, type = "success") {
    clearTimeout(state.toastTimer);
    els.toast.textContent = message;
    els.toast.className = `toast ${type}`;
    els.toast.classList.remove("hidden");
    state.toastTimer = window.setTimeout(() => {
        els.toast.classList.add("hidden");
    }, 3200);
}

function readQueryParams() {
    return new URLSearchParams(window.location.search);
}

function clearQueryParams() {
    if (window.location.search) {
        window.history.replaceState({}, "", window.location.pathname + window.location.hash);
    }
}

function setFormBusy(button, busyText, idleText, isBusy) {
    button.disabled = isBusy;
    button.textContent = isBusy ? busyText : idleText;
}

function showError(id, message) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message;
    element.classList.remove("hidden");
}

function hideError(id) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = "";
    element.classList.add("hidden");
}

function setCreateMode(mode) {
    const normalized = mode === "existing" ? "existing" : "idea";
    document.getElementById("biz-mode").value = normalized;
    document.getElementById("idea-path").classList.toggle("hidden", normalized !== "idea");
    document.getElementById("existing-path").classList.toggle("hidden", normalized !== "existing");
    document.querySelectorAll("[data-create-mode]").forEach((button) => {
        button.classList.toggle("active", button.dataset.createMode === normalized);
    });
}

function openTaskModal(prefill = "") {
    els.taskInput.value = prefill;
    els.workingDaysDisplay.textContent = state.currentBusiness
        ? `${state.currentBusiness.working_days_remaining} working days remaining`
        : "";
    els.taskModal.classList.remove("hidden");
    els.taskModal.setAttribute("aria-hidden", "false");
    window.setTimeout(() => els.taskInput.focus(), 0);
}

function closeTaskModal() {
    if (!els.taskModal.querySelector("#task-input")) {
        restoreTaskModal();
    }
    els.taskModal.classList.add("hidden");
    els.taskModal.setAttribute("aria-hidden", "true");
    els.taskInput.value = "";
}

function updateWorkingDayGauge() {
    const credits = state.currentBusiness ? state.currentBusiness.working_days_remaining : 0;

    if (els.dashWorkingDays) {
        els.dashWorkingDays.textContent = `${credits} working day${credits === 1 ? "" : "s"}`;
    }

    if (credits <= 2) {
        els.lowWorkingDayBanner.classList.remove("hidden");
    } else {
        els.lowWorkingDayBanner.classList.add("hidden");
    }
}

function enterDashboard(business) {
    stopFeedStream();
    state.currentBusiness = business;
    if (!state.businesses.some((item) => item.slug === business.slug)) {
        setBusinesses([business, ...state.businesses]);
    } else {
        updateStoredBusiness(business);
    }
    if (els.dashBizName) els.dashBizName.textContent = business.name;
    updateWorkingDayGauge();
    showView("dashboard-view");
    showTab("work");
    updateProgressStrip();
    startPolling();
    startFeedStream();
}

function startPolling() {
    stopPolling();
    state.pollTimer = window.setInterval(async () => {
        if (!state.currentBusiness) return;
        updateWorkingDayGauge();
        if (state.currentTab === "work") await loadWork();
    }, 8000);
}

function stopPolling() {
    if (!state.pollTimer) return;
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
}

function stopFeedStream() {
    if (state.feedSource) {
        state.feedSource.close();
        state.feedSource = null;
    }
}

async function refreshBusinessSummary(silent = false) {
    if (!state.currentBusiness) return;
    try {
        const settings = await api(`/businesses/${state.currentBusiness.slug}/settings`);

        state.currentBusiness = {
            ...state.currentBusiness,
            name: settings.name,
            description: settings.description,
            website_url: settings.website_url,
            website_summary: settings.website_summary,
            subdomain: settings.subdomain,
            working_days_remaining: settings.working_days_remaining,
            template: settings.template,
            plan: settings.plan,
            operating_plan: settings.operating_plan,
        };

        updateStoredBusiness(state.currentBusiness);
        if (els.dashBizName) els.dashBizName.textContent = state.currentBusiness.name;
        updateWorkingDayGauge();
    } catch (error) {
        if (!silent) showToast(error.message, "error");
    }
}

function renderBusinessSummary(settings, cycles, contentItems = []) {
    // Legacy function — kept for compatibility; rendering is now handled by the tab system
}

function applyOperatingRecommendations(operatingPlan) {
    // Legacy function — no longer used in the simplified dashboard
}

function nextQueuedTask(operatingPlan) {
    const tasks = operatingPlan?.agent_tasks || [];
    return tasks.find((item) => ["active", "queued", "pending"].includes(item.queue_status)) || null;
}

function nextThreeQueuedTasks(operatingPlan) {
    const tasks = operatingPlan?.agent_tasks || [];
    return tasks
        .filter((item) => ["active", "queued", "pending"].includes(item.queue_status))
        .slice(0, 3);
}

function availableAddOnOffers(operatingPlan) {
    const offers = operatingPlan?.add_on_offers || [];
    return offers.filter((item) => item.status === "available");
}

function formatQueueStatus(task) {
    if (!task?.queue_status) return "Queued";
    if ((task.duration_days || 1) <= 1) {
        return prettyArea(task.queue_status);
    }
    const completedNights = Math.max((task.duration_days || 1) - (task.days_remaining || 1), 0);
    return `${prettyArea(task.queue_status)} · ${completedNights}/${task.duration_days} nights used`;
}

function renderQueuePreview(operatingPlan) {
    // Legacy function — no longer used in the simplified dashboard
}

function renderExecutionProof(cycles, contentItems) {
    // Legacy function — no longer used in the simplified dashboard
}

function renderShowcase(contentItems, latestCompletedCycle, reports, social) {
    // Legacy function — no longer used in the simplified dashboard
}

function cycleStatusCopy(status) {
    if (status === "completed") return { label: "Cycle completed", tone: "success" };
    if (status === "running") return { label: "Cycle running", tone: "warning" };
    if (status === "failed") return { label: "Cycle needs review", tone: "error" };
    return { label: "Cycle queued", tone: "neutral" };
}

function taskOutcomeCopy(cycle) {
    if (!cycle || cycle.total_tasks == null) return "task count unavailable";
    const succeeded = Math.max(0, Number(cycle.total_tasks) - Number(cycle.failed_tasks || 0));
    return `${succeeded}/${cycle.total_tasks} tasks succeeded`;
}

async function loadFeed(silent = false) {
    if (!state.currentBusiness) return;
    const feedListEl = document.getElementById("feed-list");
    if (!feedListEl) return; // Element removed in simplified dashboard
    if (!silent) {
        feedListEl.innerHTML = '<div class="empty">Loading recent activity...</div>';
    }

    try {
        const items = await api(`/businesses/${state.currentBusiness.slug}/feed`);
        state.feedItems = items || [];
        updateOpsStream(items);
        renderFeedList(state.feedItems);
    } catch (error) {
        if (!silent) {
            if (feedListEl) feedListEl.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
        }
    }
}

function renderFeedList(items) {
    const feedListEl = document.getElementById("feed-list");
    if (!feedListEl) return;
    if (!items.length) {
        feedListEl.innerHTML = '<div class="empty">No activity yet. Queue a task to get work moving.</div>';
        return;
    }
    feedListEl.innerHTML = items.map((item) => `
        <div class="card">
            <div class="action">${esc(item.action)}</div>
            ${item.detail ? `<div class="detail">${esc(item.detail).slice(0, 320)}</div>` : ""}
            <div class="time">${timeAgo(item.created_at)}</div>
        </div>
    `).join("");
}

function startFeedStream() {
    if (!state.currentBusiness) return;
    stopFeedStream();

    const streamUrl = `${API}/businesses/${encodeURIComponent(state.currentBusiness.slug)}/feed/stream`;
    const source = new EventSource(streamUrl);
    state.feedSource = source;

    source.addEventListener("activity", (event) => {
        const item = JSON.parse(event.data);
        state.feedItems = [item, ...state.feedItems.filter((entry) => entry.id !== item.id)].slice(0, 50);
        updateOpsStream(state.feedItems);
        if (state.currentTab === "work") {
            loadWork();
        }
    });

    source.onerror = () => {
        source.close();
        if (state.feedSource === source) {
            state.feedSource = null;
        }
        window.setTimeout(() => {
            if (state.currentBusiness) startFeedStream();
        }, 5000);
    };
}

function updateOpsStream(items) {
    state.opsStreamItems = items || [];
    // Ops stream removed from dashboard — kept as state for potential future use
}

function renderOpsStream() {
    // Ops stream removed from simplified dashboard
}

async function loadContent(filter = "", silent = false) {
    if (!state.currentBusiness) return;
    state.currentContentFilter = filter;
    const contentListEl = document.getElementById("content-list");
    if (!contentListEl) return; // Element removed in simplified dashboard
    if (!silent) {
        contentListEl.innerHTML = '<div class="empty">Loading content output...</div>';
    }

    try {
        let path = `/businesses/${state.currentBusiness.slug}/content`;
        if (filter) path += `?content_type=${encodeURIComponent(filter)}`;
        const items = await api(path);

        if (items.length === 0) {
            contentListEl.innerHTML = '<div class="empty">No content has landed yet. The next completed cycle will show up here.</div>';
            return;
        }

        state.contentItems = items;
        contentListEl.innerHTML = items.map((item) => `
            <div class="card content-card" data-content-id="${item.id}" style="cursor:pointer">
                <div class="card-topline">
                    <span class="badge badge-${esc(item.content_type)}">${esc(item.content_type)}</span>
                    <span class="badge badge-${esc(item.status)}">${esc(item.status)}</span>
                </div>
                ${item.title ? `<div class="action" style="margin-top:0.6rem">${esc(item.title)}</div>` : ""}
                <div class="detail">${truncate(item.body, 140)}</div>
                <div class="card-actions">
                    <div class="time">${timeAgo(item.created_at)}</div>
                    <div>
                        ${item.status === "draft" ? `<button class="btn-secondary btn-small publish-btn" data-content-id="${item.id}">Publish</button>` : ""}
                        <button class="btn-secondary btn-small view-btn" data-content-id="${item.id}">View</button>
                    </div>
                </div>
            </div>
        `).join("");
        bindContentActions();
    } catch (error) {
        if (!silent) {
            contentListEl.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
        }
    }
}

function bindContentActions() {
    const contentListEl = document.getElementById("content-list");
    if (!contentListEl) return;
    contentListEl.querySelectorAll(".publish-btn").forEach((button) => {
        button.addEventListener("click", (e) => {
            e.stopPropagation();
            publishContent(button.dataset.contentId, button);
        });
    });
    contentListEl.querySelectorAll(".view-btn").forEach((button) => {
        button.addEventListener("click", (e) => {
            e.stopPropagation();
            showContentModal(button.dataset.contentId);
        });
    });
    contentListEl.querySelectorAll(".content-card").forEach((card) => {
        card.addEventListener("click", () => {
            showContentModal(card.dataset.contentId);
        });
    });
}

const TASK_MODAL_ORIGINAL_HTML = `
    <h3>Run a Task</h3>
    <p class="page-copy">Describe the output you want. Short, direct requests work best.</p>
    <textarea id="task-input" placeholder="What should Arclane work on next?" rows="4"></textarea>
    <div class="modal-actions">
        <button id="task-cancel" class="btn-secondary">Cancel</button>
        <button id="task-submit" class="btn-primary">Run</button>
    </div>
    <p id="working-days-display" class="working-days"></p>
`;

function restoreTaskModal() {
    const content = els.taskModal.querySelector(".modal-content");
    content.innerHTML = TASK_MODAL_ORIGINAL_HTML;
    // Re-point els references to the freshly created elements
    els.taskInput = document.getElementById("task-input");
    els.taskSubmit = document.getElementById("task-submit");
    els.workingDaysDisplay = document.getElementById("working-days-display");
    document.getElementById("task-cancel").addEventListener("click", closeTaskModal);
    document.getElementById("task-submit").addEventListener("click", handleTaskSubmit);
}

function showContentModal(contentId) {
    const item = state.contentItems?.find((i) => i.id === parseInt(contentId));
    if (!item) return;

    const modal = els.taskModal;
    const content = modal.querySelector(".modal-content");
    content.innerHTML = `
        <div class="card-topline" style="margin-bottom:1rem">
            <span class="badge badge-${esc(item.content_type)}">${esc(item.content_type)}</span>
            <span class="badge badge-${esc(item.status)}">${esc(item.status)}</span>
        </div>
        <h3>${esc(item.title || "Untitled")}</h3>
        <div style="margin-top:1rem;color:var(--text-muted);white-space:pre-wrap;max-height:60vh;overflow-y:auto;line-height:1.7">${esc(item.body)}</div>
        <div class="modal-actions">
            ${item.status === "draft" ? `<button class="btn-primary publish-modal-btn" data-content-id="${item.id}">Publish</button>` : ""}
            <button class="btn-secondary" id="close-content-modal">Close</button>
        </div>
    `;
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");

    modal.querySelector("#close-content-modal").addEventListener("click", () => {
        restoreTaskModal();
        modal.classList.add("hidden");
        modal.setAttribute("aria-hidden", "true");
    });
    modal.querySelector(".publish-modal-btn")?.addEventListener("click", async (e) => {
        const btn = e.target;
        btn.disabled = true;
        btn.textContent = "Publishing...";
        await publishContent(btn.dataset.contentId, btn);
        restoreTaskModal();
        modal.classList.add("hidden");
        modal.setAttribute("aria-hidden", "true");
    });
}

async function publishContent(contentId, button) {
    const previous = button.textContent;
    button.disabled = true;
    button.textContent = "Publishing...";
    try {
        await api(`/businesses/${state.currentBusiness.slug}/content/${contentId}`, {
            method: "PATCH",
            body: JSON.stringify({ status: "published" }),
        });
        if (state.currentTab === "work") await loadWork();
        showToast("Content published.");
    } catch (error) {
        showToast(error.message, "error");
        button.disabled = false;
        button.textContent = previous;
    }
}

async function loadMetrics() {
    if (!state.currentBusiness) return;
    const metricsGridEl = document.getElementById("metrics-grid");
    if (!metricsGridEl) return; // Element removed in simplified dashboard
    const metricsChartWrapEl = document.getElementById("metrics-chart-wrap");
    metricsGridEl.innerHTML = '<div class="empty" style="grid-column:1/-1">Loading metrics...</div>';

    try {
        const items = await api(`/businesses/${state.currentBusiness.slug}/metrics?limit=20`);
        const latest = {};

        items.forEach((metric) => {
            if (!latest[metric.name] || metric.recorded_at > latest[metric.name].recorded_at) {
                latest[metric.name] = metric;
            }
        });

        const entries = Object.values(latest);
        if (entries.length === 0) {
            metricsGridEl.innerHTML = '<div class="empty" style="grid-column:1/-1">No metrics recorded yet.</div>';
            if (metricsChartWrapEl) metricsChartWrapEl.classList.add("hidden");
            return;
        }

        metricsGridEl.innerHTML = entries.map((metric) => `
            <div class="metric-card">
                <div class="label">${esc(metric.name)}</div>
                <div class="value">${formatNumber(metric.value)}</div>
            </div>
        `).join("");

        await ensureChartsLoaded();
        renderMetricsChart(entries);
    } catch (error) {
        metricsGridEl.innerHTML = `<div class="empty" style="grid-column:1/-1">${esc(error.message)}</div>`;
        if (metricsChartWrapEl) metricsChartWrapEl.classList.add("hidden");
    }
}

function renderMetricsChart(entries) {
    const chartEl = document.getElementById("metrics-chart");
    if (!chartEl) return; // Element removed in simplified dashboard
    const chartContext = chartEl.getContext("2d");
    const labels = entries.map((entry) => entry.name);
    const data = entries.map((entry) => entry.value);

    if (state.metricsChart) {
        state.metricsChart.destroy();
        state.metricsChart = null;
    }

    const chartWrap = document.getElementById("metrics-chart-wrap");
    if (chartWrap) chartWrap.classList.remove("hidden");
    state.metricsChart = new window.Chart(chartContext, {
        type: "bar",
        data: {
            labels,
            datasets: [{
                data,
                backgroundColor: "rgba(75, 178, 255, 0.42)",
                borderColor: "rgba(75, 178, 255, 1)",
                borderRadius: 10,
                borderWidth: 1.4,
            }],
        },
        options: {
            responsive: true,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (context) => formatNumber(context.raw),
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: "#9db2cb" },
                    grid: { color: "rgba(128, 159, 196, 0.12)" },
                },
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: "#9db2cb",
                        callback: (value) => formatNumber(value),
                    },
                    grid: { color: "rgba(128, 159, 196, 0.12)" },
                },
            },
        },
    });
}

function ensureChartsLoaded() {
    if (window.Chart) return Promise.resolve(window.Chart);
    if (state.chartLoader) return state.chartLoader;

    state.chartLoader = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
        script.async = true;
        script.onload = () => resolve(window.Chart);
        script.onerror = () => reject(new Error("Unable to load chart library"));
        document.head.appendChild(script);
    });

    return state.chartLoader;
}

// ─── Roadmap ────────────────────────────────────────────────────
async function loadRoadmap() {
    if (!state.currentBusiness) return;
    const slug = state.currentBusiness.slug;
    const roadmapEl = document.getElementById("roadmap-phases");
    const forecastEl = document.getElementById("roadmap-forecast");
    const milestonesEl = document.getElementById("roadmap-milestones");
    const bottlenecksEl = document.getElementById("roadmap-bottlenecks");
    if (!forecastEl) return; // Elements removed in simplified dashboard
    forecastEl.innerHTML = '<div class="empty">Loading forecast...</div>';

    try {
        const [roadmap, forecast] = await Promise.all([
            api(`/businesses/${slug}/roadmap`),
            api(`/businesses/${slug}/forecast`),
        ]);

        // Forecast summary
        const pace = forecast.pace || {};
        const eta = forecast.graduation_eta || {};
        const vel = forecast.velocity || {};
        const streak = forecast.streak || {};
        forecastEl.innerHTML = `
            <div class="forecast-grid">
                <div class="forecast-card">
                    <div class="forecast-label">Pace</div>
                    <div class="forecast-value ${pace.status === 'ahead' ? 'text-green' : pace.status === 'on_track' ? 'text-blue' : 'text-amber'}">${esc(pace.label || 'Unknown')}</div>
                </div>
                <div class="forecast-card">
                    <div class="forecast-label">Graduation ETA</div>
                    <div class="forecast-value">${eta.estimated_date || 'TBD'}</div>
                    <div class="forecast-sub">${esc(eta.message || '')}</div>
                </div>
                <div class="forecast-card">
                    <div class="forecast-label">Velocity</div>
                    <div class="forecast-value">${vel.milestones_per_week || 0}/week</div>
                    <div class="forecast-sub">${vel.completed || 0}/${vel.total || 0} milestones</div>
                </div>
                <div class="forecast-card">
                    <div class="forecast-label">Streak</div>
                    <div class="forecast-value">${streak.current || 0} cycles</div>
                    <div class="forecast-sub">Longest: ${streak.longest || 0}</div>
                </div>
            </div>
        `;

        // Phase timeline
        const phases = roadmap.phases || [];
        roadmapEl.innerHTML = phases.map(p => `
            <div class="phase-card ${p.status === 'active' ? 'phase-active' : p.status === 'completed' ? 'phase-completed' : 'phase-locked'}">
                <div class="phase-header">
                    <span class="phase-number">Phase ${p.phase_number}</span>
                    <strong>${esc(p.phase_name)}</strong>
                    <span class="phase-status-badge badge-${p.status}">${p.status}</span>
                </div>
                <div class="phase-progress">
                    <div class="phase-progress-bar">
                        <div class="phase-progress-fill" style="width:${p.milestones_total ? (p.milestones_completed / p.milestones_total * 100) : 0}%"></div>
                    </div>
                    <span class="phase-progress-label">${p.milestones_completed}/${p.milestones_total} milestones</span>
                </div>
            </div>
        `).join("");

        // Milestones for active phase
        const activePhase = phases.find(p => p.status === "active");
        if (activePhase && activePhase.milestones) {
            milestonesEl.innerHTML = `
                <h3>Phase ${activePhase.phase_number} Milestones</h3>
                <div class="milestone-list">
                    ${activePhase.milestones.map(m => `
                        <div class="milestone-item ${m.status}">
                            <span class="milestone-check">${m.status === 'completed' ? '&#10003;' : '&#9675;'}</span>
                            <span class="milestone-title">${esc(m.title)}</span>
                            ${m.due_day ? `<span class="milestone-due">Day ${m.due_day}</span>` : ''}
                        </div>
                    `).join("")}
                </div>
            `;
        } else {
            milestonesEl.innerHTML = "";
        }

        // Bottlenecks
        const bots = forecast.bottlenecks || [];
        if (bots.length > 0) {
            bottlenecksEl.innerHTML = `
                <h3>Bottlenecks</h3>
                ${bots.map(b => `
                    <div class="bottleneck-card severity-${b.severity || 'medium'}">
                        <strong>${esc(b.type.replace(/_/g, ' '))}</strong>
                        <p>${esc(b.recommendation || '')}</p>
                    </div>
                `).join("")}
            `;
        } else {
            bottlenecksEl.innerHTML = "";
        }

        // Weekly focus
        const focus = forecast.weekly_focus || {};
        if (focus.action) {
            bottlenecksEl.innerHTML += `
                <div class="weekly-focus">
                    <strong>This Week's Focus:</strong> ${esc(focus.action)}
                </div>
            `;
        }
    } catch (error) {
        forecastEl.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
}

// ─── Health ─────────────────────────────────────────────────────
async function loadHealth() {
    if (!state.currentBusiness) return;
    const slug = state.currentBusiness.slug;
    const scoreEl = document.getElementById("health-score-display");
    const subsEl = document.getElementById("health-subscores");
    const recsEl = document.getElementById("health-recommendations");
    if (!scoreEl) return; // Elements removed in simplified dashboard
    scoreEl.innerHTML = '<div class="empty">Loading health score...</div>';

    try {
        const [health, recs] = await Promise.all([
            api(`/businesses/${slug}/health`),
            api(`/businesses/${slug}/health/recommendations`),
        ]);

        const overall = health.overall || 0;
        const color = overall >= 70 ? '#10b981' : overall >= 40 ? '#f59e0b' : '#ef4444';

        scoreEl.innerHTML = `
            <div class="health-gauge">
                <div class="health-score-circle" style="--score-color: ${color}">
                    <span class="health-score-number">${Math.round(overall)}</span>
                    <span class="health-score-label">/ 100</span>
                </div>
            </div>
        `;

        // Sub-scores
        const subs = health.sub_scores || {};
        subsEl.innerHTML = `
            <div class="subscore-grid">
                ${Object.entries(subs).map(([key, val]) => {
                    const c = val >= 70 ? 'text-green' : val >= 40 ? 'text-amber' : 'text-red';
                    return `
                        <div class="subscore-card">
                            <div class="subscore-label">${esc(key.replace(/_/g, ' '))}</div>
                            <div class="subscore-bar">
                                <div class="subscore-fill" style="width:${Math.min(val, 100)}%; background: ${val >= 70 ? '#10b981' : val >= 40 ? '#f59e0b' : '#ef4444'}"></div>
                            </div>
                            <div class="subscore-value ${c}">${Math.round(val)}</div>
                        </div>
                    `;
                }).join("")}
            </div>
        `;

        // Recommendations
        const recList = recs.recommendations || [];
        if (recList.length > 0) {
            recsEl.innerHTML = `
                <h3>Recommendations</h3>
                ${recList.map(r => `
                    <div class="rec-card urgency-${r.urgency}">
                        <span class="rec-area">${esc(r.area.replace(/_/g, ' '))}</span>
                        <span class="rec-score">${Math.round(r.score)}/100</span>
                        <p>${esc(r.suggestion)}</p>
                    </div>
                `).join("")}
            `;
        } else {
            recsEl.innerHTML = '<div class="empty">All health areas are performing well.</div>';
        }
    } catch (error) {
        scoreEl.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
}

// ─── Advisory ───────────────────────────────────────────────────
async function loadAdvisory() {
    if (!state.currentBusiness) return;
    const slug = state.currentBusiness.slug;
    const digestEl = document.getElementById("advisory-digest");
    const notesEl = document.getElementById("advisory-notes");
    if (!digestEl) return; // Elements removed in simplified dashboard
    digestEl.innerHTML = '<div class="empty">Loading advisory...</div>';

    try {
        const [digest, notes] = await Promise.all([
            api(`/businesses/${slug}/advisory/digest`),
            api(`/businesses/${slug}/advisory/notes?acknowledged=false`),
        ]);

        // Digest summary
        const c = digest.cycles || {};
        const ct = digest.content || {};
        const m = digest.milestones || {};
        const r = digest.revenue || {};
        digestEl.innerHTML = `
            <h3>Weekly Digest</h3>
            <div class="digest-grid">
                <div class="digest-card"><div class="digest-value">${c.completed || 0}/${c.total || 0}</div><div class="digest-label">Cycles</div></div>
                <div class="digest-card"><div class="digest-value">${ct.produced || 0}</div><div class="digest-label">Content</div></div>
                <div class="digest-card"><div class="digest-value">${m.completed || 0}</div><div class="digest-label">Milestones</div></div>
                <div class="digest-card"><div class="digest-value">$${(r.weekly_usd || 0).toLocaleString()}</div><div class="digest-label">Revenue</div></div>
            </div>
        `;

        // Notes
        const noteList = notes.notes || [];
        if (noteList.length > 0) {
            notesEl.innerHTML = `
                <h3>Active Notes</h3>
                ${noteList.map(n => `
                    <div class="advisory-note note-${n.category}">
                        <div class="note-header">
                            <span class="note-category badge-${n.category}">${n.category}</span>
                            <span class="note-priority">P${n.priority}</span>
                        </div>
                        <strong>${esc(n.title)}</strong>
                        <p>${esc(n.body)}</p>
                        <button class="btn-small btn-ghost acknowledge-btn" data-note-id="${n.id}">Acknowledge</button>
                    </div>
                `).join("")}
            `;

            // Wire acknowledge buttons
            notesEl.querySelectorAll(".acknowledge-btn").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const noteId = btn.dataset.noteId;
                    try {
                        await api(`/businesses/${slug}/advisory/notes/${noteId}/acknowledge`, { method: "POST" });
                        btn.closest(".advisory-note").remove();
                        showToast("Note acknowledged");
                    } catch (e) {
                        showToast(e.message);
                    }
                });
            });
        } else {
            notesEl.innerHTML = '<div class="empty">No unacknowledged advisory notes.</div>';
        }
    } catch (error) {
        digestEl.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
}

// ─── Progress Strip ───────────────────────────────────────────────
function updateProgressStrip() {
    const strip = document.getElementById("dash-progress");
    if (!strip || !state.currentBusiness) { if (strip) strip.classList.add("hidden"); return; }
    strip.classList.remove("hidden");

    const day = state.currentBusiness.roadmap_day || 1;
    const phase = state.currentBusiness.current_phase || 1;
    document.getElementById("progress-phase").textContent = `Phase ${phase}`;
    document.getElementById("progress-day").textContent = `Day ${day}/90`;
    const pct = Math.min(100, (day / 90) * 100);
    document.getElementById("progress-fill").style.width = `${pct}%`;

    // Update health badge from API
    api(`/businesses/${state.currentBusiness.slug}/health`).then((h) => {
        const score = Math.round(h.overall || 0);
        const badge = document.getElementById("dash-health-badge");
        if (!badge) return;
        badge.textContent = score;
        badge.style.background = score >= 70 ? "rgba(16,185,129,0.2)" : score >= 40 ? "rgba(245,158,11,0.2)" : "rgba(239,68,68,0.2)";
        badge.style.color = score >= 70 ? "#34d399" : score >= 40 ? "#fbbf24" : "#f87171";
    }).catch(() => {});
}

// ─── Roadmap Bar (legacy shim) ─────────────────────────────────────
async function updateRoadmapBar() {
    updateProgressStrip();
}

// ─── Markdown renderer ────────────────────────────────────────────
function renderMarkdown(md) {
    if (!md) return "";
    let html = md
        // Tables
        .replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)*)/gm, (match, header, sep, body) => {
            const headers = header.split("|").filter((c) => c.trim()).map((c) => `<th>${c.trim()}</th>`).join("");
            const rows = body.trim().split("\n").map((row) => {
                const cells = row.split("|").filter((c) => c.trim()).map((c) => `<td>${c.trim()}</td>`).join("");
                return `<tr>${cells}</tr>`;
            }).join("");
            return `<table><thead><tr>${headers}</tr></thead><tbody>${rows}</tbody></table>`;
        })
        // Headers
        .replace(/^#### (.+)$/gm, "<h5>$1</h5>")
        .replace(/^### (.+)$/gm, "<h4>$1</h4>")
        .replace(/^## (.+)$/gm, "<h3>$1</h3>")
        .replace(/^# (.+)$/gm, "<h2>$1</h2>")
        // Bold and italic
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\*(.+?)\*/g, "<em>$1</em>")
        // Blockquotes
        .replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>")
        // Horizontal rules
        .replace(/^---+$/gm, "<hr>")
        // List items (unordered and numbered) — wrap in <li>
        .replace(/^- (.+)$/gm, "<li>$1</li>")
        .replace(/^\d+\. (.+)$/gm, "<li>$1</li>")
        // Wrap consecutive <li> in <ul>
        .replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>")
        // Paragraphs
        .replace(/\n\n/g, "</p><p>")
        .replace(/\n/g, "<br>");

    if (!html.startsWith("<")) html = "<p>" + html + "</p>";
    return html;
}

// ─── Work tab ─────────────────────────────────────────────────────
async function loadWork() {
    if (!state.currentBusiness) return;
    const slug = state.currentBusiness.slug;
    const list = document.getElementById("deliverables-list");
    if (!list) return;

    try {
        const items = await api(`/businesses/${slug}/content`);
        if (!items || !items.length) {
            list.innerHTML = '<div class="loading-state">Your first deliverables are being generated...</div>';
            document.getElementById("work-updated").textContent = "";
            return;
        }

        const typeLabels = {
            report: { label: "Report" },
            blog: { label: "Landing Page" },
            social: { label: "Social Post" },
            newsletter: { label: "Newsletter" },
        };

        const sortOrder = { Mission: 0, Offer: 0, Strategic: 0, Market: 1, Starter: 2, Landing: 2, Homepage: 2, Launch: 3 };
        items.sort((a, b) => {
            const aKey = (a.title || "").split(" ")[0];
            const bKey = (b.title || "").split(" ")[0];
            return (sortOrder[aKey] ?? 5) - (sortOrder[bKey] ?? 5);
        });

        // Keep a local map so copy buttons can look up body text without DOM encoding issues
        const bodyMap = {};
        list.innerHTML = items.map((c) => {
            const info = typeLabels[c.content_type] || { label: c.content_type };
            const rendered = renderMarkdown(c.body || "");
            bodyMap[c.id] = c.body || "";
            return `
                <article class="deliverable-card">
                    <div class="deliverable-header">
                        <span class="deliverable-type">${esc(info.label)}</span>
                        <span class="deliverable-status status-${esc(c.status)}">${esc(c.status)}</span>
                    </div>
                    <h3 class="deliverable-title">${esc(c.title || "Untitled")}</h3>
                    <div class="deliverable-body">${rendered}</div>
                    <div class="deliverable-actions">
                        <button class="btn-small btn-copy" data-copy-id="${c.id}">Copy</button>
                        ${c.status === "draft" ? `<button class="btn-small btn-publish-item" data-content-id="${c.id}">Publish</button>` : ""}
                    </div>
                </article>
            `;
        }).join("");

        // Wire copy buttons
        list.querySelectorAll(".btn-copy").forEach((btn) => {
            btn.addEventListener("click", () => {
                const body = bodyMap[btn.dataset.copyId] || "";
                navigator.clipboard.writeText(body).then(() => {
                    btn.textContent = "Copied!";
                    setTimeout(() => { btn.textContent = "Copy"; }, 1500);
                }).catch(() => showToast("Could not copy to clipboard", "error"));
            });
        });

        // Wire publish buttons
        list.querySelectorAll(".btn-publish-item").forEach((btn) => {
            btn.addEventListener("click", () => publishContent(btn.dataset.contentId, btn));
        });

        document.getElementById("work-updated").textContent = `${items.length} deliverable${items.length === 1 ? "" : "s"}`;
    } catch (e) {
        list.innerHTML = `<div class="loading-state">${esc(e.message)}</div>`;
    }
}

// ─── Plan tab ─────────────────────────────────────────────────────
async function loadPlan() {
    if (!state.currentBusiness) return;
    const slug = state.currentBusiness.slug;

    try {
        const [health, roadmap, , notes] = await Promise.all([
            api(`/businesses/${slug}/health`),
            api(`/businesses/${slug}/roadmap`),
            api(`/businesses/${slug}/forecast`),
            api(`/businesses/${slug}/advisory/notes?acknowledged=false`),
        ]);

        // Health row
        const overall = Math.round(health.overall || 0);
        const hColor = overall >= 70 ? "#10b981" : overall >= 40 ? "#f59e0b" : "#ef4444";
        const healthNum = document.getElementById("plan-health-number");
        if (healthNum) { healthNum.textContent = overall; healthNum.style.color = hColor; }

        const bars = health.sub_scores || {};
        const barsEl = document.getElementById("plan-health-bars");
        if (barsEl) {
            barsEl.innerHTML = Object.entries(bars).map(([k, v]) => {
                const c = v >= 70 ? "#10b981" : v >= 40 ? "#f59e0b" : "#ef4444";
                return `<div class="mini-bar-row"><span class="mini-bar-label">${esc(k.replace(/_/g, " "))}</span><div class="mini-bar"><div class="mini-bar-fill" style="width:${Math.min(v, 100)}%;background:${c}"></div></div><span class="mini-bar-value" style="color:${c}">${Math.round(v)}</span></div>`;
            }).join("");
        }

        // Phases
        const phases = roadmap.phases || [];
        const phasesEl = document.getElementById("plan-phases");
        if (phasesEl) {
            phasesEl.innerHTML = phases.map((p) => `
                <div class="plan-phase ${esc(p.status)}">
                    <div class="plan-phase-info">
                        <strong>Phase ${p.phase_number}</strong> ${esc(p.phase_name)}
                        <span class="plan-phase-badge">${esc(p.status)}</span>
                    </div>
                    <div class="plan-phase-bar"><div class="plan-phase-fill" style="width:${p.milestones_total ? (p.milestones_completed / p.milestones_total * 100) : 0}%"></div></div>
                    <span class="plan-phase-count">${p.milestones_completed}/${p.milestones_total}</span>
                </div>
            `).join("");
        }

        // Next milestones from active phase
        const active = phases.find((p) => p.status === "active");
        const pending = (active?.milestones || []).filter((m) => m.status !== "completed").slice(0, 5);
        const nextEl = document.getElementById("plan-next-milestones");
        if (nextEl) {
            nextEl.innerHTML = pending.map((m) => `
                <div class="next-milestone"><span class="milestone-dot"></span>${esc(m.title)}<span class="milestone-due">Day ${m.due_day || "?"}</span></div>
            `).join("") || '<div class="empty-state">All current milestones complete!</div>';
        }

        // Advisory notes (compact)
        const noteList = (notes.notes || []).slice(0, 5);
        const notesEl = document.getElementById("plan-notes");
        if (notesEl) {
            notesEl.innerHTML = noteList.length
                ? `<h3 class="plan-next-header">Alerts</h3>` + noteList.map((n) => `
                    <div class="plan-advisory-note note-${esc(n.category)}">
                        <span class="note-dot dot-${esc(n.category)}"></span>
                        <strong>${esc(n.title)}</strong>
                        <button class="btn-ack" data-note="${n.id}">Dismiss</button>
                    </div>
                `).join("")
                : "";

            notesEl.querySelectorAll(".btn-ack").forEach((btn) => {
                btn.addEventListener("click", async () => {
                    try {
                        await api(`/businesses/${slug}/advisory/notes/${btn.dataset.note}/acknowledge`, { method: "POST" });
                        btn.closest(".plan-advisory-note").remove();
                    } catch (e) {
                        showToast(e.message, "error");
                    }
                });
            });
        }
    } catch (e) {
        const phasesEl = document.getElementById("plan-phases");
        if (phasesEl) phasesEl.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    }
}

// ─── Account tab ──────────────────────────────────────────────────
async function loadAccount() {
    if (!state.currentBusiness) return;
    await Promise.all([loadBilling(), loadSettings()]);
}

async function loadSettings() {
    if (!state.currentBusiness) return;
    els.settingsContainer.innerHTML = '<div class="empty">Loading business settings...</div>';

    try {
        const settings = await api(`/businesses/${state.currentBusiness.slug}/settings`);
        const operatingPlan = settings.operating_plan || {};
        const workingDayModel = operatingPlan.working_day_model || null;
        const recommendations = (operatingPlan.user_recommendations || []).map((item) => `
            <div class="plan-line">
                <strong>${esc(item.title)}</strong>
                <span>${esc(item.detail)}</span>
            </div>
        `).join("");
        const agentTasks = nextThreeQueuedTasks(operatingPlan).map((item) => `
            <div class="plan-line">
                <strong>${esc(item.title || item.status_label)}</strong>
                <span>${esc(prettyArea(item.area))}: ${esc(item.expected_output)} · ${esc(formatQueueStatus(item))}</span>
            </div>
        `).join("");
        const addOnOffers = availableAddOnOffers(operatingPlan).map((item) => `
            <div class="plan-line">
                <strong>${esc(item.title)}</strong>
                <span>${esc(item.detail)} · includes ${item.working_days_required} dedicated night${item.working_days_required === 1 ? "" : "s"} · ${esc(prettyArea(item.status || "locked"))}</span>
                ${item.status === "available" ? `<button class="btn-secondary btn-small inline-action" data-buy-add-on="${esc(item.key)}">Buy Add-On</button>` : ""}
            </div>
        `).join("");
        const provisioningSteps = ((operatingPlan.provisioning || {}).steps || []).map((step) => `
            <div class="plan-line">
                <strong>${esc(step.label)}</strong>
                <span>${esc(formatStepStatus(step.status))} · ${esc(step.detail)}</span>
            </div>
        `).join("");
        els.settingsContainer.innerHTML = `
            <div class="setting-row"><span class="setting-label">Business name</span><span class="setting-value">${esc(settings.name)}</span></div>
            <div class="setting-row"><span class="setting-label">Description</span><span class="setting-value">${esc(settings.description)}</span></div>
            <div class="setting-row"><span class="setting-label">Source website</span><span class="setting-value">${settings.website_url ? `<a href="${esc(settings.website_url)}" target="_blank" rel="noreferrer">${esc(settings.website_url)}</a>` : "Not provided"}</span></div>
            <div class="setting-row"><span class="setting-label">Website summary</span><span class="setting-value">${esc(settings.website_summary || "No site summary captured.")}</span></div>
            <div class="setting-row"><span class="setting-label">Business address</span><span class="setting-value">${esc(settings.contact_email)}</span></div>
            <div class="setting-row"><span class="setting-label">Subdomain</span><span class="setting-value">${esc(settings.subdomain)}</span></div>
            <div class="setting-row"><span class="setting-label">Plan</span><span class="setting-value">${esc(settings.plan)}</span></div>
            <div class="setting-row"><span class="setting-label">Working days</span><span class="setting-value">${settings.working_days_remaining}</span></div>
            <div class="setting-row"><span class="setting-label">Template</span><span class="setting-value">${esc(formatTemplate(settings.template))}</span></div>
            ${operatingPlan.program_type ? `<div class="setting-row"><span class="setting-label">Program</span><span class="setting-value">${esc(operatingPlan.program_type === "existing_business" ? "Existing Business" : "New Venture")}</span></div>` : ""}
            ${workingDayModel ? `<div class="setting-row"><span class="setting-label">Working day model</span><span class="setting-value">${esc(workingDayModel.definition)}</span></div>` : ""}
            <div class="setting-row"><span class="setting-label">Domain</span><span class="setting-value"><span class="status-dot ${settings.subdomain_provisioned ? "active" : "inactive"}"></span>${settings.subdomain_provisioned ? "Live" : "Pending"}</span></div>
            <div class="setting-row"><span class="setting-label">Business address</span><span class="setting-value"><span class="status-dot ${settings.email_provisioned ? "active" : "inactive"}"></span>${settings.email_provisioned ? "Configured" : "Pending"}</span></div>
            <div class="setting-row"><span class="setting-label">Deployment</span><span class="setting-value"><span class="status-dot ${settings.app_deployed ? "active" : "inactive"}"></span>${settings.app_deployed ? "Live" : "Pending"}</span></div>
            ${operatingPlan.code_storage ? `<div class="setting-row"><span class="setting-label">Workspace path</span><span class="setting-value">${esc(operatingPlan.code_storage.workspace_path)}</span></div>` : ""}
            ${operatingPlan.code_storage ? `<div class="setting-row"><span class="setting-label">Workspace manifest</span><span class="setting-value">${esc(operatingPlan.code_storage.manifest_path)}</span></div>` : ""}
            ${recommendations ? `<div class="settings-section"><span class="summary-label">User Recommendations</span>${recommendations}</div>` : ""}
            ${agentTasks ? `<div class="settings-section"><span class="summary-label">Agent Launch Queue</span>${agentTasks}</div>` : ""}
            ${addOnOffers ? `<div class="settings-section"><span class="summary-label">Contextual Add-Ons</span>${addOnOffers}</div>` : ""}
            ${provisioningSteps ? `<div class="settings-section"><span class="summary-label">Provisioning Plan</span>${provisioningSteps}</div>` : ""}
        `;
        els.settingsContainer.querySelectorAll("[data-buy-add-on]").forEach((button) => {
            button.addEventListener("click", () => buyAddOn(button.dataset.buyAddOn));
        });
    } catch (error) {
        els.settingsContainer.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
}

async function loadBilling() {
    if (!state.currentBusiness) return;
    els.billingContainer.innerHTML = '<div class="empty">Loading billing details...</div>';

    try {
        const [billing, settings] = await Promise.all([
            api(`/businesses/${state.currentBusiness.slug}/billing/status`),
            api(`/businesses/${state.currentBusiness.slug}/settings`),
        ]);
        const operatingPlan = settings.operating_plan || {};
        const availableAddOns = (operatingPlan.add_on_offers || []).filter((item) => item.status === "available");
        const addOnCatalog = new Map((billing.add_ons || []).map((item) => [item.key, item]));
        state.currentBusiness = { ...state.currentBusiness, plan: billing.plan };
        updateStoredBusiness(state.currentBusiness);
        const currentPlan = PLANS[billing.plan] || PLANS.preview;
        const upgradeCards = Object.entries(PLANS)
            .filter(([planKey]) => ["starter", "pro", "growth", "scale"].includes(planKey) && planKey !== billing.plan)
            .map(([planKey, plan]) => `
                <div class="plan-card">
                    <h3>${esc(plan.name)} ${planKey === "starter" ? '<span class="founder-badge">48h Trial</span>' : planKey === "pro" ? '<span class="founder-badge">Recommended</span>' : ""}</h3>
                    <div class="plan-price">${plan.price.replace("/mo", "")}<span>/mo</span></div>
                    <p class="plan-note">${plan.working_days} working days for up to ${plan.companies} compan${plan.companies === 1 ? "y" : "ies"}${plan.trialDays ? ` with a ${plan.trialDays}-day trial, card captured up front, and automatic billing after the trial` : ""}.</p>
                    <ul>
                        <li>${plan.working_days} autonomous cycles</li>
                        <li>Manage up to ${plan.companies} compan${plan.companies === 1 ? "y" : "ies"}</li>
                        <li>Revenue share: 5% and managed ad spend take: 7.5%</li>
                    </ul>
                    <button class="btn-primary" data-upgrade-plan="${planKey}">
                        ${planKey === "starter" ? "Start Starter Trial" : `Upgrade to ${esc(plan.name)}`}
                    </button>
                </div>
            `)
            .join("");

        const dayPackCards = (billing.day_packs || []).map((pack) => `
            <div class="plan-card">
                <h3>${esc(pack.name)}</h3>
                <div class="plan-price">${formatMoneyCents(pack.price_cents).replace(".00", "")}<span> one-time</span></div>
                <p class="plan-note">${pack.working_days} extra working days. Designed to cost more than stepping up to the next plan.</p>
                <ul>
                    <li>${pack.working_days} extra working days</li>
                    <li>Best for temporary bursts</li>
                    <li>Upgrade tiers first if you expect ongoing usage</li>
                </ul>
                <button class="btn-secondary" data-day-pack="${esc(pack.key)}">Buy ${esc(pack.name)}</button>
            </div>
        `).join("");
        const addOnCards = availableAddOns.map((item) => `
            <div class="plan-card">
                <h3>${esc(item.title)} <span class="founder-badge">Queue Cut</span></h3>
                <div class="plan-price">${formatMoneyCents(addOnCatalog.get(item.key)?.price_cents || 0).replace(".00", "")}<span> one-time</span></div>
                <p class="plan-note">${esc(item.detail)}</p>
                <ul>
                    <li>Cuts ahead of the normal queue</li>
                    <li>Includes ${addOnCatalog.get(item.key)?.included_cycles || item.working_days_required} dedicated night${(addOnCatalog.get(item.key)?.included_cycles || item.working_days_required) === 1 ? "" : "s"} of execution</li>
                    <li>Best when this matters more than the next default step</li>
                </ul>
                <button class="btn-secondary" data-buy-add-on="${esc(item.key)}">Buy ${esc(item.title)}</button>
            </div>
        `).join("");

        els.billingContainer.innerHTML = `
            <div class="setting-row"><span class="setting-label">Current plan</span><span class="setting-value">${esc(currentPlan.name)} - ${esc(currentPlan.price)}</span></div>
            <div class="setting-row"><span class="setting-label">Subscription status</span><span class="setting-value">${billing.active ? "Active" : "Inactive"}</span></div>
            <div class="setting-row"><span class="setting-label">Available working days</span><span class="setting-value">${billing.working_days_remaining}</span></div>
            <div class="setting-row"><span class="setting-label">Working days included</span><span class="setting-value">${billing.working_days_included}</span></div>
            <div class="setting-row"><span class="setting-label">Businesses</span><span class="setting-value">${billing.company_count} of ${billing.company_limit} used</span></div>
            ${billing.trial_days ? `<div class="setting-row"><span class="setting-label">Trial window</span><span class="setting-value">${billing.trial_days} days</span></div>` : ""}
            ${billing.can_start_paid_trial ? `<div class="setting-row"><span class="setting-label">Upgrade path</span><span class="setting-value">Enter a card to start a 48-hour trial on your selected plan, then auto-roll into subscription billing.</span></div>` : ""}
            ${addOnCards ? `<div class="settings-section"><span class="summary-label">Available Add-Ons</span><div class="plan-grid">${addOnCards}</div></div>` : ""}
            ${upgradeCards ? `<div class="plan-grid">${upgradeCards}</div>` : ""}
            ${dayPackCards ? `<div class="plan-grid">${dayPackCards}</div>` : ""}
        `;

        els.billingContainer.querySelectorAll("[data-upgrade-plan]").forEach((button) => {
            button.addEventListener("click", () => upgradePlan(button.dataset.upgradePlan));
        });
        els.billingContainer.querySelectorAll("[data-day-pack]").forEach((button) => {
            button.addEventListener("click", () => buyDayPack(button.dataset.dayPack));
        });
        els.billingContainer.querySelectorAll("[data-buy-add-on]").forEach((button) => {
            button.addEventListener("click", () => buyAddOn(button.dataset.buyAddOn));
        });
    } catch (error) {
        els.billingContainer.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
}

async function upgradePlan(plan) {
    try {
        const result = await api(`/businesses/${state.currentBusiness.slug}/billing/checkout`, {
            method: "POST",
            body: JSON.stringify({ plan }),
        });
        if (result.checkout_url) {
            window.open(result.checkout_url, "_blank", "noopener");
            showToast("Checkout opened in a new tab.");
            return;
        }
        throw new Error("Unable to create checkout session.");
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function buyDayPack(dayPack) {
    try {
        const result = await api(`/businesses/${state.currentBusiness.slug}/billing/checkout`, {
            method: "POST",
            body: JSON.stringify({ day_pack: dayPack }),
        });
        if (result.checkout_url) {
            window.open(result.checkout_url, "_blank", "noopener");
            showToast("Day pack checkout opened in a new tab.");
            return;
        }
        throw new Error("Unable to create checkout session.");
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function buyAddOn(addOnKey) {
    if (!addOnKey || !state.currentBusiness) return;
    try {
        const result = await api(`/businesses/${state.currentBusiness.slug}/billing/checkout`, {
            method: "POST",
            body: JSON.stringify({ add_on: addOnKey }),
        });
        if (result.checkout_url) {
            window.open(result.checkout_url, "_blank", "noopener");
            showToast("Add-on checkout opened in a new tab.");
            return;
        }
        throw new Error("Unable to create add-on checkout session.");
    } catch (error) {
        showToast(error.message, "error");
    }
}

function esc(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
}

function truncate(value, maxLength) {
    const text = String(value || "");
    const clipped = text.length > maxLength ? `${text.slice(0, maxLength)}...` : text;
    return esc(clipped);
}

function formatTemplate(template) {
    if (!template) return "Not selected";
    return template
        .split("-")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
}

function formatNumber(value) {
    if (Math.abs(value) >= 1000000) return `${(value / 1000000).toFixed(1)}M`;
    if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(1)}K`;
    return Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 1);
}

function prettyType(contentType) {
    return String(contentType || "deliverable")
        .split("-")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

function prettyArea(area) {
    return String(area || "general")
        .split("_")
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
}

function formatMoneyCents(cents) {
    return `$${(Number(cents || 0) / 100).toFixed(2)}`;
}

function formatWorkingDayValue(cents) {
    if (!cents) return "Free Day 1";
    return `${formatMoneyCents(cents)} per working day`;
}

function formatSourceLabel(url) {
    try {
        return new URL(url).hostname.replace(/^www\./, "");
    } catch (_) {
        return url;
    }
}

function cycleDurationSeconds(cycle) {
    if (!cycle?.started_at || !cycle?.completed_at) return null;
    const durationMs = new Date(cycle.completed_at).getTime() - new Date(cycle.started_at).getTime();
    return durationMs > 0 ? Math.round(durationMs / 1000) : null;
}

function formatDurationSeconds(seconds) {
    if (!seconds && seconds !== 0) return "";
    if (seconds < 60) return `${seconds}s`;
    const mins = Math.floor(seconds / 60);
    const remaining = seconds % 60;
    return remaining ? `${mins}m ${remaining}s` : `${mins}m`;
}

function showcaseScore(item) {
    const weights = { report: 4, social: 3, blog: 2, newsletter: 1 };
    return weights[item.content_type] || 0;
}

function normalizeOpsAction(action) {
    const raw = String(action || "").trim();
    const replacements = {
        "Business launched": "Business launched",
        "Operating plan prepared": "Locking launch plan",
        "Provisioning started": "Provisioning tenant stack",
        "Provisioning complete": "Tenant stack ready",
        "Updating task list...": "Refreshing launch queue",
        "Reviewing documents...": "Reviewing business context",
        "Structuring strategy brief...": "Structuring strategy brief",
        "Searching market...": "Mapping market landscape",
        "Drafting deliverable...": "Drafting launch asset",
        "Saving report...": "Saving deliverable",
        "Managing infrastructure...": "Syncing infrastructure",
        "Coordinating launch workflow...": "Coordinating launch workflow",
    };
    return replacements[raw] || raw.replace(/\.\.\.$/, "");
}

function formatStepStatus(status) {
    const normalized = String(status || "pending");
    const labels = {
        pending: "Pending",
        running: "Running",
        ready: "Ready",
        failed: "Needs review",
        skipped: "Skipped",
    };
    return labels[normalized] || prettyArea(normalized);
}

document.getElementById("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    const button = event.target.querySelector("button");

    setFormBusy(button, "Signing In...", "Sign In", true);
    hideError("login-error");

    try {
        const result = await api("/auth/login", {
            method: "POST",
            body: JSON.stringify({ email, password }),
        });
        state.userEmail = result.email;

        const businesses = await api("/businesses");
        setBusinesses(businesses);
        if (businesses.length > 0) {
            enterDashboard(businesses[0]);
        } else {
            showView("create-view");
        }
    } catch (error) {
        showError("login-error", error.message);
    } finally {
        setFormBusy(button, "Signing In...", "Sign In", false);
    }
});

document.getElementById("signup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = document.getElementById("signup-email").value;
    const password = document.getElementById("signup-password").value;
    const button = event.target.querySelector("button");

    setFormBusy(button, "Creating Account...", "Create Account", true);
    hideError("signup-error");

    try {
        const result = await api("/auth/register", {
            method: "POST",
            body: JSON.stringify({ email, password }),
        });
        state.userEmail = result.email;
        setBusinesses([]);
        showView("create-view");
    } catch (error) {
        showError("signup-error", error.message);
    } finally {
        setFormBusy(button, "Creating Account...", "Create Account", false);
    }
});

if (els.oauthGoogle) {
    els.oauthGoogle.addEventListener("click", () => {
        window.location.href = `${API}/auth/login/google`;
    });
}

if (els.oauthGithub) {
    els.oauthGithub.addEventListener("click", () => {
        window.location.href = `${API}/auth/login/github`;
    });
}

document.getElementById("forgot-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = document.getElementById("forgot-email").value;
    const button = event.target.querySelector("button");

    setFormBusy(button, "Sending...", "Send Reset Link", true);
    try {
        await api("/auth/forgot-password", {
            method: "POST",
            body: JSON.stringify({ email }),
        });
    } catch (_) {
        // Always show success to avoid email enumeration.
    } finally {
        setFormBusy(button, "Sending...", "Send Reset Link", false);
        event.target.reset();
        document.getElementById("forgot-success").classList.remove("hidden");
    }
});

document.getElementById("create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    hideError("create-error");

    if (!state.userEmail) {
        showView("signup-view");
        return;
    }

    setFormBusy(button, "Launching...", "Launch Business", true);

    try {
        const mode = document.getElementById("biz-mode").value || "idea";
        const template = document.querySelector('input[name="biz-template"]:checked')?.value || "content-site";
        const description = document.getElementById("biz-description").value.trim();
        const websiteUrl = document.getElementById("biz-website").value.trim();
        const goals = document.getElementById("biz-goals").value.trim();

        if (mode === "idea" && !description) {
            throw new Error("Describe the business you want Arclane to launch.");
        }
        if (mode === "existing" && !websiteUrl) {
            throw new Error("Enter the live website you want Arclane to research.");
        }

        const payloadDescription = mode === "existing"
            ? goals || "Research this business, modernize the offer, and create a stronger launch surface."
            : description;

        const business = await api("/businesses", {
            method: "POST",
            body: JSON.stringify({
                name: null,
                description: payloadDescription || null,
                website_url: websiteUrl || null,
                template,
            }),
        });
        enterDashboard(business);
        showToast(`Business launched as ${business.name}. Provisioning and the first cycle are running.`);
    } catch (error) {
        showError("create-error", error.message);
    } finally {
        setFormBusy(button, "Launching...", "Launch Business", false);
    }
});

document.getElementById("show-signup").addEventListener("click", (event) => {
    event.preventDefault();
    document.getElementById("signup-form").reset();
    hideError("signup-error");
    showView("signup-view");
});

document.getElementById("show-login-from-signup").addEventListener("click", (event) => {
    event.preventDefault();
    showView("login-view");
});

document.getElementById("show-login").addEventListener("click", (event) => {
    event.preventDefault();
    if (state.userEmail && state.currentBusiness) {
        showView("dashboard-view");
        return;
    }
    showView("login-view");
});

document.querySelectorAll("[data-create-mode]").forEach((button) => {
    button.addEventListener("click", () => setCreateMode(button.dataset.createMode));
});

document.getElementById("show-forgot").addEventListener("click", (event) => {
    event.preventDefault();
    document.getElementById("forgot-form").reset();
    document.getElementById("forgot-success").classList.add("hidden");
    showView("forgot-view");
});

document.getElementById("show-login-from-forgot").addEventListener("click", (event) => {
    event.preventDefault();
    showView("login-view");
});

document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", (event) => {
        if (!link.dataset.page) return;
        event.preventDefault();
        showPage(link.dataset.page);
    });
});

document.querySelectorAll(".dash-tab").forEach((tab) => {
    tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

els.businessSwitcher.addEventListener("change", () => {
    const selected = state.businesses.find((business) => business.slug === els.businessSwitcher.value);
    if (selected) {
        enterDashboard(selected);
    }
});

els.newBusinessBtn.addEventListener("click", () => {
    if (state.businesses.length >= currentCompanyLimit()) {
        showToast("This account has used all company slots. Upgrade to Growth or Scale.", "error");
        showPage("billing");
        return;
    }
    document.getElementById("create-form").reset();
    setCreateMode("idea");
    hideError("create-error");
    showView("create-view");
});

document.querySelectorAll(".filter-btn").forEach((button) => {
    button.addEventListener("click", () => {
        document.querySelectorAll(".filter-btn").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        loadContent(button.dataset.filter);
    });
});

document.querySelectorAll(".suggestion-chip, .quick-task").forEach((button) => {
    button.addEventListener("click", () => openTaskModal(button.dataset.task || ""));
});

document.querySelector(".low-working-day-link").addEventListener("click", (event) => {
    event.preventDefault();
    showTab("account");
});

document.getElementById("dash-run-task").addEventListener("click", () => openTaskModal());
document.getElementById("task-cancel").addEventListener("click", closeTaskModal);

els.taskModal.addEventListener("click", (event) => {
    if (event.target === els.taskModal) closeTaskModal();
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.taskModal.classList.contains("hidden")) {
        closeTaskModal();
    }
});

async function handleTaskSubmit() {
    const description = els.taskInput.value.trim();
    if (!description || !state.currentBusiness) return;

    els.taskSubmit.disabled = true;
    els.taskSubmit.textContent = "Running...";

    try {
        await api(`/businesses/${state.currentBusiness.slug}/cycles`, {
            method: "POST",
            body: JSON.stringify({ task_description: description }),
        });
        state.currentBusiness.working_days_remaining = Math.max(0, state.currentBusiness.working_days_remaining - 1);
        updateWorkingDayGauge();
        closeTaskModal();
        await refreshBusinessSummary(true);
        if (state.currentTab === "work") await loadWork();
        showToast("Task queued. Watch the feed for progress.");
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        els.taskSubmit.disabled = false;
        els.taskSubmit.textContent = "Run";
    }
}

els.taskSubmit.addEventListener("click", handleTaskSubmit);

setCreateMode("idea");

const query = readQueryParams();
// OAuth callback passes access_token in URL — clean it from history
if (query.get("access_token")) {
    window.history.replaceState({}, "", "/dashboard");
}
const authError = query.get("auth_error");
if (authError) {
    showToast(authError === "no_email" ? "OAuth account did not provide an email address." : "OAuth sign-in failed.", "error");
    clearQueryParams();
}

// Validate session via httpOnly cookie (set during login)
api("/auth/validate")
    .then((result) => {
        state.userEmail = result.email;
        return api("/businesses");
    })
    .then((businesses) => {
        setBusinesses(businesses);
        if (businesses.length > 0) {
            enterDashboard(businesses[0]);
        } else if (window.location.hash === "#create") {
            showView("create-view");
        } else {
            showView("create-view");
        }
    })
    .catch(() => {
        clearAuthState();
        if (window.location.hash === "#create") {
            showView("signup-view");
        }
    });
