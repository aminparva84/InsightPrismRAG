/* PrismRAG — Dashboard JavaScript */

const API = '';  // same-origin; set to 'https://api.prismrag.io' for prod

/* ── Auth helpers ─────────────────────────────────────────────────────────── */
function getToken() { return localStorage.getItem('prismrag_token'); }
function getUser()  {
  try { return JSON.parse(localStorage.getItem('prismrag_user') || 'null'); }
  catch { return null; }
}

async function apiFetch(path, opts = {}) {
  const token = getToken();
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) { signOut(); return null; }
  return res;
}

function signOut() {
  localStorage.removeItem('prismrag_token');
  localStorage.removeItem('prismrag_user');
  window.location.href = '/login.html';
}

/* ── Routing ─────────────────────────────────────────────────────────────── */
const sections = {};
document.querySelectorAll('.dash-section').forEach(s => { sections[s.id.replace('sec-', '')] = s; });

function showSection(name) {
  Object.values(sections).forEach(s => s.classList.remove('active'));
  const sec = sections[name];
  if (sec) sec.classList.add('active');

  document.querySelectorAll('.nav-item').forEach(a => {
    a.classList.toggle('active', a.dataset.section === name);
  });

  // Lazy-load data on first visit
  if (name === 'workspaces' && !document.querySelector('#tenants-list table')) loadTenants();
  if (name === 'jobs'       && !document.querySelector('#jobs-list table'))    loadJobs();
  if (name === 'apikeys'    && !document.querySelector('#keys-list table'))    loadKeys();
  if (name === 'billing')   loadBillingPlans();
  if (name === 'security')  loadMfaStatus();
  if (name === 'enterprise') loadEnterprise();
}

document.querySelectorAll('.nav-item').forEach(a => {
  a.addEventListener('click', e => { e.preventDefault(); showSection(a.dataset.section); });
});

document.querySelectorAll('[data-goto]').forEach(a => {
  a.addEventListener('click', e => { e.preventDefault(); showSection(a.dataset.goto); });
});

/* ── Bootstrap ───────────────────────────────────────────────────────────── */
(async function init() {
  if (!getToken()) { window.location.href = '/login.html'; return; }

  const res = await apiFetch('/api/auth/me');
  if (!res || !res.ok) { signOut(); return; }
  const me = await res.json();

  // Persist latest user data
  localStorage.setItem('prismrag_user', JSON.stringify({ ...getUser(), ...me }));

  // Sidebar user chip
  const initials = (me.full_name || me.email || '?').split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase();
  document.getElementById('user-avatar').textContent = initials;
  document.getElementById('user-name').textContent = me.full_name || me.email;
  document.getElementById('user-plan-badge').textContent = me.plan || 'free';

  // Overview stats
  document.getElementById('ov-plan').textContent = (me.plan || 'free').charAt(0).toUpperCase() + (me.plan || 'free').slice(1);

  // Load usage
  loadUsage(me.plan);
  updateQuickstart();

  if (me.plan === 'enterprise') {
    document.querySelectorAll('.nav-enterprise').forEach(el => { el.style.display = ''; });
  }
})();

document.getElementById('signout-btn').addEventListener('click', e => { e.preventDefault(); signOut(); });

/* ── Usage / quota ────────────────────────────────────────────────────────── */
async function loadUsage(plan) {
  const res = await apiFetch('/api/auth/usage');
  if (!res || !res.ok) return;
  const data = await res.json();

  const chunksUsed = data.chunks_used ?? 0;
  const chunksMax  = data.chunks_limit ?? planChunkLimit(plan);
  const searches   = data.searches_used ?? 0;
  const tenants    = data.tenants_count ?? 0;
  const pct        = chunksMax > 0 ? Math.min(100, (chunksUsed / chunksMax) * 100) : 0;

  document.getElementById('ov-chunks').textContent = chunksUsed.toLocaleString();
  document.getElementById('ov-quota').textContent  = `/ ${chunksMax === Infinity ? '∞' : chunksMax.toLocaleString()} chunks`;
  document.getElementById('quota-fill').style.width = pct + '%';
  document.getElementById('ov-tenants').textContent  = tenants;
  document.getElementById('ov-searches').textContent = searches.toLocaleString();
}

function planChunkLimit(plan) {
  return { free: 5000, starter: 200000, professional: 2000000, enterprise: Infinity }[plan] ?? 5000;
}

