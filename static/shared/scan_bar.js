/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / scan_bar
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 1835-2132
   ──────────────────────────────────────────────────────────────────────── */


/* ── Scan bar ──────────────────────────────────────────────────────────────── */
let scanWarnings = 0;

function showScanBar(title) {
  scanWarnings = 0;
  // Reset interrupt/emergency buttons for fresh use
  const ib = document.getElementById('scan-bar-interrupt');
  ib.textContent = '⏸ Interrupt'; ib.disabled = false; ib.style.display = 'inline-block';
  const eb = document.getElementById('scan-bar-emergency');
  eb.textContent = '⚡ Emergency Stop'; eb.disabled = false; eb.style.display = 'inline-block';
  document.getElementById('sb-scanned').textContent      = '0';
  document.getElementById('sb-scanned-wrap').style.display = 'none';
  document.getElementById('sb-remaining').textContent    = '—';
  document.getElementById('sb-clean').textContent        = '0';
  document.getElementById('sb-edited').textContent       = '0';
  document.getElementById('sb-errors').textContent       = '0';
  document.getElementById('sb-warnings').textContent     = '0';
  document.getElementById('sb-quarantined').textContent  = '0';
  document.getElementById('sb-quarantine-wrap').style.display = 'none';
  document.getElementById('scan-bar-title').textContent = title;
  document.getElementById('scan-bar-spinner').classList.add('active');
  document.getElementById('scan-bar-dismiss').style.display = 'none';
  document.getElementById('scan-bar').classList.add('active');
  document.body.classList.add('scan-active');
  _syncToolModalScanState();
  _chopReadoutReset(title);
}
function updateScanBar(p) {
  _chopReadoutUpdate(p);
  if (p.scanned != null) {
    document.getElementById('sb-scanned').textContent = p.scanned.toLocaleString();
    document.getElementById('sb-scanned-wrap').style.display = '';
    return;
  }
  document.getElementById('sb-scanned-wrap').style.display = 'none';
  document.getElementById('sb-remaining').textContent = p.remaining.toLocaleString();
  document.getElementById('sb-clean').textContent     = p.clean.toLocaleString();
  document.getElementById('sb-edited').textContent    = p.edited.toLocaleString();
  document.getElementById('sb-errors').textContent    = p.errors.toLocaleString();
  document.getElementById('sb-warnings').textContent  = scanWarnings.toLocaleString();
  if (p.quarantined > 0) {
    document.getElementById('sb-quarantined').textContent = p.quarantined.toLocaleString();
    document.getElementById('sb-quarantine-wrap').style.display = '';
  }
  _mirrorScanBarToModal();
}
function finishScanBar() {
  document.getElementById('scan-bar-spinner').classList.remove('active');
  document.getElementById('scan-bar-interrupt').style.display = 'none';
  const eb = document.getElementById('scan-bar-emergency');
  eb.style.display = 'none'; eb.classList.remove('armed');
  _emergencyArmed = false;
  clearTimeout(_emergencyArmTimer);
  document.getElementById('scan-bar-dismiss').style.display = 'inline-block';
  _syncToolModalScanState();
  _chopReadoutFinish();
}
function dismissScanBar() {
  document.getElementById('scan-bar').classList.remove('active');
  document.body.classList.remove('scan-active');
  document.getElementById('chop-readout')?.classList.remove('running');
}

/* ── Chop Shop scanner-window readout ─────────────────────────────────────────
   Mirrors the live scan into the bottom-half readout (chop space only). All
   look-ups are guarded so these are harmless no-ops in the Record Room. */
