/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / dedupe
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 3513-4242
   ──────────────────────────────────────────────────────────────────────── */

/* ── Rekordbox Library Migration ───────────────────────────────────────────── */
function toggleMigrateSection() {
  const body = document.getElementById('migrate-db-body');
  const chevron = document.getElementById('migrate-db-chevron');
  if (!body || !chevron) return;
  
  const isHidden = body.style.display === 'none';
  body.style.display = isHidden ? 'block' : 'none';
  chevron.textContent = isHidden ? '▾' : '▸';
}

function migrateCbChanged() {
  const checkbox = document.getElementById('migrate-confirm-cb');
  const button = document.getElementById('btn-migrate-db');
  if (checkbox && button) {
    button.disabled = !checkbox.checked;
  }
}

async function runMigrateDb() {
  const target = document.getElementById('organize-target').value.trim();
  if (!target) {
    showToast('Enter a target drive path in the "Target — organised library root" field above.', 'warning');
    return;
  }
  
  const rbMsg = 'Rekordbox must be closed before migrating the database. Please quit Rekordbox and try again.';
  if (!checkRbBlock(rbMsg)) return;
  
  if (isRunning) return;
  
  initLog('Move Rekordbox Library to Drive');
  showScanBar('Move Rekordbox Library to Drive');
  isRunning = true;
  setSpinner(true);
  setAllButtons(true);
  appendLog('▸ Move Rekordbox Library to Drive', 'dim');
  appendLog('', 'dim');
  
  try {
    const resp = await fetch('/api/migrate-pioneer-db', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target }),
    });
    
    if (!resp.ok) {
      const errorText = await resp.text();
      appendLog(`✖ Error: ${errorText}`, 'danger');
      showToast('Migration failed. Check log for details.', 'error');
      return;
    }
    
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      
      for (const part of parts) {
        const dataLine = part.split('\n').find(l => l.startsWith('data: '));
        if (!dataLine) continue;
        
        try {
          const data = JSON.parse(dataLine.slice(6));
          if (data.line !== undefined) {
            appendLog(data.line);
          }
          if (data.done !== undefined && data.done) {
            appendLog('✓ Migration complete', 'safe');
            showToast('Database migration complete. Rekordbox will now use the drive location.', 'success');
            // Reset UI
            document.getElementById('migrate-confirm-cb').checked = false;
            migrateCbChanged();
          }
        } catch (e) {
          console.error('Parse error:', e);
        }
      }
    }
  } catch (err) {
    appendLog(`✖ Error: ${err.message}`, 'danger');
    showToast('Migration failed. Check log for details.', 'error');
  } finally {
    isRunning = false;
    setSpinner(false);
    setAllButtons(false);
  }
}

/* ── Novelty Scanner ───────────────────────────────────────────────────────── */
function runNovelty() {
  const sources = getFolderPaths('novelty-pills');
  const dest    = document.getElementById('novelty-dest').value.trim();
  const dryRun  = document.getElementById('novelty-dry-run').checked;
  if (!sources.length) { showToast('Add at least one source drive or folder.', 'warning'); return; }
  if (!dest)           { showToast('Enter a destination library path.', 'warning'); return; }
  const p = new URLSearchParams();
  sources.forEach(source => p.append('source', source));
  p.set('dest', dest);
  if (!dryRun) p.set('no_dry_run', '1');
  const label = dryRun
    ? 'Novelty Scan — Dry Run (nothing will be copied)'
    : 'Novelty Scan — Copying novel tracks to destination';
  if (!dryRun) {
    _saveToolCkpt('novelty', { sources, dest, dryRun: false });
    document.getElementById('step-novelty')?.querySelector('.tool-resume-banner')?.remove();
  }
  runCommand(`/api/run/novelty?${p}`, label,
    ec => { if (ec === 0) _clearToolCkpt('novelty'); });
}

/* ── Rename Files ──────────────────────────────────────────────────────────── */

function renameZoneAdd() {
  const input = document.getElementById('rename-zone-text');
  const path = input.value.trim();
  if (!path) { showToast('Enter a folder path.', 'warning'); return; }
  addFolderPill('rename-pills', path);
  input.value = '';
}

function runRename() {
  const paths = getFolderPaths('rename-pills');
  const dryRun = document.getElementById('rename-dry-run').checked;
  if (!paths.length) { showToast('Add a folder to rename files in.', 'warning'); return; }
  if (paths.length > 1) {
    showToast(`Rename processes one folder at a time — using "${paths[0].split('/').pop()}".`, 'neutral');
  }

  if (!dryRun) {
    runRenameWithPreflight(paths[0]);
    return;
  }

  _executeRename(paths[0], true);
}