/* ── Quickstart check ─────────────────────────────────────────────────────── */
async function updateQuickstart() {
  // Check if has API keys
  const res = await apiFetch('/api/auth/api-keys');
  if (res && res.ok) {
    const keys = await res.json();
    if (keys.length > 0) markQS('qs-apikey');
  }
}

function markQS(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('done');
  el.querySelector('.qs-check').textContent = '✓';
}

/* ── Workspaces (tenants) ─────────────────────────────────────────────────── */
async function loadTenants() {
  const list = document.getElementById('tenants-list');
  list.innerHTML = '<div class="loading-state">Loading…</div>';

  const res = await apiFetch('/api/prismrag/tenants');
  if (!res || !res.ok) {
    list.innerHTML = '<div class="empty-state"><strong>Could not load workspaces</strong></div>';
    return;
  }

  const tenants = await res.json();
  if (!tenants.length) {
    list.innerHTML = `
      <div class="empty-state">
        <strong>No workspaces yet</strong>
        <p>Create one to start embedding your knowledge graph.</p>
      </div>`;
    return;
  }

  list.innerHTML = `
    <table>
      <thead><tr><th>Name</th><th>Tenant ID</th><th>Role</th><th>Region</th><th>Created</th><th></th></tr></thead>
      <tbody>${tenants.map(t => `
        <tr>
          <td>${escHtml(t.name)}</td>
          <td><code style="font-size:0.78rem;font-family:monospace">${t.tenant_id}</code></td>
          <td><span class="badge badge-gray">${escHtml(t.role)}</span></td>
          <td>${escHtml(t.data_region || '—')}</td>
          <td>${t.created_at ? new Date(t.created_at).toLocaleDateString() : '—'}</td>
          <td><button class="btn-sm-ghost" style="font-size:0.78rem" onclick="copyText('${t.tenant_id}', this)">Copy ID</button></td>
        </tr>`).join('')}</tbody>
    </table>`;
}

document.getElementById('create-tenant-btn').addEventListener('click', () => {
  document.getElementById('tenant-modal').style.display = 'flex';
});
document.getElementById('tenant-modal-cancel').addEventListener('click', () => {
  document.getElementById('tenant-modal').style.display = 'none';
});
document.getElementById('tenant-modal-confirm').addEventListener('click', createTenant);

async function createTenant() {
  const name = document.getElementById('tenant-name-input').value.trim();
  if (!name) return;

  const btn = document.getElementById('tenant-modal-confirm');
  btn.textContent = 'Creating…'; btn.disabled = true;

  const res = await apiFetch('/api/prismrag/tenants', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });

  btn.textContent = 'Create'; btn.disabled = false;

  if (res && res.ok) {
    const t = await res.json();
    document.getElementById('tenant-modal').style.display = 'none';
    document.getElementById('tenant-name-input').value = '';
    renderTenantRow(t);
  } else {
    alert('Failed to create workspace. Check the console.');
  }
}

function renderTenantRow(t) {
  let table = document.querySelector('#tenants-list table');
  if (!table) {
    document.getElementById('tenants-list').innerHTML = `
      <table>
        <thead><tr><th>Name</th><th>Tenant ID</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody id="tenants-tbody"></tbody>
      </table>`;
    table = document.querySelector('#tenants-list table');
  }
  const tbody = document.getElementById('tenants-tbody');
  const row = document.createElement('tr');
  row.innerHTML = `
    <td>${escHtml(t.name)}</td>
    <td><code style="font-size:0.78rem;font-family:monospace">${t.tenant_id}</code></td>
    <td>${new Date(t.created_at).toLocaleDateString()}</td>
    <td>
      <button class="btn-sm-ghost" style="font-size:0.78rem" onclick="copyText('${t.tenant_id}', this)">Copy ID</button>
    </td>`;
  tbody.prepend(row);
}

/* ── Ingest Jobs ──────────────────────────────────────────────────────────── */
async function loadJobs() {
  const list = document.getElementById('jobs-list');
  list.innerHTML = '<div class="loading-state">Loading…</div>';

  const res = await apiFetch('/api/prismrag/jobs?limit=20');
  if (!res || !res.ok) {
    list.innerHTML = '<div class="empty-state"><strong>No jobs yet</strong><p>Submit your first ingest job above.</p></div>';
    return;
  }

  const jobs = await res.json();
  if (!jobs.length) {
    list.innerHTML = '<div class="empty-state"><strong>No jobs yet</strong><p>Submit your first ingest job above.</p></div>';
    return;
  }

  list.innerHTML = `
    <table>
      <thead><tr><th>Job ID</th><th>Tenant</th><th>Status</th><th>Progress</th><th>Strategy</th><th>Created</th></tr></thead>
      <tbody>${jobs.map(jobRow).join('')}</tbody>
    </table>`;
}