function _chopSetText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function _chopReadoutReset(title) {
  _chopSetText('chop-readout-title', title || 'Running…');
  _chopSetText('chop-prog-pct', '0');
  _chopSetText('chop-tick-done', '0');
  _chopSetText('chop-tick-remaining', '—');
  _chopSetText('chop-stat-clean', '0');
  _chopSetText('chop-stat-edited', '0');
  _chopSetText('chop-stat-errors', '0');
  _chopSetText('chop-stat-warnings', '0');
  const fill = document.getElementById('chop-prog-fill'); if (fill) fill.style.width = '0%';
  document.getElementById('chop-readout')?.classList.add('running');
  document.getElementById('chop-readout-spinner')?.classList.add('active');
}
function _chopReadoutUpdate(p) {
  if (p.scanned != null) {
    _chopSetText('chop-readout-title', `Scanning library… ${p.scanned.toLocaleString()} checked`);
    return;
  }
  const clean = p.clean || 0, edited = p.edited || 0, errors = p.errors || 0, quar = p.quarantined || 0;
  const done = clean + edited + errors + quar;
  const remaining = p.remaining || 0;
  const total = done + remaining;
  const pct = total > 0 ? Math.round((done / total) * 100) : (done > 0 ? 100 : 0);
  _chopSetText('chop-tick-done', done.toLocaleString());
  _chopSetText('chop-tick-remaining', remaining.toLocaleString());
  _chopSetText('chop-prog-pct', String(pct));
  _chopSetText('chop-stat-clean', clean.toLocaleString());
  _chopSetText('chop-stat-edited', edited.toLocaleString());
  _chopSetText('chop-stat-errors', errors.toLocaleString());
  _chopSetText('chop-stat-warnings', scanWarnings.toLocaleString());
  const fill = document.getElementById('chop-prog-fill'); if (fill) fill.style.width = pct + '%';
}
function _chopReadoutFinish() {
  _chopSetText('chop-readout-title', 'Complete');
  document.getElementById('chop-readout-spinner')?.classList.remove('active');
  document.getElementById('chop-readout')?.classList.remove('running');
  const fill = document.getElementById('chop-prog-fill');
  if (fill && fill.style.width === '0%') fill.style.width = '100%';
}

/* ── Log panel ─────────────────────────────────────────────────────────────── */
function openLog(title) {
  document.getElementById('log-panel').classList.add('open');
  document.body.classList.add('log-open');
  document.getElementById('log-cmd-label').textContent = title;
  document.getElementById('log-output').innerHTML = '';
  document.getElementById('view-output-btn').style.display = 'none';
}
function closeLog() {
  document.getElementById('log-panel').classList.remove('open');
  document.body.classList.remove('log-open');
  if (document.getElementById('log-output').children.length > 0) {
    document.getElementById('view-output-btn').style.display = 'inline-block';
  }
}
function reopenLog() {
  document.getElementById('log-panel').classList.add('open');
  document.body.classList.add('log-open');
  document.getElementById('view-output-btn').style.display = 'none';
}
function setSpinner(on) {
  document.getElementById('log-spinner').classList.toggle('active', on);
}
const LOG_MAX_LINES = 800;
let _logScrollPending = false;
function appendLog(text, cls = '') {
  const out  = document.getElementById('log-output');
  const line = document.createElement('div');
  line.className = 'log-line ' + cls;
  line.textContent = text;
  const _logTool = document.getElementById('log-cmd-label')?.textContent?.trim() || '';
  const _logSev  = cls.includes('error') ? 'error' : cls.includes('warn') ? 'warn' : (cls.includes('success') || cls.includes('exit-ok')) ? 'safe' : 'info';
  out.appendChild(line);
  // Trim oldest lines to keep DOM size bounded (prevents browser freeze on large scans)
  while (out.children.length > LOG_MAX_LINES) {
    out.removeChild(out.firstChild);
  }
  // Debounce scroll via rAF — avoids forced reflow on every line
  if (!_logScrollPending) {
    _logScrollPending = true;
    requestAnimationFrame(() => {
      out.scrollTop = out.scrollHeight;
      _logScrollPending = false;
    });
  }
}
function classifyLine(text) {
  const t = text.toLowerCase();
  if (/^═+/.test(text) || /^─+/.test(text)) return 'header';
  if (t.includes('error') || t.includes('failed') || t.includes('exception')) return 'error';
  if (t.includes('warning') || t.includes('warn')) return 'warn';
  if (t.includes('✓') || t.includes('success') || t.includes('complete') || t.includes('ok')) return 'success';
  if (t.startsWith('  ')) return 'dim';
  return 'normal';
}

