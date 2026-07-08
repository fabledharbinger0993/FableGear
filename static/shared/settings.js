/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / settings
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 399-595
   ──────────────────────────────────────────────────────────────────────── */

/* ── Status polling ────────────────────────────────────────────────────────── */
/* ── Settings tabs ─────────────────────────────────────────────────────────── */
function settingsSwitchTab(tabId) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
  document.querySelectorAll('.settings-tab-panel').forEach(p => p.classList.toggle('active', p.id === tabId));
  document.querySelector('.settings-body')?.scrollTo(0, 0);  // each tab starts at the top
  if (tabId === 'tab-mcp') _settingsLoadMcp();
}

function _settingsLoadMcp() {
  const container = document.getElementById('settings-mcp-container');
  if (!container || container.dataset.loaded) return;
  const src = document.getElementById('mcp-panel');
  if (src) {
    container.innerHTML = src.innerHTML;
    container.dataset.loaded = '1';
    if (typeof mcpRefreshStatus === 'function') mcpRefreshStatus();
  }
}

function pickSettingsPath(inputId) {
  const api = window.pywebview && window.pywebview.api;
  if (api && api.pick_folder) {
    api.pick_folder().then(p => { if (p) document.getElementById(inputId).value = p; });
    return;
  }
  const cur = document.getElementById(inputId).value;
  const p = window.prompt('Enter path:', cur);
  if (p) document.getElementById(inputId).value = p;
}

/* ── Settings modal ────────────────────────────────────────────────────────── */
function openSettings() {
  // Load current config into the form
  fetch('/api/config').then(r => r.json()).then(cfg => {
    const mode = cfg.archive_mode || 'auto';
    document.querySelector(`input[name="archive-mode"][value="${mode}"]`).checked = true;
    document.getElementById('settings-custom-input').value = cfg.custom_archive || '';
    document.getElementById('settings-snapshot-cadence').value = cfg.snapshot_cadence || 'monthly';
    document.getElementById('settings-snapshot-master-db').checked = !!cfg.snapshot_include_master_db;
    const excluded = Array.isArray(cfg.excluded_dirs) ? cfg.excluded_dirs : [];
    document.getElementById('settings-excluded-dirs').value = excluded.join('\n');
    const acoustidEl = document.getElementById('settings-acoustid-key');
    if (acoustidEl) acoustidEl.value = cfg.acoustid_api_key || '';
    _settingsUpdateUI(mode);
    // Populate paths tab
    const pathFields = {
      'settings-music-root': cfg.music_root,
      'settings-local-db': cfg.local_db || '',
      'settings-djmt-db': cfg.djmt_db,
      'settings-backup-dir': cfg.backup_dir,
      'settings-archive-root': cfg.archive_root,
      'settings-quarantine': cfg.quarantine,
      'settings-reports': cfg.reports,
    };
    for (const [id, val] of Object.entries(pathFields)) {
      const el = document.getElementById(id);
      if (el) el.value = val || '';
    }
  }).catch(() => {
    document.querySelector('input[name="archive-mode"][value="auto"]').checked = true;
    _settingsUpdateUI('auto');
  });
  settingsSwitchTab('tab-general');
  _sbFadeBd('settings-backdrop', true);
  const _smb = document.getElementById('settings-modal');
  void _smb.offsetWidth; _sbAnim(_smb, 'sb-modal-in', '.28s');
}
function closeSettings() {
  _sbAnim(document.getElementById('settings-modal'), 'sb-modal-out', '.18s', () => {
    _sbFadeBd('settings-backdrop', false);
  });
}
function _settingsUpdateUI(mode) {
  document.getElementById('settings-custom-path').style.display  = mode === 'custom' ? 'block' : 'none';
  document.getElementById('settings-warnings').style.display     = mode === 'none'   ? 'block' : 'none';
}
document.addEventListener('change', e => {
  if (e.target.name === 'archive-mode') _settingsUpdateUI(e.target.value);
});

/* ── Welcome panel (what's new + room picker) ───────────────────────────────
   Permission consent is handled in the first-run onboarding wizard (/onboarding).
   This panel is purely informational and fires via the header Welcome button.    */

function welcomeShowStep(id) {
  document.querySelectorAll('.welcome-step').forEach(s => s.classList.remove('active'));
  const el = document.getElementById('wstep-' + id);
  if (el) el.classList.add('active');
}

