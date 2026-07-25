/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / runners
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 2133-2425
   ──────────────────────────────────────────────────────────────────────── */

// ── Error summary from last Tag Tracks / Normalize run ──────────────────────
let _lastErrorSummary = null;   // populated by FABLEGEAR_ERROR_SUMMARY line

function _retryErroredCount() {
  if (!_lastErrorSummary) return 0;
  return (
    (_lastErrorSummary.decode_failed  || []).length +
    (_lastErrorSummary.tag_failed     || []).length +
    (_lastErrorSummary.other          || []).length
  );
}

function _showRetryOption() {
  const n = _retryErroredCount();
  const row = document.getElementById('process-retry-errored-row');
  const badge = document.getElementById('process-retry-count');
  if (!row) return;
  if (n > 0) {
    row.style.display = '';
    if (badge) badge.textContent = `${n} from last run`;
  } else {
    row.style.display = 'none';
    const cb = document.getElementById('process-retry-errored');
    if (cb) cb.checked = false;
    _syncProcessRetryDisabled();
  }
}

// Retry mode hardcodes --force --no-normalize server-side, so the per-effect
// force / normalize checkboxes have no effect while it's on — disable them so
// that's obvious instead of letting them silently do nothing.
function _syncProcessRetryDisabled() {
  const checked = !!document.getElementById('process-retry-errored')?.checked;
  ['process-bpm-mode', 'process-key-mode', 'process-normalize-mode', 'process-enrich-mode', 'process-rename-mode'].forEach(id => {
    const cb = document.getElementById(id);
    if (cb) cb.disabled = checked;
  });
}

function _initProcessRetryToggle() {
  const retryCb = document.getElementById('process-retry-errored');
  if (!retryCb) return;
  retryCb.addEventListener('change', _syncProcessRetryDisabled);
  _syncProcessRetryDisabled();
}
document.addEventListener('DOMContentLoaded', _initProcessRetryToggle);

/* ── Individual command runners ────────────────────────────────────────────── */

/**
 * Flash the folder-zone associated with the given pills/zone/input element ID
 * to make empty-field validation failures visually obvious.
 * Accepts: a folder-pills ID, a folder-zone ID, or any input inside a folder-zone.
 */
function _flashNeedsInput(targetId) {
  const el = document.getElementById(targetId);
  if (!el) return;
  let zone;
  if (el.classList.contains('folder-zone')) {
    zone = el;
  } else if (el.classList.contains('folder-pills')) {
    // Pills sit right after the zone as a sibling
    const prev = el.previousElementSibling;
    zone = prev?.classList.contains('folder-zone') ? prev
         : el.closest('.field')?.querySelector('.folder-zone');
  } else {
    // Text input or similar — walk up to the enclosing zone
    zone = el.closest('.folder-zone');
  }
  if (!zone) return;
  zone.classList.remove('zone-error'); // reset so re-triggering replays animation
  void zone.offsetWidth;               // force reflow
  zone.classList.add('zone-error');
  zone.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  setTimeout(() => zone.classList.remove('zone-error'), 2500);
}

function runProcess() {
  const paths = getFolderPaths('process-pills');
  if (!paths.length) { _flashNeedsInput('process-pills'); showToast('Add at least one music folder first.', 'warning'); return; }

  const enrichMode = document.getElementById('process-enrich-mode')?.value || 'off';
  if (enrichMode !== 'off') {
    fetch('/api/config').then(r => r.json()).then(cfg => {
      if (!cfg.acoustid_api_key_configured) {
        showToast('AcoustID API key not set — enrichment will be skipped. Add it in Settings.', 'warning');
      }
      _doRunProcess(paths);
    }).catch(() => _doRunProcess(paths));
    return;
  }
  _doRunProcess(paths);
}