/* ── Log helpers ───────────────────────────────────────────────────────────── */
function initLog(title) {
  // Prepare the log buffer without showing the panel — scan bar is primary UI
  document.getElementById('log-cmd-label').textContent = title;
  document.getElementById('log-output').innerHTML = '';
  document.getElementById('view-output-btn').style.display = 'none';
}
function toggleLog() {
  const panel = document.getElementById('log-panel');
  if (panel.classList.contains('open')) {
    panel.classList.remove('open');
    document.body.classList.remove('log-open');
  } else {
    panel.classList.add('open');
    document.body.classList.add('log-open');
    document.getElementById('view-output-btn').style.display = 'none';
  }
}

/* ── SSE runner ────────────────────────────────────────────────────────────── */
function runCommand(url, logTitle, onDone, useBar = true, showPrefilter = false) {
  if (isRunning) {
    showToast('A tool is already running — wait for it to finish or click Interrupt.', 'warning');
    return;
  }
  initLog(logTitle);
  showScanBar(logTitle);
  isRunning = true;
  setSpinner(true);
  setAllButtons(true);
  appendLog(`▸ ${logTitle}`, 'dim');
  appendLog('', 'dim');

  // Report block capture — delimited by FABLEGEAR_REPORT_BEGIN / FABLEGEAR_REPORT_END
  let reportBuffer = [];
  let inReport = false;
  let capturedReportPath = null;   // set by FABLEGEAR_REPORT_PATH: line
  let capturedDuplicateCsv = null; // explicit CSV path for duplicate scans

  activeSource = new EventSource(url);

  activeSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.line !== undefined) {
      const line = data.line;

      // Detect report block boundaries
      if (line === 'FABLEGEAR_REPORT_BEGIN') { inReport = true; reportBuffer = []; return; }
      if (line === 'FABLEGEAR_REPORT_END')   { inReport = false; return; }
      if (inReport) {
        reportBuffer.push(line);
        if (logTitle === 'Find Duplicates — Acoustic Fingerprinting') {
          const match = line.match(/^Report saved to:\s+(.+\.csv)$/i);
          if (match) capturedDuplicateCsv = match[1].trim();
        }
      }

      // Smart-skip scan ticker — update a single status line, don't spam log
      if (line.startsWith('FABLEGEAR_SCAN_TICK: ')) {
        const n = parseInt(line.slice(20).trim(), 10);
        let tickEl = document.getElementById('fablegear-scan-tick');
        if (!tickEl) {
          const out = document.getElementById('log-output');
          tickEl = document.createElement('div');
          tickEl.id = 'fablegear-scan-tick';
          tickEl.className = 'log-line dim';
          out.appendChild(tickEl);
        }
        tickEl.textContent = `Scanning library… ${n.toLocaleString()} files checked`;
        return;
      }
      // Machine-readable report path — capture silently, don't echo to log
      if (line.startsWith('FABLEGEAR_REPORT_PATH: ')) {
        capturedReportPath = line.slice(22).trim();
        return;
      }
      // Physical scan JSON path — show subtle note in log
      if (line.startsWith('FABLEGEAR_PHYSICAL_SCAN: ')) {
        const physPath = line.slice(24).trim();
        appendLog(`  📁 Physical scan saved → ${physPath}`);
        return;
      }
      // Structured progress — update scan bar, don't echo to log
      if (line.startsWith('FABLEGEAR_PROGRESS: ')) {
        if (useBar) {
          try { updateScanBar(JSON.parse(line.slice(19))); } catch(_) {}
        }
        return;
      }
      // Error summary — stash for report modal actions + retry option on card
      if (line.startsWith('FABLEGEAR_ERROR_SUMMARY: ')) {
        try { _lastErrorSummary = JSON.parse(line.slice(24)); _showRetryOption(); } catch(_) {}
        return;
      }
      // Pre-filter summary — show in log as info line
      if (line.startsWith('FABLEGEAR_PREFILTER: ')) {
        if (showPrefilter) {
          try {
            const pf = JSON.parse(line.slice(20));
            const hasIndex = pf.db_tracks > 0 || pf.scan_tracks > 0;
            if (hasIndex) {
              appendLog(`Index: ${(pf.db_tracks||0).toLocaleString()} tracks from DB + ${(pf.scan_tracks||0).toLocaleString()} from scan index`, 'dim');
            }
            if (pf.skipped > 0) {
              appendLog(`Pre-filter: ${pf.candidates.toLocaleString()} of ${pf.total.toLocaleString()} need fingerprinting — ${pf.skipped.toLocaleString()} skipped (no matching key+BPM+duration)`, 'success');
            } else {
              appendLog(`Pre-filter: all ${pf.total.toLocaleString()} files queued — no index yet. Run Audit + Tag Tracks first to reduce this.`, 'warn');
            }
            if (pf.cached > 0) {
              appendLog(`Cache: ${pf.cached.toLocaleString()} fingerprints reused — only ${pf.to_compute.toLocaleString()} files need fpcalc`, 'success');
            } else {
              appendLog(`Cache: empty — all ${pf.to_compute.toLocaleString()} fingerprints will be computed fresh (subsequent runs will be much faster)`, 'dim');
            }
          } catch(_) {}
        }
        return;
      }
      // Count warnings for scan bar
      const t = line.toLowerCase();
      if (useBar && (t.includes('warning') || t.includes('warn'))) {
        scanWarnings++;
        document.getElementById('sb-warnings').textContent = scanWarnings.toLocaleString();
      }
      appendLog(line, classifyLine(line));
    }
    if (data.done) {
      activeSource.close();
      activeSource = null;
      isRunning = false;
      setSpinner(false);
      setAllButtons(false);
      if (useBar) finishScanBar();
      appendLog('', '');
      if (data.exit_code === 0) {
        appendLog('✓ Finished successfully', 'log-exit-ok');
      } else {
        appendLog(`✗ Exited with code ${data.exit_code}`, 'log-exit-fail');
      }
      // On success: auto-open report modal (user dismisses to pill).
      // On failure: store silently as pill.
      if (reportBuffer.length > 0) {
        const reportText = reportBuffer.join('\n');
        const effectiveReportPath = capturedDuplicateCsv || capturedReportPath;
        if (data.exit_code === 0) {
          openReportModal(logTitle, reportText, effectiveReportPath);
        } else {
          sessionReports[logTitle] = { text: reportText, reportPath: effectiveReportPath };
          _addOrUpdateSummaryPill(logTitle);
        }
      }
      if (onDone) onDone(data.exit_code);
    }
  };

  activeSource.onerror = () => {
    activeSource.close();
    activeSource = null;
    isRunning = false;
    setSpinner(false);
    setAllButtons(false);
    if (useBar) finishScanBar();
    appendLog('Connection error — check the server is running.', 'error');
    refreshStatus();
  };
}

/* ── Block helper ──────────────────────────────────────────────────────────── */
function checkRbBlock(msgId) {
  const msg = document.getElementById(msgId);
  if (rbRunning) {
    if (msg) msg.classList.add('visible');
    return true;
  }
  if (msg) msg.classList.remove('visible');
  return false;
}

/* ── Button disable / enable ───────────────────────────────────────────────── */
function setAllButtons(disabled) {
  document.querySelectorAll('.btn').forEach(b => b.disabled = disabled);
}

