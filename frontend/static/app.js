const API = '/api';
let currentBusiness = null;
let authToken = sessionStorage.getItem('arclane_token');
let userEmail = sessionStorage.getItem('arclane_email');

// Auto-show create view if coming from landing page CTA
if (window.location.hash === '#create') {
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('login-view').classList.add('hidden');
        document.getElementById('create-view').classList.remove('hidden');
    });
}

// --- View switching ---
function showView(id) {
    document.querySelectorAll('.view').forEach(v => v.classList.add('hidden'));
    document.getElementById(id).classList.remove('hidden');
}

function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
    document.getElementById(`page-${name}`).classList.remove('hidden');
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    const activeLink = document.querySelector(`.nav-link[data-page="${name}"]`);
    if (activeLink) activeLink.classList.add('active');

    if (name === 'feed') loadFeed();
    if (name === 'content') loadContent();
    if (name === 'metrics') loadMetrics();
    if (name === 'billing') loadBilling();
    if (name === 'settings') loadSettings();
}

// --- API helpers ---
async function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json', ...opts.headers };
    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }
    const res = await fetch(`${API}${path}`, { headers, ...opts });
    if (res.status === 401) {
        logout();
        throw new Error('Session expired. Please sign in again.');
    }
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Request failed');
    }
    return res.json();
}

function logout() {
    authToken = null;
    userEmail = null;
    currentBusiness = null;
    sessionStorage.removeItem('arclane_token');
    sessionStorage.removeItem('arclane_email');
    showView('login-view');
}

// --- Login ---
document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const btn = e.target.querySelector('button');
    btn.disabled = true;
    try {
        const result = await api('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ email, password }),
        });
        authToken = result.access_token;
        userEmail = result.email;
        sessionStorage.setItem('arclane_token', authToken);
        sessionStorage.setItem('arclane_email', userEmail);

        const businesses = await api(`/businesses?owner_email=${encodeURIComponent(email)}`);
        if (businesses.length > 0) {
            enterDashboard(businesses[0]);
        } else {
            showView('create-view');
        }
    } catch (err) {
        alert(err.message);
    } finally {
        btn.disabled = false;
    }
});

document.getElementById('show-create').addEventListener('click', (e) => {
    e.preventDefault();
    showView('create-view');
});

document.getElementById('show-login').addEventListener('click', (e) => {
    e.preventDefault();
    showView('login-view');
});

// --- Forgot Password ---
document.getElementById('show-forgot').addEventListener('click', (e) => {
    e.preventDefault();
    document.getElementById('forgot-form').reset();
    document.getElementById('forgot-success').classList.add('hidden');
    showView('forgot-view');
});

document.getElementById('show-login-from-forgot').addEventListener('click', (e) => {
    e.preventDefault();
    showView('login-view');
});

document.getElementById('forgot-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const email = document.getElementById('forgot-email').value;
    const btn = e.target.querySelector('button');
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
        await api('/auth/forgot-password', {
            method: 'POST',
            body: JSON.stringify({ email }),
        });
    } catch (_) {
        // Swallow errors — always show success to avoid email enumeration
    } finally {
        btn.disabled = false;
        btn.textContent = 'Send Reset Link';
        e.target.reset();
        document.getElementById('forgot-success').classList.remove('hidden');
    }
});

// --- Create Business ---
document.getElementById('create-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = e.target.querySelector('button');
    const email = document.getElementById('biz-email').value;
    btn.disabled = true;
    btn.textContent = 'Launching...';
    try {
        // Login first if no token (auto-creates account for new users)
        if (!authToken) {
            const result = await api('/auth/login', {
                method: 'POST',
                body: JSON.stringify({ email, password: '' }),
            });
            authToken = result.access_token;
            userEmail = result.email;
            sessionStorage.setItem('arclane_token', authToken);
            sessionStorage.setItem('arclane_email', userEmail);
        }

        const biz = await api('/businesses', {
            method: 'POST',
            body: JSON.stringify({
                name: document.getElementById('biz-name').value,
                owner_email: email,
                description: document.getElementById('biz-description').value,
                template: document.getElementById('biz-template').value,
            }),
        });
        enterDashboard(biz);
    } catch (err) {
        alert(err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Launch Business';
    }
});

// --- Credit gauge ---
function updateCreditGauge() {
    const credits = currentBusiness ? currentBusiness.credits_remaining : 0;
    const gauge = document.getElementById('credit-gauge');
    const dot = document.getElementById('credit-dot');
    const count = document.getElementById('credit-count');

    gauge.classList.remove('hidden');
    count.textContent = `${credits} credit${credits === 1 ? '' : 's'}`;

    dot.className = 'credit-dot';
    if (credits > 5) {
        dot.classList.add('green');
    } else if (credits >= 2) {
        dot.classList.add('yellow');
    } else {
        dot.classList.add('red');
    }

    const banner = document.getElementById('low-credit-banner');
    if (credits <= 2) {
        banner.classList.remove('hidden');
    } else {
        banner.classList.add('hidden');
    }
}

