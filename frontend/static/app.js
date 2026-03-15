const API = "/api";

const state = {
    currentBusiness: null,
    businesses: [],
    authToken: null,
    userEmail: null,
    currentPage: "feed",
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
    creditsDisplay: document.getElementById("credits-display"),
    feedList: document.getElementById("feed-list"),
    contentList: document.getElementById("content-list"),
    metricsGrid: document.getElementById("metrics-grid"),
    metricsChartWrap: document.getElementById("metrics-chart-wrap"),
    billingContainer: document.getElementById("billing-container"),
    settingsContainer: document.getElementById("settings-form-container"),
    bizSlug: document.getElementById("biz-slug"),
    bizNameHeading: document.getElementById("biz-name-heading"),
    bizDescriptionCopy: document.getElementById("biz-description-copy"),
    businessLink: document.getElementById("business-link"),
    latestCyclePill: document.getElementById("latest-cycle-pill"),
    provisioningStatus: document.getElementById("provisioning-status"),
    provisioningDetail: document.getElementById("provisioning-detail"),
    cycleStatus: document.getElementById("cycle-status"),
    cycleDetail: document.getElementById("cycle-detail"),
    templateStatus: document.getElementById("template-status"),
    templateDetail: document.getElementById("template-detail"),
    creditGauge: document.getElementById("credit-gauge"),
    creditDot: document.getElementById("credit-dot"),
    creditCount: document.getElementById("credit-count"),
    lowCreditBanner: document.getElementById("low-credit-banner"),
    businessSwitcher: document.getElementById("business-switcher"),
    newBusinessBtn: document.getElementById("new-business-btn"),
    portfolioStatus: document.getElementById("portfolio-status"),
    portfolioDetail: document.getElementById("portfolio-detail"),
    proofSpeedStatus: document.getElementById("proof-speed-status"),
    proofSpeedDetail: document.getElementById("proof-speed-detail"),
    proofOutputStatus: document.getElementById("proof-output-status"),
    proofOutputDetail: document.getElementById("proof-output-detail"),
    proofQualityStatus: document.getElementById("proof-quality-status"),
    proofQualityDetail: document.getElementById("proof-quality-detail"),
    nextThreeList: document.getElementById("next-three-list"),
    queueAddOnOffers: document.getElementById("queue-add-on-offers"),
    queueAddOnList: document.getElementById("queue-add-on-list"),
    showcaseList: document.getElementById("showcase-list"),
    opsStreamTrack: document.getElementById("ops-stream-track"),
    oauthGoogle: document.getElementById("oauth-google"),
    oauthGithub: document.getElementById("oauth-github"),
};

const PLANS = {
    preview: { name: "Preview", price: "Free", credits: 3, companies: 1 },
    starter: { name: "Starter", price: "$49/mo", credits: 10, companies: 1, trialDays: 3 },
    pro: { name: "Pro", price: "$99/mo", credits: 20, companies: 1 },
    growth: { name: "Growth", price: "$249/mo", credits: 75, companies: 3 },
    scale: { name: "Scale", price: "$499/mo", credits: 150, companies: 5 },
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
    state.currentPage = name;
    document.querySelectorAll(".page").forEach((page) => page.classList.add("hidden"));
    document.getElementById(`page-${name}`).classList.remove("hidden");
    document.querySelectorAll(".nav-link").forEach((link) => link.classList.remove("active"));
    const activeLink = document.querySelector(`.nav-link[data-page="${name}"]`);
    if (activeLink) activeLink.classList.add("active");

    if (name === "feed") loadFeed();
    if (name === "content") loadContent(state.currentContentFilter);
    if (name === "metrics") loadMetrics();
    if (name === "billing") loadBilling();
    if (name === "settings") loadSettings();
}

async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...options.headers };
    if (state.authToken) headers.Authorization = `Bearer ${state.authToken}`;

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
    state.authToken = null;
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
    updatePortfolioSummary();
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
    updatePortfolioSummary();
}

function currentCompanyLimit() {
    if (!state.businesses.length) return 1;
    return state.businesses.reduce((limit, business) => {
        const plan = PLANS[business.plan] || PLANS.preview;
        return Math.max(limit, plan.companies || 1);
    }, 1);
}

