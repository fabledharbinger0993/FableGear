/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / health
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1205-1289
   ──────────────────────────────────────────────────────────────────────── */

// ── Health hazard panel ───────────────────────────────────────────────────────

let _healthPanelDismissed = false;

function dismissHealthPanel() {
  _healthPanelDismissed = true;
  document.getElementById('health-panel').style.display = 'none';
}

function _severityIcon(s) {
  return s === 'critical' ? '🔴' : s === 'warn' ? '🟡' : 'ℹ️';
}

function _renderHealthFindings(findings) {
  const panel   = document.getElementById('health-panel');
  const list    = document.getElementById('health-findings-list');
  const badge   = document.getElementById('health-panel-badge');
  if (!panel || !list) return;

  const critical = findings.filter(f => f.severity === 'critical').length;
  const warn     = findings.filter(f => f.severity === 'warn').length;

  if (!findings.length) {
    panel.style.display = 'none';
    return;
  }

  badge.textContent = critical ? `${critical} critical` : `${warn} warning${warn !== 1 ? 's' : ''}`;
  badge.className   = `health-badge ${critical ? 'health-badge-critical' : 'health-badge-warn'}`;

  list.innerHTML = findings.map(f => `
    <div class="health-finding health-finding-${f.severity}">
      <div class="health-finding-title">${_severityIcon(f.severity)} <strong>${f.title}</strong></div>
      <div class="health-finding-detail">${f.detail}</div>
      ${f.fix_hint ? `<div class="health-finding-hint">↳ ${f.fix_hint}</div>` : ''}
      ${f.fix_action ? `<div class="health-finding-actions">
        <button class="btn btn-neon btn-xs" onclick="runHealthFix('${f.id}', '${f.fix_action}')">${f.fix_action_label || 'Run Fix'}</button>
      </div>` : ''}
    </div>
  `).join('');

  if (!_healthPanelDismissed) {
    panel.style.display = 'block';
  }
}

// Called once at startup and when Re-check is clicked.
// refreshStatus() picks up the summary from /api/status every 6s —
// that triggers a full /api/health fetch only when the count changes.
let _lastHealthTotal = -1;

async function runHealthCheck(force = false) {
  const btn = document.getElementById('health-recheck-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  try {
    const url  = force ? '/api/health?force=1' : '/api/health';
    const data = await fetch(url).then(r => r.json());
    _renderHealthFindings(data.findings || []);
    _lastHealthTotal = (data.summary || {}).total || 0;
  } catch (_) { /* non-fatal */ }
  finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Re-check'; }
  }
}

// Integrated into refreshStatus — pulls full findings only when count changes
function _updateHealthFromStatus(healthSummary) {
  if (!healthSummary) return;
  const total = healthSummary.total || 0;
  if (total !== _lastHealthTotal) {
    _lastHealthTotal = total;
    // Re-dismissed state resets when findings change
    if (total === 0) {
      _healthPanelDismissed = false;
      const p = document.getElementById('health-panel');
      if (p) p.style.display = 'none';
    } else {
      runHealthCheck(false);
    }
  }
}


async function runHealthFix(findingId, action) {
  const body = { id: findingId, action };
  if (action === 'move_backup_dir') {
    let newPath;
    if (typeof window.pywebview !== 'undefined' && window.pywebview.api?.pick_folder) {
      newPath = await window.pywebview.api.pick_folder();
    } else {
      newPath = prompt('Enter the path for the new backup directory:');
    }
    if (!newPath) return;
    body.path = newPath;
  }
  try {
    const resp = await fetch('/api/health/fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (resp.ok) {
      showToast(data.message || 'Fix applied.', 'success');
      runHealthCheck(true);
    } else {
      showToast(data.error || 'Fix failed.', 'error');
    }
  } catch (e) {
    showToast('Could not apply fix: ' + (e.message || e), 'error');
  }
}

setInterval(refreshStatus, 6000);
// Initial status + health check (staggered so health runs after status settles)
refreshStatus();
setTimeout(() => runHealthCheck(false), 1200);