function openWelcome() {
  welcomeShowStep('intro');
  _sbFadeBd('welcome-backdrop', true);
  const modal = document.getElementById('welcome-modal');
  void modal.offsetWidth; _sbAnim(modal, 'sb-modal-in', '.28s');
}

function closeWelcome() {
  _sbAnim(document.getElementById('welcome-modal'), 'sb-modal-out', '.18s', () => {
    _sbFadeBd('welcome-backdrop', false);
  });
}

function applyPermissions() {
  const readOk  = localStorage.getItem('fablegear-db-read')  === 'granted';
  const writeOk = localStorage.getItem('fablegear-db-write') === 'granted';
  // Rail buttons that require write permission.
  ['rail-btn-relocate','rail-btn-import','rail-btn-link'].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.classList.toggle('permission-locked', !writeOk);
    btn.disabled = !writeOk;
  });
  // Audit rail button requires read permission.
  const auditBtn = document.getElementById('rail-btn-audit');
  if (auditBtn) {
    auditBtn.classList.toggle('permission-locked', !readOk);
    auditBtn.disabled = !readOk;
  }
}

async function saveSettings() {
  const mode   = document.querySelector('input[name="archive-mode"]:checked')?.value || 'auto';
  const custom = document.getElementById('settings-custom-input').value.trim();
  const cadence = document.getElementById('settings-snapshot-cadence').value || 'monthly';
  if (mode === 'custom' && !custom) {
    showToast('Enter a folder path for the custom archive location.', 'warning');
    return;
  }
  const btn = document.querySelector('.settings-save');
  btn.textContent = 'Saving…'; btn.disabled = true;
  try {
    const res  = await fetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        archive_mode: mode,
        custom_archive_dir: custom,
        snapshot_cadence: cadence,
        snapshot_include_master_db: document.getElementById('settings-snapshot-master-db').checked,
        excluded_dirs: document.getElementById('settings-excluded-dirs').value
          .split('\n').map(s => s.trim()).filter(Boolean),
        acoustid_api_key: (document.getElementById('settings-acoustid-key')?.value || '').trim(),
      }),
    });
    const data = await res.json();
    if (data.ok) {
      closeSettings();
      // Restart the server to apply new config
      await fetch('/api/quit', { method: 'POST' }).catch(() => {});
      setTimeout(() => window.close(), 500);
    } else {
      showToast('Save failed — ' + (data.error || 'unknown error'), 'error');
    }
  } catch(e) {
    showToast('Could not save settings.', 'error');
  } finally {
    btn.textContent = 'Save'; btn.disabled = false;
  }
}

function pickSettingsArchiveFolder() {
  const api = window.pywebview && window.pywebview.api;
  if (api && api.pick_folder) {
    api.pick_folder().then(p => {
      if (p) document.getElementById('settings-custom-input').value = p;
    });
    return;
  }
  const cur = document.getElementById('settings-custom-input').value;
  const p = window.prompt('Enter archive folder path:', cur);
  if (p) document.getElementById('settings-custom-input').value = p;
}

function reopenOnboardingWizard() {
  window.location.href = '/onboarding?reconfigure=1';
}

// Explicitly named entry for the full install walkthrough (permissions, paths,
// archive policy, and MCP setup). Keep legacy name as an alias.
function openInstallWalkthrough() {
  reopenOnboardingWizard();
}

/* Clicking a locked card re-opens the onboarding wizard so the user can grant
   the permission it needs. Permission consent lives entirely in /onboarding;
   `reconfigure=1` allows re-entry after setup is already complete. */
document.addEventListener('click', e => {
  const card = e.target.closest('.card.permission-locked');
  if (!card) return;
  e.stopPropagation();
  reopenOnboardingWizard();
}, true);


/* ── MCP (AI Agent Access) ───────────────────────────────────────────────── */

let _mcpStatus = null;
let _mcpSnippetClient = 'claude-desktop';

function mcpPillClick() {
  openMcpPanel();
}

async function openMcpPanel() {
  await mcpRefreshStatus();
  _sbFadeBd('mcp-panel-backdrop', true);
  const panel = document.getElementById('mcp-panel');
  void panel.offsetWidth;
  _sbAnim(panel, 'sb-modal-in', '.28s');
}