function updatePortfolioSummary() {
    const count = state.businesses.length || (state.currentBusiness ? 1 : 0);
    const limit = currentCompanyLimit();
    els.portfolioStatus.textContent = `${count} of ${limit} company slot${limit === 1 ? "" : "s"} used`;
    els.portfolioDetail.textContent = limit > count
        ? `You can add ${limit - count} more compan${limit - count === 1 ? "y" : "ies"} on this account.`
        : "Upgrade to Growth or Scale to manage more businesses from one account.";
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
    els.creditsDisplay.textContent = state.currentBusiness
        ? `${state.currentBusiness.credits_remaining} credits remaining`
        : "";
    els.taskModal.classList.remove("hidden");
    els.taskModal.setAttribute("aria-hidden", "false");
    window.setTimeout(() => els.taskInput.focus(), 0);
}

function closeTaskModal() {
    els.taskModal.classList.add("hidden");
    els.taskModal.setAttribute("aria-hidden", "true");
    els.taskInput.value = "";
}

function updateCreditGauge() {
    const credits = state.currentBusiness ? state.currentBusiness.credits_remaining : 0;
    els.creditGauge.classList.remove("hidden");
    els.creditCount.textContent = `${credits} credit${credits === 1 ? "" : "s"}`;
    els.creditDot.className = "credit-dot";

    if (credits > 5) {
        els.creditDot.classList.add("green");
    } else if (credits >= 2) {
        els.creditDot.classList.add("yellow");
    } else {
        els.creditDot.classList.add("red");
    }

    if (credits <= 2) {
        els.lowCreditBanner.classList.remove("hidden");
    } else {
        els.lowCreditBanner.classList.add("hidden");
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
    els.bizSlug.textContent = business.subdomain;
    els.bizNameHeading.textContent = business.name;
    els.bizDescriptionCopy.textContent = business.description || "Describe the business to guide Arclane.";
    els.businessLink.href = `https://${business.subdomain}`;
    els.businessLink.textContent = `Open ${business.subdomain}`;
    els.businessLink.classList.remove("hidden");
    updateCreditGauge();
    showView("dashboard-view");
    showPage("feed");
    refreshBusinessSummary();
    startPolling();
    startFeedStream();
}

function startPolling() {
    stopPolling();
    state.pollTimer = window.setInterval(async () => {
        if (!state.currentBusiness) return;
        await refreshBusinessSummary(true);
        if (state.currentPage === "feed") await loadFeed(true);
        if (state.currentPage === "content") await loadContent(state.currentContentFilter, true);
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
        const [settings, cycles, content, feedItems] = await Promise.all([
            api(`/businesses/${state.currentBusiness.slug}/settings`),
            api(`/businesses/${state.currentBusiness.slug}/cycles`),
            api(`/businesses/${state.currentBusiness.slug}/content?limit=6`),
            api(`/businesses/${state.currentBusiness.slug}/feed?limit=12`),
        ]);

        state.currentBusiness = {
            ...state.currentBusiness,
            name: settings.name,
            description: settings.description,
            website_url: settings.website_url,
            website_summary: settings.website_summary,
            subdomain: settings.subdomain,
            credits_remaining: settings.credits_remaining,
            template: settings.template,
            plan: settings.plan,
            operating_plan: settings.operating_plan,
        };

        updateStoredBusiness(state.currentBusiness);
        renderBusinessSummary(settings, cycles, content);
        applyOperatingRecommendations(settings.operating_plan);
        renderQueuePreview(settings.operating_plan);
        state.feedItems = feedItems || [];
        updateOpsStream(feedItems);
        if (state.currentPage === "feed") {
            renderFeedList(state.feedItems);
        }
        updateCreditGauge();
    } catch (error) {
        if (!silent) showToast(error.message, "error");
    }
}

function renderBusinessSummary(settings, cycles, contentItems = []) {
    const operatingPlan = settings.operating_plan || {};
    const nextQueueTask = nextQueuedTask(operatingPlan);
    els.bizSlug.textContent = settings.subdomain;
    els.bizNameHeading.textContent = settings.name;
    els.bizDescriptionCopy.textContent = settings.description || "Add business context so tasks stay sharp.";
    els.businessLink.href = `https://${settings.subdomain}`;
    els.businessLink.textContent = `Open ${settings.subdomain}`;

    const readySteps = [
        settings.subdomain_provisioned,
        settings.email_provisioned,
        settings.app_deployed || !settings.template,
    ].filter(Boolean).length;

    els.provisioningStatus.textContent = readySteps === 3
        ? "Infrastructure is ready"
        : `${readySteps}/3 setup steps completed`;
    els.provisioningDetail.textContent = [
        settings.subdomain_provisioned ? "domain live" : "domain pending",
        settings.email_provisioned ? `business address configured (${settings.contact_email})` : "business address pending",
        settings.app_deployed || !settings.template ? "workspace ready" : "deployment pending",
    ].join(" | ");

    const latestCycle = cycles[0];
    if (!latestCycle) {
        els.latestCyclePill.className = "status-pill neutral";
        els.latestCyclePill.textContent = "Waiting for first cycle";
        els.cycleStatus.textContent = "No cycles yet";
        els.cycleDetail.textContent = "Queue work to start the first run.";
    } else {
        const cycleCopy = cycleStatusCopy(latestCycle.status);
        els.latestCyclePill.className = `status-pill ${cycleCopy.tone}`;
        els.latestCyclePill.textContent = cycleCopy.label;
        els.cycleStatus.textContent = `${cycleCopy.label} (${latestCycle.trigger.replace("_", " ")})`;
        els.cycleDetail.textContent = `Created ${timeAgo(latestCycle.created_at)}. Refreshes every few seconds.`;
    }

    els.templateStatus.textContent = formatTemplate(settings.template);
    els.templateDetail.textContent = nextQueueTask
        ? `Next queued output: ${nextQueueTask.title} (${formatQueueStatus(nextQueueTask)})`
        : settings.website_url
            ? `Optimizing around ${formatSourceLabel(settings.website_url)} while provisioning the default shell.`
            : settings.template
                ? "This is the default surface Arclane provisions and optimizes around."
                : "No template selected yet.";

    renderExecutionProof(cycles, contentItems);
}

function applyOperatingRecommendations(operatingPlan) {
    const recommendations = operatingPlan?.user_recommendations || [];
    const agentTasks = nextThreeQueuedTasks(operatingPlan);

    document.querySelectorAll(".quick-task").forEach((button, index) => {
        const recommendation = recommendations[index];
        if (!recommendation) return;
        button.textContent = recommendation.title;
        button.dataset.task = recommendation.task;
    });

    document.querySelectorAll(".suggestion-chip").forEach((button, index) => {
        const task = agentTasks[index];
        if (!task) return;
        button.textContent = task.status_label;
        button.dataset.task = task.description;
    });
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
    const nextThree = nextThreeQueuedTasks(operatingPlan);
    const offers = availableAddOnOffers(operatingPlan);

    if (!nextThree.length) {
        els.nextThreeList.innerHTML = '<div class="empty">No queued outputs are waiting right now.</div>';
    } else {
        els.nextThreeList.innerHTML = nextThree.map((item, index) => `
            <article class="queue-preview-card">
                <div class="queue-preview-topline">
                    <span class="badge badge-${esc(item.area || "general")}">Step ${index + 1}</span>
                    <span class="queue-preview-status">${esc(formatQueueStatus(item))}</span>
                </div>
                <h3>${esc(item.title || item.status_label)}</h3>
                <p>${esc(item.brief || item.description || "Queued work item")}</p>
            </article>
        `).join("");
    }

    if (!offers.length) {
        els.queueAddOnOffers.classList.add("hidden");
        els.queueAddOnList.innerHTML = "";
        return;
    }

    els.queueAddOnOffers.classList.remove("hidden");
    els.queueAddOnList.innerHTML = offers.map((item) => `
        <article class="queue-preview-card queue-preview-card-addon">
            <div class="queue-preview-topline">
                <span class="badge badge-report">Add-On</span>
                <span class="queue-preview-status">${item.credits_required} credit${item.credits_required === 1 ? "" : "s"}</span>
            </div>
            <h3>${esc(item.title)}</h3>
            <p>${esc(item.detail)}</p>
            <div class="queue-preview-actions">
                <button class="btn-secondary btn-small" data-buy-add-on="${esc(item.key)}">Buy Add-On</button>
                <button class="btn-secondary btn-small" data-open-page="content">View Output</button>
            </div>
        </article>
    `).join("");
    els.queueAddOnList.querySelectorAll("[data-buy-add-on]").forEach((button) => {
        button.addEventListener("click", () => buyAddOn(button.dataset.buyAddOn));
    });
    els.queueAddOnList.querySelectorAll("[data-open-page]").forEach((button) => {
        button.addEventListener("click", () => showPage(button.dataset.openPage));
    });
}

function renderExecutionProof(cycles, contentItems) {
    const completedCycles = (cycles || []).filter((cycle) => cycle.completed_at);
    const latestCompletedCycle = completedCycles[0];
    const newestItem = (contentItems || [])[0];
    const reports = (contentItems || []).filter((item) => item.content_type === "report");
    const social = (contentItems || []).filter((item) => item.content_type === "social");

    if (latestCompletedCycle) {
        const duration = formatDurationSeconds(cycleDurationSeconds(latestCompletedCycle));
        els.proofSpeedStatus.textContent = duration
            ? `Latest cycle completed in ${duration}`
            : "Latest cycle completed";
        els.proofSpeedDetail.textContent = `Triggered ${timeAgo(latestCompletedCycle.created_at)} with ${taskOutcomeCopy(latestCompletedCycle)}.`;
    } else {
        els.proofSpeedStatus.textContent = "Waiting for first finished cycle";
        els.proofSpeedDetail.textContent = "As soon as work completes, Arclane will show how fast it got there.";
    }

    if (contentItems.length) {
        els.proofOutputStatus.textContent = `${contentItems.length} deliverable${contentItems.length === 1 ? "" : "s"} visible now`;
        els.proofOutputDetail.textContent = contentItems
            .slice(0, 3)
            .map((item) => item.title || prettyType(item.content_type))
            .join(" | ");
    } else {
        els.proofOutputStatus.textContent = "No deliverables yet";
        els.proofOutputDetail.textContent = "Reports, social drafts, and conversion assets will appear here first.";
    }

    if (newestItem) {
        const qualitySignal = QUALITY_COPY[newestItem.content_type] || "User-facing deliverable";
        els.proofQualityStatus.textContent = qualitySignal;
        els.proofQualityDetail.textContent = newestItem.title
            ? `${newestItem.title} landed ${timeAgo(newestItem.created_at)}.`
            : `${prettyType(newestItem.content_type)} landed ${timeAgo(newestItem.created_at)}.`;
    } else {
        els.proofQualityStatus.textContent = "Waiting on first proof point";
        els.proofQualityDetail.textContent = "This should make the value obvious in a few seconds, not after a tour.";
    }

    renderShowcase(contentItems, latestCompletedCycle, reports, social);
}

function renderShowcase(contentItems, latestCompletedCycle, reports, social) {
    if (!contentItems.length) {
        els.showcaseList.innerHTML = '<div class="empty">The first deliverables will surface here automatically.</div>';
        return;
    }

    const featured = [...contentItems]
        .sort((a, b) => showcaseScore(b) - showcaseScore(a))
        .slice(0, 3);

    els.showcaseList.innerHTML = featured.map((item, index) => `
        <article class="showcase-card ${index === 0 ? "primary" : ""}">
            <div class="card-topline">
                <span class="badge badge-${esc(item.content_type)}">${esc(item.content_type)}</span>
                <span class="showcase-quality">${esc(QUALITY_COPY[item.content_type] || "Visible output")}</span>
            </div>
            <h3>${esc(item.title || prettyType(item.content_type))}</h3>
            <p>${truncate(item.body, index === 0 ? 320 : 180)}</p>
            <div class="showcase-meta">
                <span>${timeAgo(item.created_at)}</span>
                ${latestCompletedCycle ? `<span>${esc(taskOutcomeCopy(latestCompletedCycle))}</span>` : ""}
                ${reports.length ? `<span>${reports.length} reports</span>` : ""}
                ${social.length ? `<span>${social.length} social draft${social.length === 1 ? "" : "s"}</span>` : ""}
            </div>
        </article>
    `).join("");
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
    if (!silent) {
        els.feedList.innerHTML = '<div class="empty">Loading recent activity...</div>';
    }

    try {
        const items = await api(`/businesses/${state.currentBusiness.slug}/feed`);
        state.feedItems = items || [];
        updateOpsStream(items);
        renderFeedList(state.feedItems);
    } catch (error) {
        if (!silent) {
            els.feedList.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
        }
    }
}

function renderFeedList(items) {
    if (!items.length) {
        els.feedList.innerHTML = '<div class="empty">No activity yet. Queue a task to get work moving.</div>';
        return;
    }
    els.feedList.innerHTML = items.map((item) => `
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
        if (state.currentPage === "feed") {
            renderFeedList(state.feedItems);
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
    renderOpsStream();
}

function renderOpsStream() {
    const items = state.opsStreamItems.slice(0, 8);
    if (!items.length) {
        els.opsStreamTrack.innerHTML = '<span class="ops-stream-item">Waiting for launch activity...</span>';
        return;
    }

    const markup = items.map((item) => `
        <span class="ops-stream-item">
            <strong>${esc(normalizeOpsAction(item.action))}</strong>
            ${item.detail ? `<span>${truncate(item.detail, 88)}</span>` : ""}
            <em>${timeAgo(item.created_at)}</em>
        </span>
    `).join("");

    els.opsStreamTrack.innerHTML = `${markup}${markup}`;
}

async function loadContent(filter = "", silent = false) {
    if (!state.currentBusiness) return;
    state.currentContentFilter = filter;
    if (!silent) {
        els.contentList.innerHTML = '<div class="empty">Loading content output...</div>';
    }

    try {
        let path = `/businesses/${state.currentBusiness.slug}/content`;
        if (filter) path += `?content_type=${encodeURIComponent(filter)}`;
        const items = await api(path);

        if (items.length === 0) {
            els.contentList.innerHTML = '<div class="empty">No content has landed yet. The next completed cycle will show up here.</div>';
            return;
        }

        els.contentList.innerHTML = items.map((item) => `
            <div class="card">
                <div class="card-topline">
                    <span class="badge badge-${esc(item.content_type)}">${esc(item.content_type)}</span>
                    <span class="badge badge-${esc(item.status)}">${esc(item.status)}</span>
                </div>
                ${item.title ? `<div class="action" style="margin-top:0.6rem">${esc(item.title)}</div>` : ""}
                <div class="detail">${truncate(item.body, 220)}</div>
                <div class="card-actions">
                    <div class="time">${timeAgo(item.created_at)}</div>
                    ${item.status === "draft" ? `<button class="btn-secondary btn-small publish-btn" data-content-id="${item.id}">Publish</button>` : ""}
                </div>
            </div>
        `).join("");
        bindContentActions();
    } catch (error) {
        if (!silent) {
            els.contentList.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
        }
    }
}

function bindContentActions() {
    els.contentList.querySelectorAll(".publish-btn").forEach((button) => {
        button.addEventListener("click", async () => {
            await publishContent(button.dataset.contentId, button);
        });
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
        await Promise.all([loadContent(state.currentContentFilter, true), loadMetrics()]);
        showToast("Content published.");
    } catch (error) {
        showToast(error.message, "error");
        button.disabled = false;
        button.textContent = previous;
    }
}

async function loadMetrics() {
    if (!state.currentBusiness) return;
    els.metricsGrid.innerHTML = '<div class="empty" style="grid-column:1/-1">Loading metrics...</div>';

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
            els.metricsGrid.innerHTML = '<div class="empty" style="grid-column:1/-1">No metrics recorded yet.</div>';
            els.metricsChartWrap.classList.add("hidden");
            return;
        }

        els.metricsGrid.innerHTML = entries.map((metric) => `
            <div class="metric-card">
                <div class="label">${esc(metric.name)}</div>
                <div class="value">${formatNumber(metric.value)}</div>
            </div>
        `).join("");

        await ensureChartsLoaded();
        renderMetricsChart(entries);
    } catch (error) {
        els.metricsGrid.innerHTML = `<div class="empty" style="grid-column:1/-1">${esc(error.message)}</div>`;
        els.metricsChartWrap.classList.add("hidden");
    }
}

function renderMetricsChart(entries) {
    const chartContext = document.getElementById("metrics-chart").getContext("2d");
    const labels = entries.map((entry) => entry.name);
    const data = entries.map((entry) => entry.value);

    if (state.metricsChart) {
        state.metricsChart.destroy();
        state.metricsChart = null;
    }

    els.metricsChartWrap.classList.remove("hidden");
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

async function loadSettings() {
    if (!state.currentBusiness) return;
    els.settingsContainer.innerHTML = '<div class="empty">Loading business settings...</div>';

    try {
        const settings = await api(`/businesses/${state.currentBusiness.slug}/settings`);
        const operatingPlan = settings.operating_plan || {};
        const creditModel = operatingPlan.credit_model || null;
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
                <span>${esc(item.detail)} · includes ${item.credits_required} dedicated night${item.credits_required === 1 ? "" : "s"} · ${esc(prettyArea(item.status || "locked"))}</span>
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
            <div class="setting-row"><span class="setting-label">Credits</span><span class="setting-value">${settings.credits_remaining}</span></div>
            <div class="setting-row"><span class="setting-label">Template</span><span class="setting-value">${esc(formatTemplate(settings.template))}</span></div>
            ${operatingPlan.program_type ? `<div class="setting-row"><span class="setting-label">Program</span><span class="setting-value">${esc(operatingPlan.program_type === "existing_business" ? "Existing Business" : "New Venture")}</span></div>` : ""}
            ${creditModel ? `<div class="setting-row"><span class="setting-label">Credit model</span><span class="setting-value">${esc(creditModel.definition)}</span></div>` : ""}
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
                    <h3>${esc(plan.name)} ${planKey === "starter" ? '<span class="founder-badge">3-Day Trial</span>' : planKey === "pro" ? '<span class="founder-badge">Recommended</span>' : ""}</h3>
                    <div class="plan-price">${plan.price.replace("/mo", "")}<span>/mo</span></div>
                    <p class="plan-note">${plan.credits} credits for up to ${plan.companies} compan${plan.companies === 1 ? "y" : "ies"}${plan.trialDays ? ` with a ${plan.trialDays}-day trial, card captured up front, and automatic billing after the trial` : ""}.</p>
                    <ul>
                        <li>${plan.credits} autonomous cycles</li>
                        <li>Manage up to ${plan.companies} compan${plan.companies === 1 ? "y" : "ies"}</li>
                        <li>Revenue share: 5% and managed ad spend take: 7.5%</li>
                    </ul>
                    <button class="btn-primary" data-upgrade-plan="${planKey}">
                        ${planKey === "starter" ? "Start Starter Trial" : `Upgrade to ${esc(plan.name)}`}
                    </button>
                </div>
            `)
            .join("");

        const creditPackCards = (billing.credit_packs || []).map((pack) => `
            <div class="plan-card">
                <h3>${esc(pack.name)}</h3>
                <div class="plan-price">${formatMoneyCents(pack.price_cents).replace(".00", "")}<span> one-time</span></div>
                <p class="plan-note">${pack.credits} extra credits. Designed to cost more than stepping up to the next plan.</p>
                <ul>
                    <li>${pack.credits} extra credits</li>
                    <li>Best for temporary bursts</li>
                    <li>Upgrade tiers first if you expect ongoing usage</li>
                </ul>
                <button class="btn-secondary" data-credit-pack="${esc(pack.key)}">Buy ${esc(pack.name)}</button>
            </div>
        `).join("");
        const addOnCards = availableAddOns.map((item) => `
            <div class="plan-card">
                <h3>${esc(item.title)} <span class="founder-badge">Queue Cut</span></h3>
                <div class="plan-price">${formatMoneyCents(addOnCatalog.get(item.key)?.price_cents || 0).replace(".00", "")}<span> one-time</span></div>
                <p class="plan-note">${esc(item.detail)}</p>
                <ul>
                    <li>Cuts ahead of the normal queue</li>
                    <li>Includes ${addOnCatalog.get(item.key)?.included_cycles || item.credits_required} dedicated night${(addOnCatalog.get(item.key)?.included_cycles || item.credits_required) === 1 ? "" : "s"} of execution</li>
                    <li>Best when this matters more than the next default step</li>
                </ul>
                <button class="btn-secondary" data-buy-add-on="${esc(item.key)}">Buy ${esc(item.title)}</button>
            </div>
        `).join("");

        els.billingContainer.innerHTML = `
            <div class="setting-row"><span class="setting-label">Current plan</span><span class="setting-value">${esc(currentPlan.name)} - ${esc(currentPlan.price)}</span></div>
            <div class="setting-row"><span class="setting-label">Available credits</span><span class="setting-value">${billing.credits_remaining}</span></div>
            <div class="setting-row"><span class="setting-label">Credits included</span><span class="setting-value">${billing.credits_included}</span></div>
            <div class="setting-row"><span class="setting-label">Company slots</span><span class="setting-value">${billing.company_count} of ${billing.company_limit} used</span></div>
            <div class="setting-row"><span class="setting-label">Credit value</span><span class="setting-value">${formatCreditValue(billing.effective_credit_value_cents)}</span></div>
            <div class="setting-row"><span class="setting-label">Subscription status</span><span class="setting-value"><span class="status-dot ${billing.active ? "active" : "inactive"}"></span>${billing.active ? "Active" : "Inactive"}</span></div>
            <div class="setting-row"><span class="setting-label">Revenue share</span><span class="setting-value">${billing.revenue_share_percent}%</span></div>
            <div class="setting-row"><span class="setting-label">Managed ad spend take</span><span class="setting-value">${billing.ad_spend_take_percent}%</span></div>
            <div class="setting-row"><span class="setting-label">Stripe fee recovery</span><span class="setting-value">${billing.stripe_fee_percent}% + ${formatMoneyCents(billing.stripe_fee_fixed_cents)}</span></div>
            ${billing.trial_days ? `<div class="setting-row"><span class="setting-label">Trial window</span><span class="setting-value">${billing.trial_days} days</span></div>` : ""}
            ${billing.can_start_paid_trial ? `<div class="setting-row"><span class="setting-label">Upgrade path</span><span class="setting-value">Enter a card to start the 3-day Starter trial, then auto-roll into subscription billing.</span></div>` : ""}
            ${addOnCards ? `<div class="settings-section"><span class="summary-label">Available Add-Ons</span><div class="plan-grid">${addOnCards}</div></div>` : ""}
            ${upgradeCards ? `<div class="plan-grid">${upgradeCards}</div>` : ""}
            ${creditPackCards ? `<div class="plan-grid">${creditPackCards}</div>` : ""}
        `;

        els.billingContainer.querySelectorAll("[data-upgrade-plan]").forEach((button) => {
            button.addEventListener("click", () => upgradePlan(button.dataset.upgradePlan));
        });
        els.billingContainer.querySelectorAll("[data-credit-pack]").forEach((button) => {
            button.addEventListener("click", () => buyCreditPack(button.dataset.creditPack));
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

async function buyCreditPack(creditPack) {
    try {
        const result = await api(`/businesses/${state.currentBusiness.slug}/billing/checkout`, {
            method: "POST",
            body: JSON.stringify({ credit_pack: creditPack }),
        });
        if (result.checkout_url) {
            window.open(result.checkout_url, "_blank", "noopener");
            showToast("Credit pack checkout opened in a new tab.");
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

function formatCreditValue(cents) {
    if (!cents) return "Free preview";
    return `${formatMoneyCents(cents)} per credit`;
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

document.querySelector(".low-credit-link").addEventListener("click", (event) => {
    event.preventDefault();
    showPage("billing");
});

document.getElementById("run-task-btn").addEventListener("click", () => openTaskModal());
document.getElementById("task-cancel").addEventListener("click", closeTaskModal);

els.taskModal.addEventListener("click", (event) => {
    if (event.target === els.taskModal) closeTaskModal();
});

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !els.taskModal.classList.contains("hidden")) {
        closeTaskModal();
    }
});

els.taskSubmit.addEventListener("click", async () => {
    const description = els.taskInput.value.trim();
    if (!description || !state.currentBusiness) return;

    els.taskSubmit.disabled = true;
    els.taskSubmit.textContent = "Running...";

    try {
        await api(`/businesses/${state.currentBusiness.slug}/cycles`, {
            method: "POST",
            body: JSON.stringify({ task_description: description }),
        });
        state.currentBusiness.credits_remaining = Math.max(0, state.currentBusiness.credits_remaining - 1);
        updateCreditGauge();
        closeTaskModal();
        await Promise.all([refreshBusinessSummary(true), loadFeed(true)]);
        showToast("Task queued. Watch the feed for progress.");
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        els.taskSubmit.disabled = false;
        els.taskSubmit.textContent = "Run";
    }
});

setCreateMode("idea");

const query = readQueryParams();
const authError = query.get("auth_error");
if (authError) {
    showToast(authError === "no_email" ? "OAuth account did not provide an email address." : "OAuth sign-in failed.", "error");
    clearQueryParams();
}

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