// --- Dashboard ---
function enterDashboard(business) {
    currentBusiness = business;
    document.getElementById('biz-slug').textContent = business.subdomain;
    updateCreditGauge();
    showView('dashboard-view');
    showPage('feed');
}

// Nav
document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
        e.preventDefault();
        if (link.dataset.page) showPage(link.dataset.page);
    });
});

// Low-credit banner upgrade link
document.querySelector('.low-credit-link').addEventListener('click', (e) => {
    e.preventDefault();
    showPage('billing');
});

// --- Feed ---
async function loadFeed() {
    const list = document.getElementById('feed-list');
    try {
        const items = await api(`/businesses/${currentBusiness.slug}/feed`);
        if (items.length === 0) {
            list.innerHTML = '<div class="empty">No activity yet. Run a task to get started.</div>';
            return;
        }
        list.innerHTML = items.map(item => `
            <div class="card">
                <div class="action">${esc(item.action)}</div>
                ${item.detail ? `<div class="detail">${esc(item.detail).slice(0, 300)}</div>` : ''}
                <div class="time">${timeAgo(item.created_at)}</div>
            </div>
        `).join('');
    } catch (err) {
        list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
}

// --- Run Task Modal ---
document.getElementById('run-task-btn').addEventListener('click', () => {
    document.getElementById('task-modal').classList.remove('hidden');
    document.getElementById('credits-display').textContent =
        `${currentBusiness.credits_remaining} credits remaining`;
});
document.getElementById('task-cancel').addEventListener('click', () => {
    document.getElementById('task-modal').classList.add('hidden');
});
document.getElementById('task-submit').addEventListener('click', async () => {
    const desc = document.getElementById('task-input').value.trim();
    if (!desc) return;
    const btn = document.getElementById('task-submit');
    btn.disabled = true;
    try {
        await api(`/businesses/${currentBusiness.slug}/cycles`, {
            method: 'POST',
            body: JSON.stringify({ task_description: desc }),
        });
        document.getElementById('task-modal').classList.add('hidden');
        document.getElementById('task-input').value = '';
        currentBusiness.credits_remaining--;
        updateCreditGauge();
        setTimeout(loadFeed, 1000);
    } catch (err) {
        alert(err.message);
    } finally {
        btn.disabled = false;
    }
});

// --- Content ---
async function loadContent(filter = '') {
    const list = document.getElementById('content-list');
    try {
        let url = `/businesses/${currentBusiness.slug}/content`;
        if (filter) url += `?content_type=${filter}`;
        const items = await api(url);
        if (items.length === 0) {
            list.innerHTML = '<div class="empty">No content produced yet. Your AI will create content during its next cycle.</div>';
            return;
        }
        list.innerHTML = items.map(item => `
            <div class="card">
                <div>
                    <span class="badge badge-${esc(item.content_type)}">${esc(item.content_type)}</span>
                    <span class="badge badge-${esc(item.status)}">${esc(item.status)}</span>
                </div>
                ${item.title ? `<div class="action" style="margin-top:0.5rem">${esc(item.title)}</div>` : ''}
                <div class="detail">${esc(item.body).slice(0, 200)}${item.body.length > 200 ? '...' : ''}</div>
                <div class="time">${timeAgo(item.created_at)}</div>
            </div>
        `).join('');
    } catch (err) {
        list.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
}

document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        loadContent(btn.dataset.filter);
    });
});

// --- Metrics ---
let metricsChart = null;

async function loadMetrics() {
    const grid = document.getElementById('metrics-grid');
    const chartWrap = document.getElementById('metrics-chart-wrap');
    try {
        const items = await api(`/businesses/${currentBusiness.slug}/metrics?limit=20`);
        const latest = {};
        for (const m of items) {
            if (!latest[m.name] || m.recorded_at > latest[m.name].recorded_at) {
                latest[m.name] = m;
            }
        }
        const entries = Object.values(latest);
        if (entries.length === 0) {
            grid.innerHTML = '<div class="empty" style="grid-column:1/-1">No metrics recorded yet.</div>';
            chartWrap.classList.add('hidden');
            return;
        }

        // Metric cards
        grid.innerHTML = entries.map(m => `
            <div class="metric-card">
                <div class="label">${esc(m.name)}</div>
                <div class="value">${formatNumber(m.value)}</div>
            </div>
        `).join('');

        // Bar chart
        chartWrap.classList.remove('hidden');
        const labels = entries.map(m => m.name);
        const data = entries.map(m => m.value);

        if (metricsChart) {
            metricsChart.destroy();
            metricsChart = null;
        }

        const ctx = document.getElementById('metrics-chart').getContext('2d');
        metricsChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: 'Latest value',
                    data,
                    backgroundColor: 'rgba(74, 108, 247, 0.6)',
                    borderColor: 'rgba(74, 108, 247, 1)',
                    borderWidth: 1,
                    borderRadius: 4,
                }],
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: (ctx) => formatNumber(ctx.raw),
                        },
                    },
                },
                scales: {
                    x: {
                        ticks: { color: '#888894' },
                        grid: { color: '#222230' },
                    },
                    y: {
                        ticks: {
                            color: '#888894',
                            callback: (v) => formatNumber(v),
                        },
                        grid: { color: '#222230' },
                        beginAtZero: true,
                    },
                },
            },
        });
    } catch (err) {
        grid.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
        chartWrap.classList.add('hidden');
    }
}