function closeMcpPanel() {
  _sbAnim(document.getElementById('mcp-panel'), 'sb-modal-out', '.18s', () => {
    _sbFadeBd('mcp-panel-backdrop', false);
  });
}

async function mcpRefreshStatus() {
  try {
    const res = await fetch('/api/mcp/status');
    _mcpStatus = await res.json();
  } catch {
    _mcpStatus = { running: false, enabled: false };
  }
  mcpUpdateUI();
}

function mcpUpdateUI() {
  const s = _mcpStatus || {};
  const dot = document.getElementById('mcp-dot');
  const states = ['mcp-state-running', 'mcp-state-stopped', 'mcp-state-wizard'];
  states.forEach(id => document.getElementById(id)?.classList.add('hidden'));

  if (s.running) {
    document.getElementById('mcp-state-running')?.classList.remove('hidden');
    dot.className = 'mcp-dot green';
    const url = s.url || `http://localhost:${s.port || 5002}/sse`;
    document.getElementById('mcp-url-display').textContent = url;
    document.getElementById('mcp-port-display').textContent = s.port || '5002';
    mcpLoadSnippet(_mcpSnippetClient);
  } else if (s.enabled) {
    document.getElementById('mcp-state-stopped')?.classList.remove('hidden');
    dot.className = 'mcp-dot amber';
  } else {
    document.getElementById('mcp-state-wizard')?.classList.remove('hidden');
    dot.className = 'mcp-dot';
  }
}

async function mcpStart() {
  try {
    const res = await fetch('/api/mcp/start', { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast('MCP server started on port ' + (data.port || '5002'), 'success');
    } else {
      showToast('Failed to start MCP: ' + (data.error || 'unknown'), 'error');
    }
  } catch (e) {
    showToast('MCP start failed', 'error');
  }
  await mcpRefreshStatus();
}

async function mcpStop() {
  try {
    await fetch('/api/mcp/stop', { method: 'POST' });
    showToast('MCP server stopped', 'info');
  } catch { /* ignore */ }
  await mcpRefreshStatus();
}

async function mcpEnableFromWizard() {
  const autostart = document.getElementById('mcp-wiz-autostart')?.checked || false;
  try {
    const res = await fetch('/api/mcp/enable', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ autostart }),
    });
    const data = await res.json();
    if (data.ok) {
      showToast('AI agent access enabled — starting server…', 'success');
      await mcpStart();
    } else {
      showToast('Enable failed', 'error');
    }
  } catch {
    showToast('Enable failed', 'error');
  }
  await mcpRefreshStatus();
}

async function mcpDisable() {
  try {
    await fetch('/api/mcp/disable', { method: 'POST' });
    showToast('AI agent access disabled', 'info');
  } catch { /* ignore */ }
  await mcpRefreshStatus();
}

async function mcpLoadSnippet(client) {
  _mcpSnippetClient = client;
  document.querySelectorAll('.mcp-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.client === client);
  });
  try {
    const res = await fetch(`/api/mcp/config-snippet?client=${encodeURIComponent(client)}`);
    const data = await res.json();
    document.getElementById('mcp-snippet-code').textContent = data.snippet || '';
  } catch {
    document.getElementById('mcp-snippet-code').textContent = '(failed to load)';
  }
}

document.addEventListener('click', e => {
  const tab = e.target.closest('.mcp-tab');
  if (tab && tab.dataset.client) mcpLoadSnippet(tab.dataset.client);
});

function mcpCopyUrl() {
  const url = document.getElementById('mcp-url-display')?.textContent || '';
  navigator.clipboard.writeText(url).then(() => showToast('URL copied', 'success'));
}

function mcpCopySnippet() {
  const snippet = document.getElementById('mcp-snippet-code')?.textContent || '';
  navigator.clipboard.writeText(snippet).then(() => showToast('Config copied', 'success'));
}

/* Update the MCP pill dot on welcome open */
const _origOpenWelcome = typeof openWelcome === 'function' ? openWelcome : null;
if (_origOpenWelcome) {
  openWelcome = function() {
    _origOpenWelcome();
    fetch('/api/mcp/status').then(r => r.json()).then(s => {
      const dot = document.getElementById('mcp-dot');
      if (dot) dot.className = 'mcp-dot' + (s.running ? ' green' : s.enabled ? ' amber' : '');
    }).catch(() => {});
  };
}