function jobRow(j) {
  const statusBadge = {
    pending:    '<span class="badge badge-gray">Pending</span>',
    queued:     '<span class="badge badge-gray">Queued</span>',
    running:    '<span class="badge badge-blue">Running</span>',
    completed:  '<span class="badge badge-green">Done</span>',
    done:       '<span class="badge badge-green">Done</span>',
    failed:     '<span class="badge badge-red">Failed</span>',
    stale:      '<span class="badge badge-red">Stale</span>',
  }[j.status] || `<span class="badge badge-gray">${j.status}</span>`;

  const pct = j.progress_pct ?? 0;
  return `<tr>
    <td><code style="font-size:0.78rem">${j.job_id.slice(0,8)}…</code></td>
    <td>${escHtml(j.tenant_id?.slice(0,8) ?? '—')}…</td>
    <td>${statusBadge}</td>
    <td>
      <div class="progress-cell">
        <div class="mini-bar"><div class="mini-fill" style="width:${pct}%"></div></div>
        <span style="font-size:0.78rem;color:var(--text-dim)">${pct}%</span>
      </div>
    </td>
    <td><span class="badge badge-gray">${j.strategy || '—'}</span></td>
    <td>${j.created_at ? new Date(j.created_at).toLocaleDateString() : '—'}</td>
  </tr>`;
}

document.getElementById('refresh-jobs-btn').addEventListener('click', loadJobs);

/* File drop */
const fileDrop  = document.getElementById('file-drop');
const fileInput = document.getElementById('file-input');
let selectedFile = null;

fileDrop.addEventListener('dragover',  e => { e.preventDefault(); fileDrop.classList.add('dragover'); });
fileDrop.addEventListener('dragleave', () => fileDrop.classList.remove('dragover'));
fileDrop.addEventListener('drop', e => {
  e.preventDefault(); fileDrop.classList.remove('dragover');
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});
fileInput.addEventListener('change', () => { if (fileInput.files[0]) setFile(fileInput.files[0]); });

function setFile(f) {
  selectedFile = f;
  document.getElementById('file-drop-label').textContent = `✓ ${f.name} (${(f.size / 1024).toFixed(1)} KB)`;
}