// --- Settings ---
async function loadSettings() {
    const container = document.getElementById('settings-form-container');
    try {
        const s = await api(`/businesses/${currentBusiness.slug}/settings`);
        container.innerHTML = `
            <div class="setting-row"><span class="setting-label">Name</span><span class="setting-value">${esc(s.name)}</span></div>
            <div class="setting-row"><span class="setting-label">Subdomain</span><span class="setting-value">${esc(s.subdomain)}</span></div>
            <div class="setting-row"><span class="setting-label">Plan</span><span class="setting-value">${esc(s.plan)}</span></div>
            <div class="setting-row"><span class="setting-label">Credits</span><span class="setting-value">${s.credits_remaining}</span></div>
            <div class="setting-row"><span class="setting-label">Template</span><span class="setting-value">${esc(s.template || 'None')}</span></div>
            <div class="setting-row">
                <span class="setting-label">Subdomain</span>
                <span class="setting-value"><span class="status-dot ${s.subdomain_provisioned ? 'active' : 'inactive'}"></span>${s.subdomain_provisioned ? 'Active' : 'Pending'}</span>
            </div>
            <div class="setting-row">
                <span class="setting-label">Email</span>
                <span class="setting-value"><span class="status-dot ${s.email_provisioned ? 'active' : 'inactive'}"></span>${s.email_provisioned ? 'Active' : 'Pending'}</span>
            </div>
            <div class="setting-row">
                <span class="setting-label">App</span>
                <span class="setting-value"><span class="status-dot ${s.app_deployed ? 'active' : 'inactive'}"></span>${s.app_deployed ? 'Deployed' : 'Pending'}</span>
            </div>
        `;
    } catch (err) {
        container.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
}

// --- Billing ---
const PLANS = {
    starter: { name: 'Starter', price: '$49/mo', credits: 5 },
    pro: { name: 'Pro', price: '$99/mo', credits: 20 },
    enterprise: { name: 'Enterprise', price: '$199/mo', credits: 100 },
};

async function loadBilling() {
    const container = document.getElementById('billing-container');
    try {
        const s = await api(`/businesses/${currentBusiness.slug}/billing/status`);
        const plan = PLANS[s.plan] || PLANS.starter;
        container.innerHTML = `
            <div class="setting-row"><span class="setting-label">Plan</span><span class="setting-value">${esc(plan.name)} — ${plan.price}</span></div>
            <div class="setting-row"><span class="setting-label">Credits</span><span class="setting-value">${s.credits_remaining} remaining</span></div>
            <div class="setting-row"><span class="setting-label">Status</span><span class="setting-value"><span class="status-dot ${s.active ? 'active' : 'inactive'}"></span>${s.active ? 'Active' : 'Inactive'}</span></div>
            ${s.plan === 'starter' ? `
            <div class="plan-grid">
                <div class="plan-card">
                    <h3>Pro</h3>
                    <div class="plan-price">$99<span>/mo</span></div>
                    <ul><li>20 cycles/month</li><li>Priority execution</li><li>All content types</li></ul>
                    <button class="btn-primary" onclick="upgradePlan('pro')">Upgrade</button>
                </div>
                <div class="plan-card">
                    <h3>Enterprise</h3>
                    <div class="plan-price">$199<span>/mo</span></div>
                    <ul><li>100 cycles/month</li><li>Custom templates</li><li>API access</li></ul>
                    <button class="btn-primary" onclick="upgradePlan('enterprise')">Upgrade</button>
                </div>
            </div>` : ''}
        `;
    } catch (err) {
        container.innerHTML = `<div class="empty">${esc(err.message)}</div>`;
    }
}

async function upgradePlan(plan) {
    try {
        const result = await api(`/businesses/${currentBusiness.slug}/billing/checkout`, {
            method: 'POST',
            body: JSON.stringify({ plan }),
        });
        if (result.checkout_url) {
            window.open(result.checkout_url, '_blank');
        } else {
            alert('Checkout not available yet — billing integration pending.');
        }
    } catch (err) {
        alert(err.message);
    }
}

// --- Utilities ---
function esc(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
}

function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}

function formatNumber(n) {
    if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
    if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
    return n.toFixed(n % 1 === 0 ? 0 : 1);
}

// Auto-login if token exists
if (authToken) {
    api('/auth/validate').then(() => {
        api(`/businesses?owner_email=${encodeURIComponent(userEmail)}`).then(businesses => {
            if (businesses.length > 0) enterDashboard(businesses[0]);
        }).catch(() => {});
    }).catch(() => logout());
}