function _doRunProcess(paths) {
  const retryOnly = document.getElementById('process-retry-errored')?.checked;
  if (retryOnly && _lastErrorSummary) {
    const retryPaths = [
      ...(_lastErrorSummary.decode_failed  || []).map(e => e.path),
      ...(_lastErrorSummary.tag_failed     || []).map(e => e.path),
      ...(_lastErrorSummary.other          || []).map(e => e.path),
    ].filter(Boolean);
    if (!retryPaths.length) {
      showToast('No retryable errored tracks from the last run.', 'warning');
      return;
    }
    const body = {
      paths:  retryPaths,
      bpm_mode: document.getElementById('process-bpm-mode')?.value || 'passive',
      key_mode: document.getElementById('process-key-mode')?.value || 'passive',
    };
    _runProcessRetry(body);
    return;
  }

  const bpmMode = document.getElementById('process-bpm-mode')?.value || 'passive';
  const keyMode = document.getElementById('process-key-mode')?.value || 'passive';
  const normalizeMode = document.getElementById('process-normalize-mode')?.value || 'off';
  const enrichMode = document.getElementById('process-enrich-mode')?.value || 'off';
  const renameMode = document.getElementById('process-rename-mode')?.value || 'off';
  if (
    bpmMode === 'off' &&
    keyMode === 'off' &&
    normalizeMode === 'off' &&
    enrichMode === 'off' &&
    renameMode === 'off'
  ) {
    showToast('All Tag Tracks modes are Off — enable at least one effect.', 'warning');
    return;
  }

  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  p.set('bpm_mode', bpmMode);
  p.set('key_mode', keyMode);
  p.set('normalize_mode', normalizeMode);
  p.set('enrich_mode', enrichMode);
  p.set('rename_mode', renameMode);
  const el = document.getElementById('process-result');
  if (el) el.classList.add('hidden');
  _saveToolCkpt('process', {
    paths,
    bpm_mode: bpmMode,
    key_mode: keyMode,
    normalize_mode: normalizeMode,
    enrich_mode: enrichMode,
    rename_mode: renameMode,
  });
  document.getElementById('step-process')?.querySelector('.tool-resume-banner')?.remove();
  runCommand(`/api/run/process?${p}`, 'Tag Tracks — BPM & Key Detection',
    ec => { if (ec === 0) _clearToolCkpt('process'); }, true, false);
}

