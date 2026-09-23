// Shared utilities

// Page loader
function showLoader() {
    const el = document.getElementById('page-loader');
    if (el) el.classList.add('active');
}
function hideLoader() {
    const el = document.getElementById('page-loader');
    if (el) el.classList.remove('active');
}

let _apiCount = 0;
async function api(url, options = {}) {
    _apiCount++;
    if (_apiCount === 1) showLoader();

    const defaults = { headers: {} };
    if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
        defaults.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(options.body);
    }
    try {
        const resp = await fetch(url, { ...defaults, ...options });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: 'Request failed' }));
            throw new Error(err.error || `HTTP ${resp.status}`);
        }
        return resp.json();
    } finally {
        _apiCount--;
        if (_apiCount === 0) hideLoader();
    }
}

// Toast icons (SVG inline for independence from Lucide load timing)
const TOAST_ICONS = {
    success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    error:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    info:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    warning: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>'
};

function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');

    // Max 3 toasts — remove oldest
    while (container.children.length >= 3) {
        container.removeChild(container.firstChild);
    }

    const el = document.createElement('div');
    el.className = `toast toast-${type}`;

    const icon = TOAST_ICONS[type] || TOAST_ICONS.info;
    el.innerHTML = `${icon}<span>${escapeHtml(message)}</span><button class="toast-close" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(el);

    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transform = 'translateX(30px)';
        el.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        setTimeout(() => el.remove(), 300);
    }, 4000);
}

// Alias for chats page compatibility
function showToast(message, type) { toast(message, type); }

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

// Re-initialize Lucide icons after dynamic content updates
function refreshIcons() {
    if (window.lucide) lucide.createIcons();
}