function _executeRename(path, dryRun) {
  const p = new URLSearchParams();
  p.set('path', path);
  if (!dryRun) p.set('no_dry_run', '1');
  const label = dryRun
    ? 'Rename Files — Dry Run (preview only)'
    : 'Rename Files — Cleaning file names';
  if (!dryRun) {
    _saveToolCkpt('rename', { path, dryRun: false });
    document.getElementById('step-rename')?.querySelector('.tool-resume-banner')?.remove();
  }
  runCommand(`/api/run/rename?${p}`, label,
    ec => {
      if (ec === 0) {
        _clearToolCkpt('rename');
        if (dryRun) showToast('Dry run complete — uncheck "Dry Run" and click Clean File Names again to apply.', 'neutral');
      }
    });
}

async function runRenameWithPreflight(path) {
  let data;
  const p = new URLSearchParams();
  p.set('path', path);
  p.set('top_n', '5');
  p.set('sample_size', '100');

  try {
    const res = await fetch(`/api/rename/probe?${p}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Probe failed');
  } catch (err) {
    showToast('Rename preflight failed — ' + (err.message || err), 'error');
    return;
  }

  const candidates = Array.isArray(data.candidates) ? data.candidates : [];
  if (!candidates.length) {
    _executeRename(path, false);
    return;
  }

  openRenamePreflightModal(path, data, { executeRenameAfterApply: true, source: 'rename' });
}

function openRenamePreflightModal(path, data, options = {}) {
  renamePreflightState = {
    path,
    candidates: Array.isArray(data.candidates) ? data.candidates : [],
    sampleSize: data.sample_size || 100,
    topN: data.top_n || 5,
    executeRenameAfterApply: Boolean(options.executeRenameAfterApply),
    source: options.source || 'probe',
  };

  const subtitle = document.getElementById('rename-learn-subtitle');
  const summary = document.getElementById('rename-learn-summary');
  const list = document.getElementById('rename-learn-list');
  const applyBtn = document.getElementById('rename-learn-apply-btn');
  if (!subtitle || !summary || !list || !applyBtn) return;

  subtitle.textContent = `${renamePreflightState.topN} most ambiguous files from a stratified sample of ${renamePreflightState.sampleSize} tracks`;
  summary.textContent = renamePreflightState.executeRenameAfterApply
    ? 'Before a live rename, FableGear pauses on the riskiest filenames. You can confirm the exact filename for this file, teach a producer-attribution casing fix such as Ken@Work, or move truly unidentified tracks into the sibling “No-Name tracks for Tagging” folder. Confirmed-good filenames also feed the known artist and producer dictionaries for future runs.'
    : 'Use this probe to approve or correct the most ambiguous filenames before a full rename run. If the suggested filename is already right, leave it in place and apply it. Confirmed-good filenames feed the known artist and producer dictionaries for future runs.';
  applyBtn.textContent = renamePreflightState.executeRenameAfterApply ? 'Apply Decisions + Rename' : 'Apply Decisions';
  list.innerHTML = '';

  renamePreflightState.candidates.forEach((candidate, index) => {
    const row = document.createElement('div');
    row.className = 'rename-learn-row';
    row.dataset.sourcePath = candidate.source_path;
    row.dataset.proposedMix = candidate.proposed_mix || '';

    const why = (candidate.reasons || []).join(', ');
    row.innerHTML = `
      <div class="rename-learn-rowhead">
        <div>
          <div class="rename-learn-rank">Case ${index + 1}</div>
          <div class="rename-learn-source">${escapeHtml(candidate.source_name || candidate.source_path || '')}</div>
        </div>
        <div class="rename-learn-score">Ambiguity ${candidate.score ?? 0}</div>
      </div>
      <div class="rename-learn-proposed"><strong>Current proposal:</strong> <code>${escapeHtml(candidate.proposed_filename || '')}</code></div>
      <div class="rename-learn-why"><strong>Why it surfaced:</strong> ${escapeHtml(why || 'Complex filename')}</div>
      <div class="rename-learn-controls">
        <select class="rename-learn-select">
          <option value="manual">Confirm or correct exact filename</option>
          <option value="producer_alias">Teach producer-attribution casing</option>
          <option value="guess">Use current guess without teaching</option>
          <option value="quarantine">Move to No-Name tracks for Tagging</option>
        </select>
        <input class="rename-learn-input" type="text" value="${escapeHtmlAttr(candidate.proposed_filename || '')}" placeholder="Artist: Title.mp3">
      </div>
      <div class="rename-learn-note">Exact teaching is path-specific. Producer alias only affects that attribution token. Nothing here creates a blanket release-code rule.</div>
    `;

    const select = row.querySelector('.rename-learn-select');
    const input = row.querySelector('.rename-learn-input');
    const updateRowMode = () => {
      if (select.value === 'manual') {
        input.disabled = false;
        input.placeholder = 'Artist: Title.mp3';
        input.value = candidate.proposed_filename || '';
      } else if (select.value === 'producer_alias') {
        input.disabled = false;
        input.placeholder = 'Producer name with correct casing';
        input.value = extractProducerAliasToken(candidate.proposed_mix || '');
      } else {
        input.disabled = true;
      }
    };
    select.addEventListener('change', updateRowMode);
    updateRowMode();

    list.appendChild(row);
  });

  document.getElementById('rename-learn-backdrop')?.classList.add('open');
  document.getElementById('rename-learn-modal')?.classList.add('open');
}

function closeRenamePreflightModal() {
  document.getElementById('rename-learn-backdrop')?.classList.remove('open');
  document.getElementById('rename-learn-modal')?.classList.remove('open');
  renamePreflightState = null;
}

async function applyRenamePreflightAndRun() {
  if (!renamePreflightState) return;
  const list = document.getElementById('rename-learn-list');
  if (!list) return;

  const entries = [];
  for (const row of list.querySelectorAll('.rename-learn-row')) {
    const sourcePath = row.dataset.sourcePath;
    const proposedMix = row.dataset.proposedMix || '';
    const action = row.querySelector('.rename-learn-select')?.value || 'guess';
    const input = row.querySelector('.rename-learn-input');
    const targetName = input?.value.trim() || '';

    if (action === 'manual') {
      if (!targetName) {
        showToast('Every exact rename needs a filename — fill it in or switch that row to another action.', 'warning');
        input?.focus();
        return;
      }
      entries.push({ action: 'manual', source_path: sourcePath, target_name: targetName });
    } else if (action === 'producer_alias') {
      const token = extractProducerAliasToken(proposedMix);
      if (!targetName) {
        showToast('Producer alias fixes need the producer name with the correct casing.', 'warning');
        input?.focus();
        return;
      }
      if (!token) {
        showToast('This row has no clear producer attribution token — use exact filename instead.', 'warning');
        return;
      }
      entries.push({ action: 'producer_alias', source_path: sourcePath, token, canonical: targetName });
    } else if (action === 'quarantine') {
      entries.push({ action: 'quarantine', source_path: sourcePath });
    } else {
      entries.push({ action: 'skip', source_path: sourcePath });
    }
  }

  try {
    const res = await fetch('/api/rename/preflight/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: renamePreflightState.path, entries }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Could not apply rename decisions');
  } catch (err) {
    showToast('Could not apply rename decisions — ' + (err.message || err), 'error');
    return;
  }

  const path = renamePreflightState.path;
  const executeRenameAfterApply = renamePreflightState.executeRenameAfterApply;
  closeRenamePreflightModal();
  if (executeRenameAfterApply) {
    _executeRename(path, false);
    return;
  }

  openReportModal(
    'Rename Probe — Decisions Saved',
    [
      `Saved decisions for ${entries.length} probe item${entries.length === 1 ? '' : 's'}.`,
      '',
      'The rename tool will use those exact decisions on the next full run.',
      'Run Clean File Names with Dry Run off when you want to execute the rename pass.',
    ].join('\n'),
    null,
  );
}

function escapeHtml(text) {
  return String(text)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function escapeHtmlAttr(text) {
  return escapeHtml(text).replaceAll('\n', ' ').replaceAll('\r', ' ');
}

function extractProducerAliasToken(text) {
  return String(text || '')
    .replace(/\s+(remix|dub|edit|mix|rework|version|remaster|bootleg|re-edit|radio\s+edit|extended\s+mix)\s*$/i, '')
    .trim();
}

async function runRenameProbe() {
  const paths = getFolderPaths('rename-pills');
  if (!paths.length) { showToast('Add a folder to probe.', 'warning'); return; }

  const p = new URLSearchParams();
  p.set('path', paths[0]);
  p.set('top_n', '5');
  p.set('sample_size', '100');

  let data;
  try {
    const res = await fetch(`/api/rename/probe?${p}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Probe failed');
  } catch (err) {
    showToast('Rename probe failed — ' + (err.message || err), 'error');
    return;
  }

  if (!data.candidates || data.candidates.length === 0) {
    openReportModal(
      'Rename Probe — Most Ambiguous',
      [
        `Probe sample: ${data.sample_size} files`,
        `Top candidates shown: ${data.top_n}`,
        '',
        'No probe candidates found.',
        'This usually means the current parser already looks confident across the sampled files.',
      ].join('\n'),
      null,
    );
    return;
  }

  openRenamePreflightModal(paths[0], data, { executeRenameAfterApply: false, source: 'probe' });
}

/* ── Prune Duplicates ──────────────────────────────────────────────────────── */
let pruneGroups        = [];          // current page's groups
let pruneSelected      = new Set();   // file_paths checked for removal
let saState            = null;        // 'best' | 'lower' | null
let prunePage          = 0;
let prunePageSize      = 200;
let pruneTotalGroups   = 0;
let pruneTotalRemove   = 0;
let pruneTotalRemoveMb = 0;
let pruneCsvPath       = '';

async function loadPruneReport() {
  const csvPath = document.getElementById('prune-csv-path').value.trim();
  await _autoLoadDupeResults(csvPath);
}

async function _loadPrunePage(page) {
  let url = `/api/duplicates/load?page=${page}&per_page=${prunePageSize}`;
  if (pruneCsvPath) url += '&csv_path=' + encodeURIComponent(pruneCsvPath);

  const res  = await fetch(url);
  const data = await res.json();
  if (!res.ok) { showToast('Could not load report — ' + data.error, 'error'); return false; }

  pruneGroups        = data.groups;
  prunePage          = data.page;
  pruneTotalGroups   = data.total_groups;
  if (data.total_remove    != null) pruneTotalRemove   = data.total_remove;
  if (data.total_remove_mb != null) pruneTotalRemoveMb = data.total_remove_mb;

  _renderPruneGroups();
  _renderPrunePagination();
  _syncCheckboxes();
  _updateSaButtons();
  _updatePruneSummary();
  return true;
}

function _renderPruneGroups() {
  const container = document.getElementById('prune-groups');
  container.innerHTML = '';

  pruneGroups.forEach(g => {
    const wrap = document.createElement('div');
    wrap.className = 'prune-group';

    const keep    = g.entries.find(e => e.action === 'KEEP');
    const lowers  = g.entries.filter(e => e.action === 'REVIEW_REMOVE');
    const title   = keep ? keep.filename : ('Group ' + g.group_id);

    wrap.innerHTML = `<div class="prune-group-head">
      <span class="prune-group-title">${_esc(title)}</span>
      <span class="prune-group-count">${g.entries.length} copies</span>
    </div>`;

    if (keep)   wrap.appendChild(_makeRow(keep,  false));
    lowers.forEach(e => wrap.appendChild(_makeRow(e, true)));
    container.appendChild(wrap);
  });
}

function _makeRow(entry, isLower) {
  const row   = document.createElement('div');
  row.className = isLower ? 'prune-row-lower' : 'prune-row-keep';
  // Store path safely as a data attribute — avoids inline onclick string injection
  row.dataset.filePath = entry.file_path;

  const ext   = (entry.format_ext || '').replace('.','').toUpperCase();
  const lossless = ['AIFF','AIF','WAV','FLAC'].includes(ext);
  const fmtCls = lossless ? 'fmt-lossless' : 'fmt-lossy';

  const rankCls = { PN:'rank-pn', MIK:'rank-mik', RAW:'rank-raw' }[entry.rank] || 'rank-raw';
  const checked = pruneSelected.has(entry.file_path);
  const cbCls   = isLower ? 'prune-cb' : 'prune-cb keep-cb';

  row.innerHTML = `
    <input type="checkbox" class="${cbCls}" ${checked ? 'checked' : ''}>
    <span class="prune-star">${isLower ? '' : '★'}</span>
    <span class="prune-fname" title="${_esc(entry.file_path)}">${_esc(entry.filename)}</span>
    <span class="fmt-badge ${fmtCls}">${ext || '?'}</span>
    <span class="prune-meta">${entry.file_size_mb.toFixed(1)} MB</span>
    ${entry.bpm  ? `<span class="prune-meta">${entry.bpm} BPM</span>`  : ''}
    ${entry.key  ? `<span class="prune-meta">${entry.key}</span>` : ''}
    <span class="rank-badge ${rankCls}">${entry.rank}</span>
    ${entry.in_db         ? '<span class="prune-indb">in DB</span>'    : ''}
    ${!entry.exists_on_disk ? '<span class="prune-missing">missing</span>' : ''}
    <button class="prune-preview-btn">▶</button>`;

  // Attach event listeners using the data attribute — safe against any path content
  row.querySelector('input[type=checkbox]').addEventListener('change', function() {
    togglePruneFile(entry.file_path, this.checked);
  });
  row.querySelector('.prune-preview-btn').addEventListener('click', function() {
    previewFile(entry.file_path);
  });

  return row;
}

function togglePruneFile(path, checked) {
  checked ? pruneSelected.add(path) : pruneSelected.delete(path);
  saState = null;
  _updateSaButtons();
  _updatePruneSummary();
}

async function _fetchAllPaths() {
  let url = '/api/duplicates/remove-paths';
  if (pruneCsvPath) url += '?csv_path=' + encodeURIComponent(pruneCsvPath);
  const res  = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error);
  return data;
}