function _runProcessRetry(body) {
  const label = `Tag Tracks — Retry ${body.paths.length} errored track${body.paths.length === 1 ? '' : 's'}`;
  if (isRunning) return;
  initLog(label);
  showScanBar(label);
  isRunning = true;
  setSpinner(true);
  setAllButtons(true);
  appendLog(`▸ ${label}`, 'dim');
  appendLog('', 'dim');

  let reportBuffer = [];
  let inReport = false;
  let capturedReportPath = null;

  fetch('/api/run/process-retry', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(resp => {
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    function pump() {
      return reader.read().then(({ done, value }) => {
        if (value) buf += decoder.decode(value, { stream: true });
        const events = buf.split('\n\n');
        buf = events.pop();
        for (const evt of events) {
          const dataLine = evt.split('\n').find(l => l.startsWith('data: '));
          if (!dataLine) continue;
          let data;
          try { data = JSON.parse(dataLine.slice(6)); } catch(_) { continue; }
          if (data.line !== undefined) {
            const line = data.line;
            if (line === 'FABLEGEAR_REPORT_BEGIN') { inReport = true; reportBuffer = []; continue; }
            if (line === 'FABLEGEAR_REPORT_END')   { inReport = false; continue; }
            if (inReport) { reportBuffer.push(line); continue; }
            if (line.startsWith('FABLEGEAR_REPORT_PATH: '))  { capturedReportPath = line.slice(22).trim(); continue; }
            if (line.startsWith('FABLEGEAR_PROGRESS: '))     { try { updateScanBar(JSON.parse(line.slice(19))); } catch(_){} continue; }
            if (line.startsWith('FABLEGEAR_ERROR_SUMMARY: ')) { try { _lastErrorSummary = JSON.parse(line.slice(24)); _showRetryOption(); } catch(_){} continue; }
            appendLog(line, classifyLine(line));
          }
          if (data.done) {
            isRunning = false; setSpinner(false); setAllButtons(false); finishScanBar();
            appendLog('');
            appendLog(data.exit_code === 0 ? '✓ Finished successfully' : `✗ Exited with code ${data.exit_code}`,
                      data.exit_code === 0 ? 'log-exit-ok' : 'log-exit-fail');
            if (reportBuffer.length > 0) {
              const txt = reportBuffer.join('\n');
              if (data.exit_code === 0) openReportModal(label, txt, capturedReportPath);
              else sessionReports[label] = { text: txt, reportPath: capturedReportPath };
            }
          }
        }
        if (!done) return pump();
      });
    }
    return pump();
  }).catch(err => {
    isRunning = false; setSpinner(false); setAllButtons(false); finishScanBar();
    appendLog(`[Connection error] ${err.message}`, 'error');
  });
}

function runNormalize(_skipConfirm = false) {
  const paths = getFolderPaths('normalize-pills');
  if (!paths.length) { _flashNeedsInput('normalize-pills'); showToast('Add at least one music folder first.', 'warning'); return; }
  if (!_skipConfirm) {
    const confirmed = confirm(
      'This will rewrite audio files.\n\n' +
      'Originals are renamed .bak during the operation and deleted only after the new file is verified.\n\n' +
      'Make sure you have an independent backup of your drive before proceeding.\n\n' +
      'Continue?'
    );
    if (!confirmed) return;
  }
  const workers = document.getElementById('normalize-workers')?.value || '4';
  const p = new URLSearchParams({ no_bpm: '1', no_key: '1' });
  paths.forEach(path => p.append('path', path));
  if (parseInt(workers) > 1) p.set('workers', workers);
  const el = document.getElementById('normalize-result');
  if (el) el.classList.add('hidden');
  _saveToolCkpt('normalize', { paths, workers });
  document.getElementById('step-normalize')?.querySelector('.tool-resume-banner')?.remove();
  runCommand(`/api/run/process?${p}`, 'Normalize — Loudness to −8.0 LUFS',
    ec => { if (ec === 0) _clearToolCkpt('normalize'); }, true, false);
}

function runImportDry() {
  const paths = getFolderPaths('import-pills');
  if (!paths.length) { _flashNeedsInput('import-pills'); showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams({ dry_run: '1' });
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/import?${p}`, 'Preview Import — Dry Run', null, true, false, null);
}

function runImport() {
  if (checkRbBlock('import-rb-block')) return;
  const paths = getFolderPaths('import-pills');
  if (!paths.length) { _flashNeedsInput('import-pills'); showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/import?${p}`, 'Import — Writing Tracks to Database', null, true);
}

function runLink() {
  if (checkRbBlock('link-rb-block')) return;
  const paths = getFolderPaths('link-pills');
  if (!paths.length) { _flashNeedsInput('link-pills'); showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/link?${p}`, 'Link Playlists — Matching Tracks to Folders', null, true);
}

function runRelocate() {
  if (checkRbBlock('relocate-rb-block')) return;
  const oldPaths = getFolderPaths('relocate-old-pills');
  const new_ = document.getElementById('relocate-new').value.trim();
  if (!oldPaths.length) { _flashNeedsInput('relocate-old-pills'); showToast('Add at least one old path prefix.', 'warning'); return; }
  if (!new_) { _flashNeedsInput('relocate-new'); showToast('Enter the new destination path.', 'warning'); return; }
  const p = new URLSearchParams({ new_root: new_ });
  oldPaths.forEach(old => p.append('old_root', old));
  runCommand(`/api/run/relocate?${p}`, 'Relocate — Updating File Paths in Database', null, true);
}

function runDuplicates() {
  const paths = getFolderPaths('dupes-pills');
  if (!paths.length) { _flashNeedsInput('dupes-pills'); showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  // Tier: quick = instant cached-hash match; deep = acoustic fpcalc.
  const scanMode = document.querySelector('input[name="dupes-scan-mode"]:checked')?.value || 'quick';
  p.set('scan_mode', scanMode);
  let matchMode = 'exact';
  if (scanMode === 'deep') {
    const workers = document.getElementById('dupes-workers')?.value || '4';
    if (parseInt(workers) > 1) p.set('workers', workers);
    matchMode = document.querySelector('input[name="dupes-match-mode"]:checked')?.value || 'exact';
    if (matchMode !== 'exact') p.set('match_mode', matchMode);
    // Fuzzy threshold (only relevant when fuzzy or all)
    if (matchMode === 'fuzzy' || matchMode === 'all') {
      const thresholdPct = parseInt(document.getElementById('fuzzy-threshold')?.value || '85');
      p.set('fuzzy_threshold', (thresholdPct / 100).toFixed(2));
    }
  }
  _saveToolCkpt('duplicates', { paths, scanMode, matchMode });
  document.getElementById('step-duplicates')?.querySelector('.tool-resume-banner')?.remove();
  const title = scanMode === 'quick'
    ? 'Find Duplicates — Quick (exact copies)'
    : 'Find Duplicates — Deep (acoustic fingerprint)';
  runCommand(`/api/run/duplicates?${p}`, title, (exitCode) => {
    if (exitCode === 0) {
      _clearToolCkpt('duplicates');
      const rp = sessionReports[title]?.reportPath;
      if (rp && /\.csv$/i.test(rp)) {
        const el = document.getElementById('prune-csv-path');
        if (el) el.value = rp;
        _autoLoadDupeResults(rp);
      }
    }
  }, true, true);
}

// Show/hide fuzzy threshold row based on match mode selection
function _initMatchModeUI() {
  const radios = document.querySelectorAll('input[name="dupes-match-mode"]');
  const row = document.getElementById('fuzzy-threshold-row');
  if (!row) return;
  radios.forEach(r => r.addEventListener('change', () => {
    const val = document.querySelector('input[name="dupes-match-mode"]:checked')?.value;
    row.style.display = (val === 'fuzzy' || val === 'all') ? 'block' : 'none';
  }));
}
document.addEventListener('DOMContentLoaded', _initMatchModeUI);

// Show/hide the Deep (acoustic) options based on the Quick vs Deep scan tier.
function _dupesUpdateScanMode() {
  const mode = document.querySelector('input[name="dupes-scan-mode"]:checked')?.value || 'quick';
  const deep = document.getElementById('dupes-deep-options');
  if (deep) deep.style.display = (mode === 'deep') ? '' : 'none';
}
document.addEventListener('DOMContentLoaded', _dupesUpdateScanMode);

function runConvert() {
  const paths = getFolderPaths('convert-pills');
  const format = document.getElementById('convert-format').value.trim();
  if (!paths.length) { _flashNeedsInput('convert-pills'); showToast('Add at least one folder first.', 'warning'); return; }
  if (!format) { showToast('Select a target format.', 'warning'); return; }
  const workers = document.getElementById('convert-workers')?.value || '4';
  const p = new URLSearchParams({ format });
  paths.forEach(path => p.append('path', path));
  if (parseInt(workers) > 1) p.set('workers', workers);
  _saveToolCkpt('convert', { paths, format, workers });
  document.getElementById('step-convert')?.querySelector('.tool-resume-banner')?.remove();
  runCommand(`/api/run/convert?${p}`, `Converting Audio Files to ${format.toUpperCase()}`,
    ec => { if (ec === 0) _clearToolCkpt('convert'); });
}

/* ── Pipeline Builder ──────────────────────────────────────────────────────── */

const PIPE_STEPS = {
  audit:      { name: 'Library Audit',      icon: '/static/icon-settings.png',          desc: 'DB snapshot + physical filesystem inventory' },
  process:    { name: 'Tag Tracks',         icon: '/static/icon-track-tagger.png',   desc: 'Write BPM and Key into each file' },
  rename:     { name: 'Rename Files',       icon: '/static/icon-renamer.png',        desc: 'Clean filenames using embedded metadata' },
  duplicates: { name: 'Find Duplicates',    icon: '/static/icon-deduper.png',        desc: 'Scan for files that are the same recording' },
  prune:      { name: 'Prune Duplicates',   icon: '/static/icon-deduper.png',          desc: 'Remove copies found by Find Duplicates' },
  relocate:   { name: 'Fix Broken Paths',   icon: '/static/icon-settings.png',           desc: 'Update RekordBox after files have moved' },
  import:     { name: 'Import Tracks',      icon: '/static/icon-queue.png',         desc: 'Add new audio files to RekordBox database' },
  link:       { name: 'Link Playlists',     icon: '/static/icon-settings.png',           desc: 'Connect tracks to playlists by folder name' },
  normalize:  { name: 'Balance Loudness',   icon: '/static/icon-normalizer.png',     desc: 'Bring every track to the same volume' },
  convert:    { name: 'Convert Format',     icon: '/static/icon-converter.png',      desc: 'Change files to AIFF, MP3, WAV, or FLAC' },
  organize:   { name: 'Organize Library',   icon: '/static/icon-organizer.png',      desc: 'Move files into Artist / Album / Track' },
  novelty:    { name: 'Novelty Scan',       icon: '/static/icon-novelty.png',        desc: 'Copy unique tracks from source to home library' },
};

const RECOMMENDED = ['process','rename','duplicates','prune','relocate','import','link','organize'];

let pipelineSteps = [];   // [{id, type}]
let pipeUid = 0;

/* ── Novelty Scanner ─────────────────────────────────────────────────────────
   Scans an external drive (or any source path) for tracks not already in the
   home library, then optionally copies them across.
   Source paths come from the staging queue if no pills are manually set.    */

function runNovelty() {
  if (stagingIsEmpty && stagingIsEmpty() && !getFolderPaths('novelty-pills').length) {
    _flashNeedsInput('novelty-pills');
    showToast('Add at least one source drive or folder, or stage items from the Record Room.', 'warning');
    return;
  }
  if (stagingIsEmpty && !stagingIsEmpty() && !getFolderPaths('novelty-pills').length) {
    stagingPopulatePills('novelty-pills');
  }
  const sources = getFolderPaths('novelty-pills');
  const dest    = document.getElementById('novelty-dest').value.trim();
  const dryRun  = document.getElementById('novelty-dry-run').checked;
  const matchMode = document.getElementById('novelty-match-mode')?.value || 'fingerprint';
  if (!sources.length) { _flashNeedsInput('novelty-pills'); showToast('Add at least one source drive or folder.', 'warning'); return; }
  if (!dest)           { _flashNeedsInput('novelty-dest'); showToast('Enter a destination library path.', 'warning'); return; }
  const p = new URLSearchParams();
  sources.forEach(source => p.append('source', source));
  p.set('dest', dest);
  p.set('match_mode', matchMode);
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

/* ── Rename Files ─────────────────────────────────────────────────────────── */

// renamePreflightState is declared in shared/state.js (these slices share one
// global scope). Re-declaring it here with `let` is a parse-time SyntaxError
// that aborts this whole file, silently undefining every runner. Do not re-add.

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
  if (!paths.length) { _flashNeedsInput('rename-pills'); showToast('Add a folder to rename files in.', 'warning'); return; }
  if (!dryRun && paths.length === 1) {
    runRenameWithPreflight(paths[0]);
    return;
  }
  if (!dryRun && paths.length > 1) {
    showToast('Multiple folders selected: running rename across all selected folders (preflight skipped).', 'neutral');
  }
  _executeRename(paths, dryRun);
}

function _executeRename(pathOrPaths, dryRun) {
  const paths = Array.isArray(pathOrPaths) ? pathOrPaths : [pathOrPaths];
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  if (!dryRun) p.set('no_dry_run', '1');
  const pathLabel = paths.length > 1 ? ` (${paths.length} folders)` : '';
  const label = dryRun
    ? `Rename Files — Dry Run${pathLabel} (preview only)`
    : `Rename Files — Cleaning file names${pathLabel}`;
  if (!dryRun) {
    _saveToolCkpt('rename', { path: paths[0], paths, dryRun: false });
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
    ? 'Before a live rename, FableGear pauses on the riskiest filenames. You can confirm the exact filename for this file, teach a producer-attribution casing fix such as Ken@Work, or move truly unidentified tracks into the sibling "No-Name tracks for Tagging" folder. Confirmed-good filenames also feed the known artist and producer dictionaries for future runs.'
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

async function runRenameProbe() {
  const paths = getFolderPaths('rename-pills');
  if (!paths.length) { _flashNeedsInput('rename-pills'); showToast('Add a folder to probe.', 'warning'); return; }

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

/* ── Staging queue helpers for tools ─────────────────────────────────────────
   Tools call stagingPopulatePills(pillsId) to pre-fill their source zones
   from the staging queue. Only called when the user hasn't manually added
   any paths (i.e. the zone contains only the library-root indicator pill).  */

function _useQueueIfEmpty(pillsId) {
  const container = document.getElementById(pillsId);
  if (!container) return;
  const manual = Array.from(container.querySelectorAll('.folder-pill:not(.library-pill)'));
  if (!manual.length && typeof stagingPopulatePills === 'function') {
    stagingPopulatePills(pillsId);
  }
}
