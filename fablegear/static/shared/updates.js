/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / updates
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 699-1204
   ──────────────────────────────────────────────────────────────────────── */

/* ── Homebrew update banner ─────────────────────────────────────────────────── */

let _brewDismissed = false;

async function brewCheckStatus() {
  try {
    const res = await fetch('/api/brew/status');
    if (!res.ok) return;
    const data = await res.json();
    _brewRender(data);
  } catch (_) {}
}

async function brewCheckNow() {
  document.getElementById('brew-msg').textContent = 'Checking for Homebrew updates…';
  document.getElementById('brew-banner').style.display = 'flex';
  _brewDismissed = false;
  try {
    const res = await fetch('/api/brew/check', { method: 'POST' });
    const data = await res.json();
    _brewRender(data);
  } catch (e) {
    document.getElementById('brew-msg').textContent = 'Could not reach brew — check manually.';
  }
}

function _brewRender(data) {
  const banner   = document.getElementById('brew-banner');
  const msgEl    = document.getElementById('brew-msg');
  if (_brewDismissed) return;
  const outdated = data.outdated || [];
  if (!outdated.length) {
    banner.style.display = 'none';
    return;
  }
  const list = outdated.map(p =>
    `<strong>${p.name}</strong> ${p.installed} → ${p.current}`
  ).join(' &nbsp;·&nbsp; ');
  msgEl.innerHTML = `Homebrew updates available for FableGear packages: ${list}`;
  banner.style.display = 'flex';
}

function brewDismiss() {
  _brewDismissed = true;
  document.getElementById('brew-banner').style.display = 'none';
}

/* ── FableGear update checker ────────────────────────────────────────────────── */
let _rkbUpdateData   = null;   // populated when update found; used by modal buttons
let _rkbModalShown   = false;  // ask permission at most once per session

async function fablegearUpdateCheck() {
  if (_rkbModalShown) return;
  try {
    const res = await fetch('/api/update/status');
    if (!res.ok) return;
    const data = await res.json();
    // Silently do nothing if no update or no connection
    if (!data.update_available) return;
    _rkbUpdateData = data;
    _rkbModalShown = true;
    _rkbShowUpdateModal(data);
  } catch (_) {
    // No internet / server unreachable — silent, keep running current version
  }
}

/* Manual "Check for Updates" (Settings button). Unlike the silent automatic
   check above, this one is LOUD both ways: it forces a live check
   (?refresh=1) and always announces the outcome — up to date, update found,
   or the exact failure. */
