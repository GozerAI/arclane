const feed = document.getElementById("feed");

async function loadStats() {
    try {
        const response = await fetch("/api/live/stats");
        const data = await response.json();
        document.getElementById("stat-businesses").textContent = data.businesses;
        document.getElementById("stat-cycles").textContent = data.cycles_completed;
        document.getElementById("stat-content").textContent = data.content_produced;
        document.getElementById("stat-actions").textContent = data.total_actions;
    } catch (_) {
        // Stats are optional.
    }
}

async function loadFeed() {
    try {
        const response = await fetch("/api/live?limit=30");
        const items = await response.json();
        if (!items.length) {
            feed.innerHTML = '<div class="empty">Waiting for the next visible action...</div>';
            return;
        }
        feed.innerHTML = items.map(renderEntry).join("");
    } catch (_) {
        feed.innerHTML = '<div class="empty">Connecting to live activity...</div>';
    }
}

function renderEntry(item) {
    const detail = item.detail ? `<div class="detail">${esc(item.detail).slice(0, 220)}</div>` : "";
    return `
        <article class="entry">
            <div class="biz">${esc(item.business_name)}</div>
            <div class="action">${esc(item.action)}</div>
            ${detail}
            <div class="time">${timeAgo(item.created_at)}</div>
        </article>
    `;
}

function connectStream() {
    const source = new EventSource("/api/live/stream");
    source.addEventListener("activity", (event) => {
        const item = JSON.parse(event.data);
        const wrapper = document.createElement("div");
        wrapper.innerHTML = renderEntry(item);
        if (feed.querySelector(".empty")) {
            feed.innerHTML = "";
        }
        feed.prepend(wrapper.firstElementChild);
        while (feed.children.length > 60) {
            feed.removeChild(feed.lastChild);
        }
        loadStats();
    });
    source.onerror = () => {
        source.close();
        window.setTimeout(connectStream, 5000);
    };
}

function esc(value) {
    const div = document.createElement("div");
    div.textContent = value || "";
    return div.innerHTML;
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

loadStats();
loadFeed();
connectStream();
window.setInterval(loadStats, 30000);
