/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / settings
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 399-595
   ──────────────────────────────────────────────────────────────────────── */

/* ── Status polling ────────────────────────────────────────────────────────── */
/* ── Settings modal ────────────────────────────────────────────────────────── */
function openSettings() {
  // Load current config into the form
  fetch('/api/config').then(r => r.json()).then(cfg => {
    const mode = cfg.archive_mode || 'auto';
    document.querySelector(`input[name="archive-mode"][value="${mode}"]`).checked = true;
    document.getElementById('settings-custom-input').value = cfg.custom_archive || '';
    const excluded = Array.isArray(cfg.excluded_dirs) ? cfg.excluded_dirs : [];
    document.getElementById('settings-excluded-dirs').value = excluded.join('\n');
    _settingsUpdateUI(mode);
  }).catch(() => {
    document.querySelector('input[name="archive-mode"][value="auto"]').checked = true;
    _settingsUpdateUI('auto');
  });
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

/* ── Welcome wizard ─────────────────────────────────────────────────────────
   Permission keys: fablegear-db-read / fablegear-db-write = 'granted'|'denied'
   Setup gate:      fablegear-setup-complete = '1'                             */

let _wReadGranted  = false;
let _wWriteGranted = false;

function welcomeShowStep(id) {
  document.querySelectorAll('.welcome-step').forEach(s => s.classList.remove('active'));
  const el = document.getElementById('wstep-' + id);
  if (el) el.classList.add('active');
}

function openWelcome() {
  _wReadGranted  = localStorage.getItem('fablegear-db-read')  === 'granted';
  _wWriteGranted = localStorage.getItem('fablegear-db-write') === 'granted';
  // Returning users land on the read step so they can adjust permissions
  welcomeShowStep(localStorage.getItem('fablegear-setup-complete') ? 'read' : 'intro');
  _sbFadeBd('welcome-backdrop', true);
  const modal = document.getElementById('welcome-modal');
  void modal.offsetWidth; _sbAnim(modal, 'sb-modal-in', '.28s');
}

function closeWelcome() {
  _sbAnim(document.getElementById('welcome-modal'), 'sb-modal-out', '.18s', () => {
    _sbFadeBd('welcome-backdrop', false);
  });
}

function welcomeGrantRead() {
  _wReadGranted = true;
  welcomeShowStep('write');
}
function welcomeDenyRead() {
  _wReadGranted  = false;
  _wWriteGranted = false;
  _welcomeShowReady();
}
function welcomeGrantWrite() {
  _wWriteGranted = true;
  _welcomeShowReady();
}
function welcomeDenyWrite() {
  _wWriteGranted = false;
  _welcomeShowReady();
}

function _welcomeShowReady() {
  const body = document.getElementById('wstep-ready-body');
  if (_wReadGranted && _wWriteGranted) {
    body.innerHTML =
      `<p class="welcome-step-title">You're all set.</p>
       <p class="welcome-step-sub">We'll kick off a quick library audit automatically — it's read-only and maps where Rekordbox thinks everything is. It runs silently in the background. When it's done you'll land on Tag Tracks, and the tools will have data to work with.</p>
       <p class="welcome-step-sub" style="color:var(--safe)">✓ Full access — all tools enabled.</p>`;
  } else if (_wReadGranted) {
    body.innerHTML =
      `<p class="welcome-step-title">Read-only mode.</p>
       <p class="welcome-step-sub">We'll run a quick library audit to map your library. Available: Library Audit, Tag Tracks, Find Duplicates, Normalize, Convert, Organize, Novelty Scanner, Pipeline Builder.</p>
       <p class="welcome-step-sub" style="color:var(--caution)">⚠ Write tools are locked: Fix Broken Paths, Import, Link Playlists, Prune. Enable them anytime via the lightbulb icon.</p>`;
  } else {
    body.innerHTML =
      `<p class="welcome-step-title">Limited mode.</p>
       <p class="welcome-step-sub">Database tools aren't available. These work without database access: Tag Tracks (file analysis), Find Duplicates (folder scan), Normalize, Convert, Organize, Novelty Scanner.</p>
       <p class="welcome-step-sub" style="color:var(--text-dim)">Enable database access anytime via the lightbulb icon in the bottom-right corner.</p>`;
  }
  welcomeShowStep('ready');
}

async function completeSetup() {
  const readVal  = _wReadGranted  ? 'granted' : 'denied';
  const writeVal = _wWriteGranted ? 'granted' : 'denied';
  // Mirror to localStorage as fast cache, but truth lives server-side.
  localStorage.setItem('fablegear-db-read',        readVal);
  localStorage.setItem('fablegear-db-write',       writeVal);
  localStorage.setItem('fablegear-setup-complete', '1');
  if (_wWriteGranted) {
    localStorage.setItem('fablegear-archive-permission', 'granted');
    fetch('/api/setup-archive', { method: 'POST' }).catch(() => {});
  }
  // Persist to ~/.rekordbox-toolkit/fablegear-state.json so it survives
  // across pywebview sessions even if WKWebView clears localStorage.
  await fetch('/api/setup-complete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ db_read: readVal, db_write: writeVal }),
  }).catch(() => {});
  applyPermissions();
  closeWelcome();
  if (_wReadGranted) {
    setTimeout(runSilentAudit, 700);
  } else {
    setTimeout(() => document.getElementById('step-process')
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 500);
  }
}

function applyPermissions() {
  const readOk  = localStorage.getItem('fablegear-db-read')  === 'granted';
  const writeOk = localStorage.getItem('fablegear-db-write') === 'granted';
  // step-duplicates contains both a read-only scan phase and a write prune phase.
  // Don't lock the whole card — the scan should always be accessible.
  // The prune button is already guarded by the 2-step confirm + RB-running check.
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
        excluded_dirs: document.getElementById('settings-excluded-dirs').value
          .split('\n').map(s => s.trim()).filter(Boolean),
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

/* Clicking a locked card reopens the wizard at the relevant step */
document.addEventListener('click', e => {
  const card = e.target.closest('.card.permission-locked');
  if (!card) return;
  e.stopPropagation();
  _wReadGranted  = localStorage.getItem('fablegear-db-read')  === 'granted';
  _wWriteGranted = localStorage.getItem('fablegear-db-write') === 'granted';
  const needsWrite = ['rail-btn-relocate','rail-btn-import','rail-btn-link','step-duplicates'].includes(card.id);
  openWelcome();
  welcomeShowStep(needsWrite ? 'write' : 'read');
}, true);

