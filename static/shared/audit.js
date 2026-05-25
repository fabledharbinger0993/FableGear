/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / audit
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 596-698
   ──────────────────────────────────────────────────────────────────────── */

/* ── Silent background audit ─────────────────────────────────────────────── */
function runSilentAudit() {
  fetch('/api/run/audit')
    .then(r => {
      const reader  = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      function pump() {
        return reader.read().then(({ done, value }) => {
          if (done) { _onSilentAuditDone(true); return; }
          buf += decoder.decode(value, { stream: true });
          if (buf.includes('[DONE]'))  { reader.cancel(); _onSilentAuditDone(true);  return; }
          if (buf.includes('[ERROR]')) { reader.cancel(); _onSilentAuditDone(false); return; }
          return pump();
        });
      }
      return pump();
    })
    .catch(() => _onSilentAuditDone(false));
}

function _onSilentAuditDone(ok) {
  showToast(ok ? 'Library audit complete ✓' : 'Audit skipped — check Settings for music drive', ok ? 'success' : 'neutral');
  refreshStatus();
  setTimeout(() => document.getElementById('step-process')
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 900);
}

/* ── Toast notification ──────────────────────────────────────────────────── */
function showToast(message, type = 'neutral') {
  const t = document.createElement('div');
  t.className = `sb-toast toast-${type}`;
  t.textContent = message;
  document.body.appendChild(t);
  t.addEventListener('animationend', e => { if (e.animationName === 'sb-toast-out') t.remove(); });
}

async function interruptScan() {
  if (!isRunning) return;
  const btn = document.getElementById('scan-bar-interrupt');
  btn.textContent = '⏸ Stopping…'; btn.disabled = true;
  try {
    const resp = await fetch('/api/cancel', { method: 'POST' });
    let data = null;
    try { data = await resp.json(); } catch (_) {}
    if (!resp.ok) {
      appendLog(`[ERROR] ${data?.error || 'Could not send interrupt signal.'}`, 'error');
      btn.textContent = '⏸ Interrupt'; btn.disabled = false;
      // If the server says "No active scan", the process is already gone — clean up the stuck UI
      if (resp.status === 404) {
        if (activeSource) { activeSource.close(); activeSource = null; }
        isRunning = false;
        setSpinner(false);
        setAllButtons(false);
        finishScanBar();
        appendLog('(Process had already exited — UI state cleared.)', 'dim');
      }
      return;
    }
    appendLog(data?.message || '⏸ Interrupt signal sent — waiting for process to exit…', 'warn');
  } catch(e) {
    appendLog('[ERROR] Could not send interrupt signal.', 'error');
    btn.textContent = '⏸ Interrupt'; btn.disabled = false;
  }
}

let _emergencyArmed = false;
let _emergencyArmTimer = null;

async function emergencyStop() {
  if (!isRunning) return;
  const btn = document.getElementById('scan-bar-emergency');
  if (!_emergencyArmed) {
    // First click — arm it for 3 seconds, require a second click to confirm
    _emergencyArmed = true;
    btn.textContent = '⚡ Click again to confirm';
    btn.classList.add('armed');
    _emergencyArmTimer = setTimeout(() => {
      _emergencyArmed = false;
      if (btn) { btn.textContent = '⚡ Emergency Stop'; btn.classList.remove('armed'); }
    }, 3000);
    return;
  }
  // Second click — fire
  clearTimeout(_emergencyArmTimer);
  _emergencyArmed = false;
  btn.textContent = '⚡ Killing…'; btn.disabled = true; btn.classList.remove('armed');
  try {
    const resp = await fetch('/api/cancel/force', { method: 'POST' });
    let data = null;
    try { data = await resp.json(); } catch (_) {}
    if (!resp.ok) {
      appendLog(`[ERROR] ${data?.error || 'Could not send kill signal.'}`, 'error');
      btn.textContent = '⚡ Emergency Stop'; btn.disabled = false;
      return;
    }
    appendLog('⚡ Emergency stop — process force-killed. Server is still running.', 'error');
  } catch(e) {
    appendLog('[ERROR] Could not send kill signal.', 'error');
    btn.textContent = '⚡ Emergency Stop'; btn.disabled = false;
  }
}