async function selectAllBest() {
  try {
    const data = await _fetchAllPaths();
    pruneSelected = new Set(data.keep_paths);
    saState = 'best';
    _syncCheckboxes();
    _updateSaButtons();
    _updatePruneSummary();
  } catch (err) { showToast('Could not load keep paths — ' + err, 'error'); }
}

async function selectAllLower() {
  try {
    const data = await _fetchAllPaths();
    pruneSelected = new Set(data.remove_paths);
    saState = 'lower';
    _syncCheckboxes();
    _updateSaButtons();
    _updatePruneSummary();
  } catch (err) { showToast('Could not load remove paths — ' + err, 'error'); }
}

function _renderPrunePagination() {
  const totalPages = Math.ceil(pruneTotalGroups / prunePageSize);
  const pg = document.getElementById('prune-pagination');
  if (totalPages <= 1) { pg.style.display = 'none'; } else { pg.style.display = 'flex'; }

  const start = prunePage * prunePageSize + 1;
  const end   = Math.min((prunePage + 1) * prunePageSize, pruneTotalGroups);
  document.getElementById('prune-page-info').textContent =
    `Groups ${start.toLocaleString()}–${end.toLocaleString()} of ${pruneTotalGroups.toLocaleString()}`;
  document.getElementById('prune-prev-btn').disabled = prunePage === 0;
  document.getElementById('prune-next-btn').disabled = prunePage >= totalPages - 1;

  _updateDupesStats();
}

