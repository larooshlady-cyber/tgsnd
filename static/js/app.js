// Shared utilities

async function api(url, options = {}) {
    const defaults = { headers: {} };
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        defaults.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }
    const resp = await fetch(url, { ...defaults, ...options });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({ error: 'Request failed' }));
        throw new Error(err.error || `HTTP ${resp.status}`);
    }
    return resp.json();
}

function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = message;
    container.appendChild(el);
    setTimeout(() => el.remove(), 4000);
}

function statusBadge(status) {
    const map = {
        'on_telegram': ['On TG', 'success'],
        'not_found': ['Not Found', 'danger'],
        'unknown': ['Unknown', 'muted'],
        'error': ['Error', 'danger'],
        'sent': ['Sent', 'success'],
        'pending': ['Pending', 'muted'],
        'failed': ['Failed', 'danger'],
        'peer_flood': ['Flood', 'warning'],
        'not_on_telegram': ['Not on TG', 'danger'],
        'added': ['Added', 'success'],
        'imported': ['Imported', 'success'],
        'cancelled': ['Cancelled', 'warning'],
        'flood_wait': ['Flood Wait', 'warning'],
        'connected': ['Connected', 'success'],
        'disconnected': ['Offline', 'danger'],
        'complete': ['Complete', 'success']
    };
    const [label, cls] = map[status] || [status, 'muted'];
    return `<span class="badge badge-${cls}">${label}</span>`;
}

function renderPagination(containerId, page, pages, onPageChange) {
    const el = document.getElementById(containerId);
    if (!el || pages <= 1) { if (el) el.innerHTML = ''; return; }
    el.innerHTML = `
        <button ${page <= 1 ? 'disabled' : ''} onclick="(${onPageChange.name})(${page - 1})">Prev</button>
        <span class="page-info">Page ${page} of ${pages}</span>
        <button ${page >= pages ? 'disabled' : ''} onclick="(${onPageChange.name})(${page + 1})">Next</button>
    `;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text || '';
    return div.innerHTML;
}
