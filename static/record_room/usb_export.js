/* ════════════════════════════════════════════════════════════════════════
   FableGear — record_room / usb_export
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 6060-6286
   ──────────────────────────────────────────────────────────────────────── */

/* ══ USB Export ════════════════════════════════════════════════════════════ */

let _leExportDrivePath = null;
let _leExportPollTimer = null;

function _leExportGb(bytes) {
  return (bytes / 1e9).toFixed(1) + ' GB';
}

async function leOpenExportModal() {
  document.getElementById('le-export-backdrop').classList.remove('hidden');
  document.getElementById('le-export-modal').classList.remove('hidden');
  document.getElementById('le-export-progress').classList.add('hidden');
  document.getElementById('le-export-audit-action')?.classList.add('hidden');
  document.getElementById('le-export-errors').classList.add('hidden');
  document.getElementById('le-export-submit').disabled = true;
  _leExportDrivePath = null;

  // Populate playlists from already-rendered tree
  const plContainer = document.getElementById('le-export-playlists');
  const treeItems = [...document.querySelectorAll('#le-playlist-tree .le-tree-item[data-type="playlist"]')];
  if (treeItems.length) {
    plContainer.innerHTML = treeItems.map(btn => {
      const id = btn.dataset.id;
      const name = btn.querySelector('.le-tree-label')?.textContent || '';
      const count = btn.querySelector('.le-tree-count')?.textContent || '';
      return `<label class="le-export-pl-row">
        <input type="checkbox" class="le-export-pl-cb" value="${id}" checked onchange="_leExportUpdateSubmit()">
        <span class="le-export-pl-name">${_leEsc(name)}</span>
        <span class="le-export-pl-count">${_leEsc(count)}</span>
      </label>`;
    }).join('');
  } else {
    plContainer.innerHTML = '<div class="le-export-loading">Load your library first, then reopen Export.</div>';
  }

  // Fetch drives
  const driveContainer = document.getElementById('le-export-drives');
  driveContainer.innerHTML = '<div class="le-export-loading">Scanning drives…</div>';
  try {
    const res = await fetch('/api/library/export/drives');
    const drives = await res.json();
    const pioneer = drives.filter(d => d.pioneer);
    if (!pioneer.length) {
      driveContainer.innerHTML = '<div class="le-export-no-drives">No Pioneer USB drives found. Insert a drive that Rekordbox has exported to at least once.</div>';
    } else {
      const supported = pioneer.filter(d => d.export_supported);
      _leExportDrivePath = supported.length ? supported[0].path : null;
      driveContainer.innerHTML = pioneer.map(d => {
        const checked = _leExportDrivePath === d.path ? 'checked' : '';
        const disabled = d.export_supported ? '' : 'disabled';
        const badge = d.export_supported ? 'Pioneer' : 'Detected Pioneer';
        const note = d.export_supported
          ? (d.layout === 'master-db' ? 'FableGear export supported' : (d.layout || 'Supported'))
          : (d.export_error || 'Unsupported export target');
        const detail = d.export_supported ? (d.export_note || '') : '';
        return `
        <label class="le-export-drive">
          <input type="radio" name="le-export-drive" value="${_leEsc(d.path)}" ${checked} ${disabled} onchange="_leExportDrivePath=this.value;_leExportUpdateSubmit()">
          <span class="le-export-drive-pioneer">${_leEsc(badge)}</span>
          <span class="le-export-drive-name">${_leEsc(d.name)}</span>
          <span class="le-export-drive-meta">${_leExportGb(d.free_bytes)} free / ${_leExportGb(d.total_bytes)}</span>
          <span class="le-export-drive-meta">${_leEsc(note)}</span>
          ${detail ? `<span class="le-export-drive-detail">${_leEsc(detail)}</span>` : ''}
        </label>`;
      }).join('');
    }
  } catch (_) {
    driveContainer.innerHTML = '<div class="le-export-no-drives">Could not scan drives.</div>';
  }
  _leExportUpdateSubmit();
}

function leCloseExportModal() {
  if (_leExportPollTimer) { clearInterval(_leExportPollTimer); _leExportPollTimer = null; }
  document.getElementById('le-export-backdrop').classList.add('hidden');
  document.getElementById('le-export-modal').classList.add('hidden');
}

function leExportSelectAll()  { document.querySelectorAll('.le-export-pl-cb').forEach(cb => cb.checked = true);  _leExportUpdateSubmit(); }
function leExportSelectNone() { document.querySelectorAll('.le-export-pl-cb').forEach(cb => cb.checked = false); _leExportUpdateSubmit(); }

function _leExportUpdateSubmit() {
  const hasDrive = !!_leExportDrivePath;
  const hasPlaylists = [...document.querySelectorAll('.le-export-pl-cb')].some(cb => cb.checked);
  document.getElementById('le-export-submit').disabled = !(hasDrive && hasPlaylists);
}

async function leStartExport() {
  const selectedIds = [...document.querySelectorAll('.le-export-pl-cb:checked')].map(cb => cb.value);
  if (!selectedIds.length || !_leExportDrivePath) return;

  document.getElementById('le-export-submit').disabled = true;
  document.getElementById('le-export-errors').classList.add('hidden');
  const prog = document.getElementById('le-export-progress');
  prog.classList.remove('hidden');
  document.getElementById('le-export-progress-bar').style.width = '0%';
  document.getElementById('le-export-progress-label').textContent = 'Starting export…';

  try {
    const res = await fetch('/api/library/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ playlist_ids: selectedIds, drive_path: _leExportDrivePath }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      _leExportShowErrors([data.error || 'Export failed to start.']);
      document.getElementById('le-export-submit').disabled = false;
      return;
    }
    _leExportPoll(data.job_id);
  } catch (_) {
    _leExportShowErrors(['Could not reach server.']);
    document.getElementById('le-export-submit').disabled = false;
  }
}