/* ── Duplicate Tracks — phase switching & stats ────────────────────────── */

async function _autoLoadDupeResults(csvPath) {
  pruneCsvPath = csvPath || document.getElementById('prune-csv-path')?.value.trim() || '';
  prunePage    = 0;
  pruneSelected.clear();
  saState = null;

  try {
    const loaded = await _loadPrunePage(0);
    if (!loaded) return;
    await selectAllLower();

    // Switch card into review/prune phase
    document.getElementById('dupes-scan-phase').style.display    = 'none';
    document.getElementById('dupes-results-phase').style.display = 'block';
    const badge = document.getElementById('dupes-risk-badge');
    if (badge) { badge.textContent = 'Writes DB + Files'; badge.className = 'risk-badge danger'; }
    document.getElementById('step-duplicates')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) { showToast('Could not load results — ' + err, 'error'); }
}

function resetDupesScan() {
  document.getElementById('dupes-results-phase').style.display = 'none';
  document.getElementById('dupes-scan-phase').style.display    = '';
  const badge = document.getElementById('dupes-risk-badge');
  if (badge) { badge.textContent = 'Read-Only Scan'; badge.className = 'risk-badge safe'; }

  pruneCsvPath       = '';
  pruneTotalRemoveMb = 0;
  pruneSelected.clear();
  pruneGroups = [];
  const groupsEl = document.getElementById('prune-groups');
  if (groupsEl) groupsEl.innerHTML = '';
  const pgEl = document.getElementById('prune-pagination');
  if (pgEl) pgEl.style.display = 'none';
  _updatePruneSummary();
  _updateDupesStats();
}