document.getElementById('job-form').addEventListener('submit', async e => {
  e.preventDefault();
  if (!selectedFile) { alert('Please select a CSV or Excel file.'); return; }

  const tenantId = document.getElementById('job-tenant').value.trim();
  const strategy = document.getElementById('job-strategy').value;
  if (!tenantId) { alert('Paste your tenant (workspace) ID.'); return; }

  const btn = document.getElementById('job-submit-btn');
  btn.textContent = 'Submitting…'; btn.disabled = true;

  const fd = new FormData();
  fd.append('file', selectedFile);
  fd.append('tenant_id', tenantId);
  fd.append('strategy', strategy);
  fd.append('source_type', 'file');

  const token = getToken();
  const res = await fetch(API + '/api/prismrag/jobs/upload', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}` },
    body: fd,
  });

  btn.textContent = 'Submit job'; btn.disabled = false;

  if (res && res.ok) {
    const job = await res.json();
    alert(`Job submitted! ID: ${job.job_id}\nCheck 'Recent jobs' for progress.`);
    selectedFile = null;
    document.getElementById('file-drop-label').innerHTML = `Drag & drop or <label for="file-input" class="file-link">browse file</label>`;
    markQS('qs-job');
    loadJobs();
  } else {
    const err = await res.json().catch(() => ({}));
    alert('Submission failed: ' + (err.detail || res.statusText));
  }
});

/* ── API Keys ─────────────────────────────────────────────────────────────── */
async function loadKeys() {
  const list = document.getElementById('keys-list');
  list.innerHTML = '<div class="loading-state">Loading…</div>';

  const res = await apiFetch('/api/auth/api-keys');
  if (!res || !res.ok) { list.innerHTML = '<div class="empty-state"><strong>No keys yet</strong></div>'; return; }
  const keys = await res.json();

  if (!keys.length) {
    list.innerHTML = '<div class="empty-state"><strong>No keys yet</strong><p>Generate one to start making API calls.</p></div>';
    return;
  }

  list.innerHTML = `
    <table>
      <thead><tr><th>Prefix</th><th>Name</th><th>Created</th><th>Last used</th><th></th></tr></thead>
      <tbody>${keys.map(keyRow).join('')}</tbody>
    </table>`;
}

function keyRow(k) {
  const id = k.id || k.key_id;
  const prefix = k.keyPrefix || k.key_prefix;
  const label = k.label || k.name || '—';
  const created = k.createdAt || k.created_at;
  const lastUsed = k.lastUsedAt || k.last_used_at;
  return `<tr>
    <td><code style="font-family:monospace;font-size:0.82rem">${escHtml(prefix)}…</code></td>
    <td>${escHtml(label)}</td>
    <td>${created ? new Date(created).toLocaleDateString() : '—'}</td>
    <td>${lastUsed ? new Date(lastUsed).toLocaleDateString() : 'Never'}</td>
    <td>
      <button class="btn-sm-ghost" style="font-size:0.78rem;color:#f87171;border-color:rgba(248,113,113,0.3)"
        onclick="revokeKey('${id}', this)">Revoke</button>
    </td>
  </tr>`;
}

document.getElementById('create-key-btn').addEventListener('click', createKey);

async function createKey() {
  const btn = document.getElementById('create-key-btn');
  btn.textContent = 'Generating…'; btn.disabled = true;

  const res = await apiFetch('/api/auth/api-keys', { method: 'POST', body: JSON.stringify({}) });
  btn.textContent = '+ Generate key'; btn.disabled = false;

  if (res && res.ok) {
    const data = await res.json();
    document.getElementById('new-key-value').textContent = data.raw_key;
    document.getElementById('new-key-reveal').style.display = 'flex';
    markQS('qs-apikey');
    loadKeys();
  } else {
    alert('Failed to create key.');
  }
}

document.getElementById('copy-key-btn').addEventListener('click', () => {
  const raw = document.getElementById('new-key-value').textContent;
  copyText(raw, document.getElementById('copy-key-btn'));
});

document.getElementById('key-done-btn').addEventListener('click', () => {
  document.getElementById('new-key-reveal').style.display = 'none';
});

async function revokeKey(keyId, btn) {
  if (!confirm('Revoke this key? Any apps using it will stop working.')) return;
  btn.textContent = '…'; btn.disabled = true;
  const res = await apiFetch(`/api/auth/api-keys/${keyId}`, { method: 'DELETE' });
  if (res && res.ok) loadKeys();
  else { btn.textContent = 'Revoke'; btn.disabled = false; }
}

/* ── Billing ──────────────────────────────────────────────────────────────── */
async function loadBillingPlans() {
  const container = document.getElementById('billing-plans');
  if (container.children.length > 0) return;  // already loaded

  const res = await apiFetch('/api/billing/plans');
  if (!res || !res.ok) return;
  const { plans, publishable_key } = await res.json();

  const user = getUser();
  const currentPlan = user?.plan || 'free';

  document.getElementById('billing-plan-badge').textContent = currentPlan;
  document.getElementById('billing-plan-name').textContent  = (currentPlan.charAt(0).toUpperCase() + currentPlan.slice(1)) + ' Plan';

  container.innerHTML = plans.map(p => planCard(p, currentPlan, publishable_key)).join('');
}

function planCard(p, currentPlan, pk) {
  const isCurrent = p.id === currentPlan;
  const btnLabel  = isCurrent ? 'Current plan' : (p.id === 'enterprise' ? 'Contact sales' : 'Upgrade');
  const btnClass  = isCurrent ? 'btn-sm-ghost' : 'btn-sm-primary';
  const onclick   = isCurrent ? '' : (p.id === 'enterprise' ? `onclick="window.location='/contact.html'"` : `onclick="checkout('${p.id}')"`);

  return `<div class="pricing-card${p.popular ? ' popular' : ''}${isCurrent ? ' current' : ''}">
    ${p.popular ? '<div class="popular-tag">Most popular</div>' : ''}
    <div class="plan-name">${escHtml(p.name)}</div>
    <div class="price-row">
      <span class="price-amount">${p.price_display}</span>
      ${p.id !== 'enterprise' ? '<span class="price-period">/month</span>' : ''}
    </div>
    <p class="plan-desc">${escHtml(p.description)}</p>
    <ul class="plan-features">${(p.features || []).map(f => `<li>${escHtml(f)}</li>`).join('')}</ul>
    <button class="${btnClass}" ${isCurrent ? 'disabled' : ''} ${onclick}>${btnLabel}</button>
  </div>`;
}

async function checkout(plan) {
  const res = await apiFetch('/api/billing/checkout', {
    method: 'POST',
    body: JSON.stringify({ plan }),
  });
  if (res && res.ok) {
    const d = await res.json();
    if (d.redirect) window.location.href = d.redirect;
  } else {
    alert('Could not start checkout. Please try again.');
  }
}

document.getElementById('manage-billing-btn').addEventListener('click', async () => {
  const btn = document.getElementById('manage-billing-btn');
  btn.textContent = 'Loading…'; btn.disabled = true;
  const res = await apiFetch('/api/billing/portal', { method: 'POST', body: JSON.stringify({}) });
  btn.textContent = 'Manage billing'; btn.disabled = false;
  if (res && res.ok) {
    const d = await res.json();
    if (d.redirect) window.location.href = d.redirect;
  }
});

/* ── Security / MFA ───────────────────────────────────────────────────────── */
let mfaEnrollSecret = null;

async function loadMfaStatus() {
  const res = await apiFetch('/api/v1/auth/mfa/status');
  if (!res || !res.ok) return;
  const st = await res.json();
  const badge = document.getElementById('mfa-status-badge');
  const hint = document.getElementById('mfa-policy-hint');

  if (st.org_mfa_required) {
    hint.textContent = 'Your organization requires MFA on all accounts.';
    hint.style.display = 'block';
  }

  if (st.mfa_enabled) {
    badge.textContent = 'Enabled';
    badge.className = 'badge badge-green';
    document.getElementById('mfa-disabled-panel').style.display = 'none';
    document.getElementById('mfa-enroll-panel').style.display = 'none';
    document.getElementById('mfa-enabled-panel').style.display = 'block';
  } else {
    badge.textContent = st.mfa_configured ? 'Pending confirm' : 'Disabled';
    badge.className = 'badge badge-gray';
    document.getElementById('mfa-disabled-panel').style.display = 'block';
    document.getElementById('mfa-enroll-panel').style.display = 'none';
    document.getElementById('mfa-enabled-panel').style.display = 'none';
  }
}

document.getElementById('mfa-start-btn')?.addEventListener('click', async () => {
  const res = await apiFetch('/api/v1/auth/mfa/enroll/start', { method: 'POST', body: '{}' });
  if (!res || !res.ok) { alert('Could not start MFA enrollment'); return; }
  const data = await res.json();
  mfaEnrollSecret = data.secret;
  const uri = data.otpauth_uri;
  document.getElementById('mfa-disabled-panel').style.display = 'none';
  document.getElementById('mfa-enroll-panel').style.display = 'block';
  document.getElementById('mfa-qr-wrap').innerHTML =
    `<img src="https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(uri)}" width="180" height="180" alt="MFA QR code"/>`;
  document.getElementById('mfa-secret-display').textContent = `Manual key: ${data.secret}`;
  document.getElementById('mfa-secret-display').style.display = 'block';
});

document.getElementById('mfa-enroll-cancel')?.addEventListener('click', () => {
  document.getElementById('mfa-enroll-panel').style.display = 'none';
  document.getElementById('mfa-disabled-panel').style.display = 'block';
});

document.getElementById('mfa-confirm-btn')?.addEventListener('click', async () => {
  const code = document.getElementById('mfa-enroll-code').value.trim();
  if (!code) return;
  const res = await apiFetch('/api/v1/auth/mfa/enroll/confirm', {
    method: 'POST',
    body: JSON.stringify({ code }),
  });
  if (!res || !res.ok) { alert('Invalid code — try again'); return; }
  const data = await res.json();
  if (data.backup_codes?.length) {
    const box = document.getElementById('backup-codes-box');
    box.textContent = 'Backup codes (save now):\n' + data.backup_codes.join('\n');
    box.style.display = 'block';
  }
  loadMfaStatus();
});

document.getElementById('mfa-disable-btn')?.addEventListener('click', async () => {
  const password = prompt('Enter your password to disable MFA:');
  if (!password) return;
  const code = prompt('Enter current MFA code:');
  if (!code) return;
  const res = await apiFetch('/api/v1/auth/mfa/disable', {
    method: 'POST',
    body: JSON.stringify({ password, code }),
  });
  if (!res || !res.ok) { alert('Could not disable MFA'); return; }
  loadMfaStatus();
});

/* ── Enterprise org / SCIM / CMEK ─────────────────────────────────────────── */
async function loadEnterprise() {
  const regionsRes = await apiFetch('/api/v1/auth/regions');
  if (regionsRes?.ok) {
    const { regions } = await regionsRes.json();
    const sel = document.getElementById('org-region-input');
    if (sel && !sel.options.length) {
      sel.innerHTML = regions.map(r =>
        `<option value="${escHtml(r.id)}">${escHtml(r.label || r.id)}</option>`
      ).join('');
    }
  }

  const res = await apiFetch('/api/v1/auth/organizations/me');
  if (res?.status === 404) {
    document.getElementById('org-none-card').style.display = 'block';
    document.getElementById('org-settings-wrap').style.display = 'none';
    return;
  }
  if (!res?.ok) return;

  const org = await res.json();
  document.getElementById('org-none-card').style.display = 'none';
  document.getElementById('org-settings-wrap').style.display = 'block';
  document.getElementById('org-title').textContent = org.name;
  document.getElementById('org-region-badge').textContent = org.data_region;
  document.getElementById('org-scim-status').textContent = org.scim_enabled ? 'On' : 'Off';
  document.getElementById('org-mfa-status').textContent = org.mfa_required ? 'Required' : 'Optional';
  document.getElementById('org-cmek-status').textContent = org.cmek_enabled ? 'Enabled' : 'Off';
  document.getElementById('org-mfa-required-toggle').checked = org.mfa_required;
  document.getElementById('scim-base-url').textContent =
    `${window.location.origin}/api/v1/scim/v2`;
}

document.getElementById('org-create-btn')?.addEventListener('click', async () => {
  const name = document.getElementById('org-name-input').value.trim();
  const slug = document.getElementById('org-slug-input').value.trim().toLowerCase();
  const data_region = document.getElementById('org-region-input').value;
  if (!name || !slug) { alert('Name and slug are required'); return; }
  const res = await apiFetch('/api/v1/auth/organizations', {
    method: 'POST',
    body: JSON.stringify({ name, slug, data_region, scim_enabled: true }),
  });
  if (!res?.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'Failed to create organization');
    return;
  }
  loadEnterprise();
});

document.getElementById('org-save-btn')?.addEventListener('click', async () => {
  const mfa_required = document.getElementById('org-mfa-required-toggle').checked;
  const res = await apiFetch('/api/v1/auth/organizations/me', {
    method: 'PATCH',
    body: JSON.stringify({ mfa_required }),
  });
  if (res?.ok) loadEnterprise();
  else alert('Could not save organization policy');
});

document.getElementById('scim-token-btn')?.addEventListener('click', async () => {
  const res = await apiFetch('/api/v1/auth/organizations/scim-token?label=IdP', { method: 'POST', body: '{}' });
  if (!res?.ok) { alert('Could not generate SCIM token'); return; }
  const data = await res.json();
  const box = document.getElementById('scim-token-reveal');
  box.textContent = `Token (copy now): ${data.token}\nSCIM URL: ${data.scim_base_url}`;
  box.style.display = 'block';
  loadEnterprise();
});

document.getElementById('cmek-save-btn')?.addEventListener('click', async () => {
  const vault_url = document.getElementById('cmek-vault-input').value.trim();
  const key_name = document.getElementById('cmek-key-input').value.trim();
  if (!vault_url || !key_name) return;
  const res = await apiFetch('/api/v1/auth/organizations/cmek', {
    method: 'POST',
    body: JSON.stringify({ vault_url, key_name }),
  });
  if (res?.ok) loadEnterprise();
  else alert('CMEK configuration failed — check vault URL and key name');
});

/* ── Copy snippet ─────────────────────────────────────────────────────────── */
document.getElementById('copy-snippet').addEventListener('click', () => {
  const code = document.getElementById('api-snippet').innerText;
  copyText(code, document.getElementById('copy-snippet'));
});

/* ── Utilities ────────────────────────────────────────────────────────────── */
function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    if (!btn) return;
    const prev = btn.textContent;
    btn.textContent = 'Copied!'; btn.classList.add('copied');
    setTimeout(() => { btn.textContent = prev; btn.classList.remove('copied'); }, 2000);
  });
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Close modal on backdrop click
document.getElementById('tenant-modal').addEventListener('click', e => {
  if (e.target === document.getElementById('tenant-modal'))
    document.getElementById('tenant-modal').style.display = 'none';
});