async function fablegearUpdateCheckManual(btn) {
  const label = btn?.textContent;
  if (btn) { btn.disabled = true; btn.textContent = 'Checking…'; }
  try {
    const res  = await fetch('/api/update/status?refresh=1');
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.error) {
      showToast(`Update check failed — ${data.error || res.statusText || 'server error'}. Are you online?`, 'error');
      return;
    }
    if (data.update_available) {
      _rkbUpdateData = data;
      _rkbModalShown = true;
      _rkbShowUpdateModal(data);
      return;
    }
    const cur = data.current_version ? ` — FableGear ${data.current_version} is the latest` : '';
    showToast(`You're up to date${cur}.`, 'success');
  } catch (e) {
    showToast(`Update check failed — ${e.message || 'could not reach the server'}. Are you online?`, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

function _rkbShowUpdateModal(data) {
  const latest  = data.latest_version || 'a newer version';
  const current = data.current_version;
  const overlay = document.getElementById('rkb-update-overlay');
  const title   = document.getElementById('rkb-update-title');
  const body    = document.getElementById('rkb-update-body');
  const goBtn   = document.getElementById('rkb-update-go');

  title.textContent = current
    ? `FableGear ${latest} is available`
    : 'FableGear update available';

  if (data.is_git_install) {
    body.textContent = current
      ? `You're running ${current}. FableGear will pull ${latest} and restart itself — takes about 10 seconds. Your library and settings are untouched.`
      : `A newer version is available. FableGear will pull it and restart itself — takes about 10 seconds.`;
    goBtn.textContent = 'Update now';
  } else {
    const dlUrl = data.download_url || data.release_url || '#';
    body.textContent = current
      ? `You're running ${current}. Download ${latest}, replace your current FableGear.app, and relaunch.`
      : `A newer version is available. Download it, replace your current FableGear.app, and relaunch.`;
    goBtn.textContent = 'Download FableGear.zip';
    goBtn.dataset.dlUrl = dlUrl;
  }

  overlay.style.display = 'flex';
}

async function rkbUpdateGo() {
  const data = _rkbUpdateData;
  if (!data) return;

  if (!data.is_git_install) {
    // ZIP install — open download in new tab, hide modal, leave banner reminder
    document.getElementById('rkb-update-overlay').style.display = 'none';
    const url = document.getElementById('rkb-update-go').dataset.dlUrl;
    if (url && url !== '#') window.open(url, '_blank', 'noopener');
    _rkbShowBanner(data);
    return;
  }

  // Git install — pull + restart in place
  const body     = document.getElementById('rkb-update-body');
  const goBtn    = document.getElementById('rkb-update-go');
  const skipBtn  = document.getElementById('rkb-update-skip');
  const titleEl  = document.getElementById('rkb-update-title');

  goBtn.disabled   = true;
  skipBtn.disabled = true;
  goBtn.style.opacity   = '0.5';
  skipBtn.style.opacity = '0.5';
  goBtn.textContent = 'Updating…';
  body.textContent  = 'Pulling the latest release from GitHub…';

  let resp;
  try {
    resp = await fetch('/api/update/apply', { method: 'POST' });
  } catch (e) {
    _rkbShowUpdateError('Could not reach the server to start the update.');
    return;
  }

  let payload;
  try { payload = await resp.json(); } catch (_) { payload = null; }

  if (!resp.ok || !payload || !payload.ok) {
    const err = (payload && payload.error) || `Update failed (HTTP ${resp.status}).`;
    _rkbShowUpdateError(err);
    return;
  }

  // Server pulled successfully and is now shutting itself down.
  titleEl.textContent = 'Restarting FableGear…';
  body.innerHTML =
    '<span style="display:inline-block;width:14px;height:14px;border:2px solid rgba(196,181,253,0.3);'
    + 'border-top-color:#c4b5fd;border-radius:50%;animation:spin .7s linear infinite;margin-right:10px;'
    + 'vertical-align:middle;"></span>'
    + 'Waiting for the server to come back online. The page will reload automatically.';
  goBtn.style.display = 'none';
  skipBtn.style.display = 'none';

  _rkbWaitForServerThenReload();
}

// Poll /api/update/status until the server responds again, then reload.
// Gap timeline: SIGTERM ~0.7s, port free ~0.2s, helper sleep 2s, Flask boot
// a few seconds — typical total 4-8s. Give up after ~60s.
async function _rkbWaitForServerThenReload() {
  // Initial grace so we don't race the old process that's still shutting down
  await new Promise(r => setTimeout(r, 1500));

  const started = Date.now();
  while (Date.now() - started < 60000) {
    try {
      const ctrl = new AbortController();
      const t    = setTimeout(() => ctrl.abort(), 1500);
      const res  = await fetch('/api/update/status', { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(t);
      if (res.ok) {
        // Small buffer so the server finishes initializing other routes too
        await new Promise(r => setTimeout(r, 400));
        window.location.reload();
        return;
      }
    } catch (_) { /* server still down — keep polling */ }
    await new Promise(r => setTimeout(r, 800));
  }

  _rkbShowUpdateError(
    'Server did not come back online after 60 seconds. '
    + 'Try launching FableGear manually from your dock.'
  );
}

function _rkbShowUpdateError(msg) {
  const body    = document.getElementById('rkb-update-body');
  const goBtn   = document.getElementById('rkb-update-go');
  const skipBtn = document.getElementById('rkb-update-skip');
  const titleEl = document.getElementById('rkb-update-title');

  titleEl.textContent    = 'Update failed';
  body.textContent       = msg;
  goBtn.style.display    = '';
  skipBtn.style.display  = '';
  goBtn.disabled         = false;
  skipBtn.disabled       = false;
  goBtn.style.opacity    = '';
  skipBtn.style.opacity  = '';
  goBtn.textContent      = 'Retry';
  skipBtn.textContent    = 'Close';
}

function rkbUpdateSkip() {
  // Dismiss modal, show the smaller banner as a reminder
  document.getElementById('rkb-update-overlay').style.display = 'none';
  if (_rkbUpdateData) _rkbShowBanner(_rkbUpdateData);
}

function _rkbShowBanner(data) {
  const latest  = data.latest_version || 'a newer version';
  const current = data.current_version;
  const msgEl   = document.getElementById('fablegear-update-msg');
  const linkEl  = document.getElementById('fablegear-update-link');

  if (data.is_git_install) {
    msgEl.textContent = current
      ? `FableGear ${latest} available — close and relaunch to update.`
      : `FableGear update available — close and relaunch to update.`;
    linkEl.style.display = 'none';
  } else {
    msgEl.textContent = current
      ? `FableGear ${latest} available (you have ${current}).`
      : `FableGear update available.`;
    const dlUrl = data.download_url || data.release_url;
    if (dlUrl) {
      linkEl.href = dlUrl;
      linkEl.textContent = 'Download FableGear.zip';
      linkEl.style.display = '';
    } else {
      linkEl.style.display = 'none';
    }
  }
  document.getElementById('fablegear-update-banner').style.display = 'flex';
}

function fablegearUpdateDismiss() {
  document.getElementById('fablegear-update-banner').style.display = 'none';
}

function runBrewUpgrade() {
  brewDismiss();
  runCommand('/api/run/brew-upgrade', 'Homebrew — Upgrade FableGear Packages');
}

// Check on page load (non-blocking — banners appear only if updates found)
brewCheckStatus();
// Delay the update check slightly so the brew check fires first. The server's
// own GitHub check runs ~5 s after boot, so the first page load may race it —
// re-check at 45 s to catch the result on a fresh open (modal shows at most once).
setTimeout(fablegearUpdateCheck, 1000);
setTimeout(fablegearUpdateCheck, 45000);

async function quitFableGear() {
  const msg = isRunning
    ? '⚠️ A scan is still running.\n\nShutting down now will cancel it mid-process. Are you sure?'
    : 'Shut down FableGear?\n\nThe server will stop and this window will close.';
  if (!confirm(msg)) return;
  const btn = document.getElementById('quit-btn');
  btn.textContent = 'Shutting down…';
  btn.disabled = true;
  try {
    await fetch('/api/quit', { method: 'POST' });
  } catch(_) {}
  // window.close() only works on script-opened tabs — replace the page instead
  setTimeout(() => {
    document.open();
    document.write(
      '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>FableGear — Stopped</title>'
      + '<style>body{background:#0a0a0a;color:#555;font-family:ui-monospace,monospace;'
      + 'display:flex;align-items:center;justify-content:center;height:100vh;margin:0;'
      + 'flex-direction:column;gap:16px;}p{margin:0;font-size:.9rem;letter-spacing:.04em;}'
      + 'strong{color:#888;}</style></head><body>'
      + '<p><strong>FableGear has shut down.</strong></p>'
      + '<p>Close this tab or relaunch the app to continue.</p>'
      + '</body></html>'
    );
    document.close();
  }, 500);
}

function minimizeWindow() {
  if (window.pywebview?.api?.minimize) window.pywebview.api.minimize();
}

function toggleFullscreen() {
  if (window.pywebview?.api?.toggle_fullscreen) window.pywebview.api.toggle_fullscreen();
}

function replaySplash() {
  const overlay = document.getElementById('splash-overlay');
  const video   = document.getElementById('splash-video');
  if (!overlay || !video) return;
  overlay.hidden = false;
  video.currentTime = 0;
  video.play().catch(() => {});
  video.onended = () => { overlay.hidden = true; };
  overlay.onclick = (e) => {
    if (e.target !== video) { overlay.hidden = true; video.pause(); }
  };
}

function openSiteKey() {
  // TODO: implement site key / definitions modal
  console.info('[FableGear] openSiteKey — modal not yet implemented');
}

async function refreshStatus() {
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    rbRunning = data.rb_running;

    const dot   = document.getElementById('rb-dot');
    const label = document.getElementById('rb-label');
    if (rbRunning) {
      dot.className = 'dot danger pulse';
      label.textContent = 'RekordBox is OPEN — close before writing';
      label.style.color = 'var(--danger)';
    } else {
      dot.className = 'dot safe';
      label.textContent = 'RekordBox is closed — safe to write';
      label.style.color = 'var(--safe)';
    }

    if (data.backup?.exists) {
      const bp = document.getElementById('backup-pill');
      const bl = document.getElementById('backup-label');
      bp.style.display = 'flex';
      bl.textContent = `Last backup: ${data.backup.age}`;
    }

    const rp = document.getElementById('release-pill');
    const rl = document.getElementById('release-label');
    if (rp && rl) {
      if (data.release?.exists && data.release?.label) {
        rp.style.display = 'flex';
        rl.textContent = data.release.label;
      } else {
        rp.style.display = 'none';
      }
    }

    // ── Drive-offline banner ──────────────────────────────────────────────
    _updateDriveBanner(data.drives);
    // ── Health hazard panel (summary-driven; full fetch only when count changes) ──
    _updateHealthFromStatus(data.health);
    // ── Volume hotplug detection ──────────────────────────────────────────
    _detectVolumeChanges(data.volumes || []);
    // ── Refresh left-rail drive list if open ─────────────────────────────
    if (typeof _driveListOpen !== 'undefined' && _driveListOpen) initDriveList();
  } catch (_) {}
}

function _driveName(pathStr) {
  if (!pathStr) return 'drive';
  const parts = pathStr.split('/');
  if (parts[1] === 'Volumes' && parts[2]) return parts[2];
  return pathStr;
}

// Tracks which drives were offline on last poll — used to auto-clear results
// and reset dismiss state when drives come back online.
let _lastDrivesOffline = false;
let _driveBannerDismissed = false;

function dismissDriveBanner() {
  _driveBannerDismissed = true;
  document.getElementById('drive-offline-banner').style.display = 'none';
}

function _updateDriveBanner(drives) {
  const banner  = document.getElementById('drive-offline-banner');
  const detail  = document.getElementById('drive-offline-detail');
  const actions = document.getElementById('drive-offline-actions');
  if (!banner || !detail) return;

  if (!drives) { banner.style.display = 'none'; return; }

  const STEPS = {
    not_configured: [
      '1. Open a terminal in the FableGear folder.',
      '2. Run: <code>python3 cli.py setup</code>',
      '3. Follow the prompts to enter your DB and library paths.',
    ],
    local_db: [
      '1. Confirm Rekordbox is installed on this Mac.',
      '2. Open Rekordbox at least once so it creates its database.',
      '3. Restart FableGear — the path should resolve automatically.',
      '4. If the issue persists, use <em>Auto-detect</em> below.',
    ],
    device_db: [
      '1. Connect the DJ drive (USB or Thunderbolt).',
      '2. Wait for macOS to mount it — the banner will clear automatically.',
      '3. If the drive name changed, use <em>Auto-detect</em> to find the new path.',
    ],
    music_root: [
      '1. Connect the music library drive.',
      '2. Wait for macOS to mount it — the banner will clear automatically.',
      '3. If the drive name changed, use <em>Auto-detect</em> to find the new path.',
    ],
  };

  function _stepsHtml(key) {
    return `<ul class="drive-fix-steps">${STEPS[key].map(s => `<li>${s}</li>`).join('')}</ul>`;
  }

  const msgs   = [];
  let needsDrive = false;

  if (!drives.configured) {
    msgs.push(`<strong style="color:var(--warn)">Not configured</strong>${_stepsHtml('not_configured')}`);
  } else {
    if (!drives.local_db_ok) {
      msgs.push(`Local Rekordbox database not found${_stepsHtml('local_db')}`);
    }
    if (!drives.device_db_ok) {
      msgs.push(`DJ drive database offline — <strong>${_driveName(drives.device_db_path)}</strong>${_stepsHtml('device_db')}`);
      needsDrive = true;
    }
    if (!drives.music_root_ok) {
      msgs.push(`Music library offline — <strong>${_driveName(drives.music_root_path)}</strong>${_stepsHtml('music_root')}`);
      needsDrive = true;
    }
  }

  const nowOffline = msgs.length > 0;

  // If drives just came back online, un-dismiss the banner for next offline event
  if (_lastDrivesOffline && !nowOffline) {
    _driveBannerDismissed = false;
    _clearDetectResults();
  }
  _lastDrivesOffline = nowOffline;

  if (!nowOffline) {
    banner.style.display = 'none';
    return;
  }
  if (_driveBannerDismissed) return;

  detail.innerHTML = msgs.join('<hr style="border-color:rgba(255,255,255,.06);margin:6px 0">');
  if (actions) actions.style.display = needsDrive ? 'flex' : 'none';
  banner.style.display = 'block';
}

function _clearDetectResults() {
  const r = document.getElementById('drive-detect-results');
  const s = document.getElementById('drive-detect-status');
  if (r) { r.innerHTML = ''; r.style.display = 'none'; }
  if (s) s.textContent = '';
}

async function autodetectDrives() {
  const status  = document.getElementById('drive-detect-status');
  const results = document.getElementById('drive-detect-results');
  if (!status || !results) return;

  status.textContent = 'Scanning…';
  results.style.display = 'none';
  results.innerHTML = '';

  try {
    const data = await fetch('/api/drives/autodetect').then(r => r.json());
    status.textContent = '';

    const rows = [];

    (data.device_db || []).forEach(p => {
      rows.push(`<div class="candidate-row">
        <span style="color:var(--text-muted);flex-shrink:0">DJ DB:</span>
        <code>${p}</code>
        <button class="btn btn-secondary btn-xs" onclick="applyDriveFix('device_db','${p.replace(/'/g,"\\'")}',this)">Apply</button>
      </div>`);
    });

    (data.music_root || []).forEach(p => {
      rows.push(`<div class="candidate-row">
        <span style="color:var(--text-muted);flex-shrink:0">Music:</span>
        <code>${p}</code>
        <button class="btn btn-secondary btn-xs" onclick="applyDriveFix('music_root','${p.replace(/'/g,"\\'")}',this)">Apply</button>
      </div>`);
    });

    if (rows.length === 0) {
      results.innerHTML = '<span style="color:var(--text-muted)">No candidates found — connect the drive and try again.</span>';
    } else {
      results.innerHTML = rows.join('');
    }
    results.style.display = 'block';
  } catch (_) {
    status.textContent = 'Scan failed.';
  }
}

async function applyDriveFix(key, path, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  try {
    const res  = await fetch('/api/drives/apply-fix', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: path }),
    });
    const data = await res.json();
    if (data.ok) {
      if (btn) { btn.textContent = '✓ Applied'; btn.style.color = 'var(--safe)'; }
      // Trigger an immediate status refresh so the banner updates
      setTimeout(refreshStatus, 400);
    } else {
      if (btn) { btn.disabled = false; btn.textContent = 'Apply'; }
      appendLog(`Drive fix failed: ${data.error}`, 'error');
    }
  } catch (_) {
    if (btn) { btn.disabled = false; btn.textContent = 'Apply'; }
  }
}