function _leExportPoll(jobId) {
  if (_leExportPollTimer) clearInterval(_leExportPollTimer);
  _leExportPollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/library/export/${jobId}`);
      const job = await res.json().catch(() => ({}));
      const total = job.tracks_total || 0;
      const done  = job.tracks_done  || 0;
      const isTerminal = ['complete', 'complete_with_errors', 'failed'].includes(job.status);
      const pct   = total > 0 ? Math.round((done / total) * 100) : ((job.status === 'complete' || job.status === 'complete_with_errors') ? 100 : 0);
      document.getElementById('le-export-progress-bar').style.width = pct + '%';
      const track = job.current_track ? ` — ${job.current_track}` : '';
      document.getElementById('le-export-progress-label').textContent =
        job.status === 'complete' ? `Done — ${done} track${done === 1 ? '' : 's'} exported.` :
        job.status === 'complete_with_errors' ? `Done with warnings — ${done} track${done === 1 ? '' : 's'} exported.` :
        job.status === 'failed'   ? 'Export failed.' :
        `${done} / ${total}${track}`;

      if (isTerminal) {
        clearInterval(_leExportPollTimer);
        _leExportPollTimer = null;
        if (job.errors && job.errors.length) _leExportShowErrors(job.errors);
        if (job.status === 'complete') {
          showToast(`Export complete — ${done} track${done === 1 ? '' : 's'} on drive.`, 'success');
          document.getElementById('le-export-audit-action')?.classList.remove('hidden');
        }
        if (job.status === 'complete_with_errors') {
          showToast(`Export finished with warnings — ${done} track${done === 1 ? '' : 's'} processed.`, 'warning');
          document.getElementById('le-export-audit-action')?.classList.remove('hidden');
        }
        document.getElementById('le-export-submit').disabled = false;
      }
    } catch (_) { /* keep polling */ }
  }, 1000);
}

function _leExportShowErrors(errors) {
  const el = document.getElementById('le-export-errors');
  el.innerHTML = errors.map(e => `<div>${_leEsc(String(e))}</div>`).join('');
  el.classList.remove('hidden');
}

document.addEventListener('DOMContentLoaded', () => {
  setFableGearSpace(localStorage.getItem('fablegear-space') || 'record');

  /* Load the lighter playlist tree first; tracks load on demand */
  leLoadPlaylistsOnly();

  const search = document.getElementById('le-search');
  if (search) search.addEventListener('input', e => {
    _leSearchQuery = e.target.value.toLowerCase();
    leApplyFilters();
  });
  const createInput = document.getElementById('le-create-input');
  if (createInput) createInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      leSubmitCreate();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      leCloseCreate();
    }
  });
});


function _leEsc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}

/* Wire all drop zones once the DOM is confirmed ready */
document.addEventListener('DOMContentLoaded', () => {
  // Folder-pill zones (multi-path, capture-phase drag)
  setupFolderZone('audit-zone',     'audit-pills',     'audit-zone-text');
  setupFolderZone('process-zone',   'process-pills',   'process-zone-text');
  setupFolderZone('dupes-zone',     'dupes-pills',     'dupes-zone-text');
  setupFolderZone('normalize-zone', 'normalize-pills', 'normalize-zone-text');
  setupFolderZone('convert-zone',   'convert-pills',   'convert-zone-text');
  setupFolderZone('novelty-zone',   'novelty-pills',   'novelty-zone-text');
  setupFolderZone('import-zone',    'import-pills',    'import-zone-text');
  setupFolderZone('link-zone',      'link-pills',      'link-zone-text');
  setupFolderZone('rename-zone',    'rename-pills',    'rename-zone-text');
  setupFolderZone('organize-zone',  'organize-source-pills', 'organize-zone-text');
  // Single-path zones (visual feedback + Browse/drop, no pills)
  setupFolderZone('relocate-old-zone', 'relocate-old-pills', 'relocate-old-zone-text');
  setupSinglePathZone('relocate-new-zone',    'relocate-new');
  setupSinglePathZone('organize-target-zone', 'organize-target');
  setupSinglePathZone('novelty-dest-zone',    'novelty-dest');
  setupSinglePathZone('novelty-copy-to-zone', 'novelty-copy-to');
  setupAllDropZones();
  normPreviewSetupObserver();
  _initToolCheckpoints();

  // Pre-populate all music-folder tool zones with the configured music_root.
  // This means every tool (Audit, Duplicates, Normalize, etc.) opens pre-filled
  // so the user doesn't have to re-enter the library path every time.
  fetch('/api/config')
    .then(r => r.ok ? r.json() : null)
    .then(cfg => {
      if (!cfg || !cfg.music_root) return;
      const root = cfg.music_root;
      // Only pre-fill zones that are currently empty (don't overwrite user changes)
      const zones = [
        'audit-pills', 'process-pills', 'dupes-pills', 'normalize-pills',
        'convert-pills', 'rename-pills', 'organize-source-pills',
      ];
      zones.forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.querySelector('.folder-pill')) addFolderPill(id, root);
      });
    })
    .catch(() => {});
});


function leRunExportAudit() {
  if (!_leExportDrivePath) {
    showToast('No target drive selected for verification.', 'warning');
    return;
  }
  leCloseExportModal();
  runCommand(`/api/run/export-audit?mount=${encodeURIComponent(_leExportDrivePath)}`, 'Pioneer USB Export Audit', null, true);
}

