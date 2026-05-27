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
  }
}

/* ── Individual command runners ────────────────────────────────────────────── */
function runProcess() {
  const paths = getFolderPaths('process-pills');
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }

  const enrichChecked = document.getElementById('process-enrich-tags')?.checked;
  if (enrichChecked) {
    fetch('/api/config').then(r => r.json()).then(cfg => {
      if (!cfg.acoustid_api_key) {
        showToast('AcoustID API key not set — enrichment will be skipped. Add acoustid_api_key to your config.', 'warning');
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
      no_bpm: document.getElementById('process-no-bpm').checked,
      no_key: document.getElementById('process-no-key').checked,
    };
    _runProcessRetry(body);
    return;
  }

  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  if (document.getElementById('process-no-bpm').checked)  p.set('no_bpm', '1');
  if (document.getElementById('process-no-key').checked)  p.set('no_key', '1');
  if (document.getElementById('process-force').checked)   p.set('force',  '1');
  if (document.getElementById('process-enrich-tags')?.checked) p.set('enrich_tags', '1');
  p.set('no_normalize', '1');
  const el = document.getElementById('process-result');
  if (el) el.classList.add('hidden');
  _saveToolCkpt('process', {
    paths,
    no_bpm:      document.getElementById('process-no-bpm').checked,
    no_key:      document.getElementById('process-no-key').checked,
    force:       document.getElementById('process-force').checked,
    enrich_tags: document.getElementById('process-enrich-tags')?.checked || false,
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
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }
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
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams({ dry_run: '1' });
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/import?${p}`, 'Preview Import — Dry Run', null, true, false, null);
}

function runImport() {
  if (checkRbBlock('import-rb-block')) return;
  const paths = getFolderPaths('import-pills');
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/import?${p}`, 'Import — Writing Tracks to Database', null, true);
}

function runLink() {
  if (checkRbBlock('link-rb-block')) return;
  const paths = getFolderPaths('link-pills');
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  runCommand(`/api/run/link?${p}`, 'Link Playlists — Matching Tracks to Folders', null, true);
}

function runRelocate() {
  if (checkRbBlock('relocate-rb-block')) return;
  const oldPaths = getFolderPaths('relocate-old-pills');
  const new_ = document.getElementById('relocate-new').value.trim();
  if (!oldPaths.length) { showToast('Add at least one old path prefix.', 'warning'); return; }
  if (!new_) { showToast('Enter the new destination path.', 'warning'); return; }
  const p = new URLSearchParams({ new_root: new_ });
  oldPaths.forEach(old => p.append('old_root', old));
  runCommand(`/api/run/relocate?${p}`, 'Relocate — Updating File Paths in Database', null, true);
}

function runDuplicates() {
  const paths = getFolderPaths('dupes-pills');
  if (!paths.length) { showToast('Add at least one music folder first.', 'warning'); return; }
  const p = new URLSearchParams();
  paths.forEach(path => p.append('path', path));
  const workers = document.getElementById('dupes-workers')?.value || '4';
  if (parseInt(workers) > 1) p.set('workers', workers);
  // Match mode
  const matchMode = document.querySelector('input[name="dupes-match-mode"]:checked')?.value || 'exact';
  if (matchMode !== 'exact') p.set('match_mode', matchMode);
  // Fuzzy threshold (only relevant when fuzzy or all)
  if (matchMode === 'fuzzy' || matchMode === 'all') {
    const thresholdPct = parseInt(document.getElementById('fuzzy-threshold')?.value || '85');
    p.set('fuzzy_threshold', (thresholdPct / 100).toFixed(2));
  }
  _saveToolCkpt('duplicates', { paths, workers, matchMode });
  document.getElementById('step-duplicates')?.querySelector('.tool-resume-banner')?.remove();
  const title = 'Find Duplicates — Acoustic Fingerprinting';
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

function runConvert() {
  const paths = getFolderPaths('convert-pills');
  const format = document.getElementById('convert-format').value.trim();
  if (!paths.length) { showToast('Add at least one folder first.', 'warning'); return; }
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
  audit:      { name: 'Library Audit',      icon: '/static/icon-audit.png',          desc: 'DB snapshot + physical filesystem inventory' },
  process:    { name: 'Tag Tracks',         icon: '/static/icon-tag.png',            desc: 'Write BPM and Key into each file' },
  duplicates: { name: 'Find Duplicates',    icon: '/static/icon-find-duplicate.png', desc: 'Scan for files that are the same recording' },
  prune:      { name: 'Prune Duplicates',   icon: '/static/icon-prune.png',          desc: 'Remove copies found by Find Duplicates' },
  relocate:   { name: 'Fix Broken Paths',   icon: '/static/icon-move.png',           desc: 'Update RekordBox after files have moved' },
  import:     { name: 'Import Tracks',      icon: '/static/icon-import.png',         desc: 'Add new audio files to RekordBox database' },
  link:       { name: 'Link Playlists',     icon: '/static/icon-link.png',           desc: 'Connect tracks to playlists by folder name' },
  normalize:  { name: 'Balance Loudness',   icon: '/static/icon-normalize.png',      desc: 'Bring every track to the same volume' },
  convert:    { name: 'Convert Format',     icon: '/static/icon-convert.png',        desc: 'Change files to AIFF, MP3, WAV, or FLAC' },
  organize:   { name: 'Organize Library',   icon: '/static/icon-organizer.png',      desc: 'Move files into Artist / Album / Track' },
  novelty:    { name: 'Novelty Scan',       icon: '/static/icon-novelty.png',        desc: 'Copy unique tracks from source to home library' },
};

const RECOMMENDED = ['process','duplicates','prune','relocate','import','link','organize'];

let pipelineSteps = [];   // [{id, type}]
let pipeUid = 0;