function _updateDupesStats() {
  const grpEl = document.getElementById('dupes-stat-groups');
  const rmEl  = document.getElementById('dupes-stat-remove');
  const szEl  = document.getElementById('dupes-stat-size');
  if (!grpEl) return;
  grpEl.textContent = `${pruneTotalGroups.toLocaleString()} group${pruneTotalGroups !== 1 ? 's' : ''}`;
  rmEl.textContent  = `${pruneTotalRemove.toLocaleString()} to remove`;
  const gb = pruneTotalRemoveMb / 1024;
  szEl.textContent  = gb >= 1
    ? `${gb.toFixed(1)} GB recoverable`
    : `${Math.round(pruneTotalRemoveMb)} MB recoverable`;
}

function _syncCheckboxes() {
  document.querySelectorAll('#prune-groups input[type=checkbox]').forEach(cb => {
    const row  = cb.closest('[class^="prune-row"]');
    const path = row ? _rowPath(row) : null;
    if (path) cb.checked = pruneSelected.has(path);
  });
}

function _rowPath(row) {
  return row.dataset.filePath || null;
}

function _updateSaButtons() {
  document.getElementById('sa-best-btn') .classList.toggle('active-keep',  saState === 'best');
  document.getElementById('sa-lower-btn').classList.toggle('active-lower', saState === 'lower');
}

