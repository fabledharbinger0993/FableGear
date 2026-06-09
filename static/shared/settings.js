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

/* Clicking a locked card re-opens the onboarding wizard so the user can grant
   the permission it needs. Permission consent lives entirely in /onboarding;
   `reconfigure=1` allows re-entry after setup is already complete. */
document.addEventListener('click', e => {
  const card = e.target.closest('.card.permission-locked');
  if (!card) return;
  e.stopPropagation();
  window.location.href = '/onboarding?reconfigure=1';
}, true);