function _updatePruneSummary() {
  const n   = pruneSelected.size;
  const lbl = document.getElementById('prune-count-label');
  const sum = document.getElementById('prune-selected-summary');
  const btn = document.getElementById('btn-prune-start');

  lbl.textContent = n === 0 ? '0 files selected' : `${n} file${n > 1 ? 's' : ''} selected`;

  if (n === 0) {
    sum.innerHTML = 'Select files above to continue.';
    btn.disabled  = true;
  } else {
    sum.innerHTML = `<strong>${n}</strong> file${n > 1 ? 's' : ''} queued for removal`;
    btn.disabled  = false;
  }
}

async function previewFile(path) {
  try {
    await fetch('/api/open-file?path=' + encodeURIComponent(path));
  } catch(e) { showToast('Could not open file — ' + e, 'error'); }
}

/* ── Confirmation flow — 3 spatially separated steps ──────────────────────── */
// Each panel is at a different screen position.
// Each action button is at a different corner within its panel.
// User must physically move cursor between each step — no click-through.



function _showPruneStatus(msg, isError) {
  const el = document.getElementById('prune-status-msg');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.style.background    = isError ? 'rgba(239,68,68,.15)'  : 'rgba(34,197,94,.15)';
  el.style.border        = isError ? '1px solid rgba(239,68,68,.4)' : '1px solid rgba(34,197,94,.4)';
  el.style.color         = isError ? 'var(--danger)' : 'var(--safe)';
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function executePrune() {
  if (pruneSelected.size === 0) {
    showToast('Select files to remove first.', 'warning');
    return;
  }

  // Live RB check
  await refreshStatus();
  if (rbRunning) {
    showToast('Close RekordBox before pruning.', 'warning');
    return;
  }

  // Guard: another operation is already in progress
  if (isRunning) {
    _showPruneStatus('⚠ Another operation is still running. Wait for it to finish, then try again.', true);
    return;
  }

  // Hide any previous status before starting
  const statusEl = document.getElementById('prune-status-msg');
  if (statusEl) statusEl.style.display = 'none';

  const paths   = [...pruneSelected];
  const permanent = document.getElementById('prune-permanent-cb').checked;

  // Stage the paths server-side to avoid blowing the 256 KB header limit
  // when passing thousands of file paths as a query string.
  let token;
  try {
    const res = await fetch('/api/prune/stage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, permanent, csv_path: pruneCsvPath }),
    });
    const data = await res.json();
    if (!res.ok || !data.token) throw new Error(data.error || 'stage failed');
    token = data.token;
  } catch (e) {
    _showPruneStatus(`✗ Could not stage prune — ${e.message}`, true);
    return;
  }

  const verb   = permanent ? 'deleted permanently' : 'moved to Trash';
  const label  = permanent ? 'deleted' : 'moved to Trash';
  const url    = `/api/run/prune?token=${encodeURIComponent(token)}`;
  runCommand(url, `Prune — ${paths.length} duplicate${paths.length > 1 ? 's' : ''} ${verb}`, (exitCode) => {
    if (exitCode === 0) {
      pruneSelected.clear();
      _updatePruneSummary();
      _showPruneStatus(`✓ Prune complete — ${paths.length} file${paths.length > 1 ? 's' : ''} ${label}. Check the report for details.`, false);
    } else if (exitCode === 130) {
      _showPruneStatus('⚠ Prune cancelled. Re-open the report and re-stage if you still want to apply removals.', true);
    } else {
      _showPruneStatus('✗ Prune failed — see the log panel (View Output) for details.', true);
    }
  });
}



function _esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

