/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / modals
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 235-398
   ──────────────────────────────────────────────────────────────────────── */

/* ── Step completion report modal ─────────────────────────────────────────── */
// Session-only storage — cleared when the page reloads / server stops.
const sessionReports = {};

const STEP_PILL_LABELS = {
  'Audit — Library Health Check':                 'Step 1 Summary',
  'Audit — Database + Physical Scan':             'Step 1 Summary',
  'Tag Tracks — BPM & Key Detection':             'Step 2 Summary',
  'Preview Import — Dry Run':                     'Step 3 Preview',
  'Import — Writing Tracks to Database':          'Step 3 Summary',
  'Link Playlists — Matching Tracks to Folders':  'Step 4 Summary',
  'Normalize — Loudness to −8.0 LUFS':            'Step 5 Summary',
  'Find Duplicates — Acoustic Fingerprinting':    'Duplicates Summary',
  'Relocate — Updating File Paths in Database':   'Relocate Summary',
};

function _pillLabel(title) {
  if (STEP_PILL_LABELS[title]) return STEP_PILL_LABELS[title];
  // Dynamic labels: Organize, Convert, Novelty, Pipeline
  if (title.startsWith('Organize —'))      return 'Organize Summary';
  if (title.startsWith('Converting '))     return 'Convert Summary';
  if (title.startsWith('Novelty Scan —'))  return 'Novelty Summary';
  if (title.startsWith('Prune —'))         return 'Prune Summary';
  if (title.startsWith('Running Pipeline') || title.startsWith('Pipeline —')) return 'Pipeline Summary';
  if (title.startsWith('Homebrew —'))      return 'Brew Update';
  // Generic fallback
  return title.split(' — ')[0] + ' Summary';
}

function _addOrUpdateSummaryPill(title, animate) {
  const label     = _pillLabel(title);
  const container = document.getElementById('session-pills-container');
  if (!container) return;
  const existing = [...container.querySelectorAll('[data-pill-title]')]
    .find(el => el.dataset.pillTitle === title);
  if (existing) return;
  const pill = document.createElement('button');
  pill.className        = 'summary-pill';
  pill.dataset.pillTitle = title;
  pill.title            = 'Re-open summary: ' + label;
  pill.innerHTML        = `<span class="summary-pill-icon">📋</span>${label}`;
  pill.addEventListener('click', () => {
    const r = sessionReports[title];
    if (r) openReportModal(title, r.text, r.reportPath);
  });
  container.appendChild(pill);
}

/* ── Modal animation helpers ───────────────────────────────────────────────
   _sbAnim(el, keyframe, dur, cb) — runs keyframe then fires cb.
   _sbFadeBd(id, show, cb) — fades backdrop in/out.
   pulseModal kept as no-op so existing call-sites don't break.            */
function _sbAnim(el, kf, dur, cb) {
  if (!el) { if (cb) cb(); return; }
  el.style.animation = kf + ' ' + dur + ' cubic-bezier(.16,1,.3,1) forwards';
  el.addEventListener('animationend', () => { el.style.animation=''; if (cb) cb(); }, {once:true});
}
function _sbFadeBd(id, show, cb) {
  const bd = document.getElementById(id);
  if (!bd) { if (cb) cb(); return; }
  if (show) { bd.classList.remove('hidden'); _sbAnim(bd, 'sb-backdrop-in', '.2s', cb); }
  else      { _sbAnim(bd, 'sb-backdrop-out', '.18s', () => { bd.classList.add('hidden'); if (cb) cb(); }); }
}
function pulseModal(el) { /* no-op — animations now use _sbAnim */ }


function openReportModal(title, text, reportPath) {
  document.getElementById('rmod-title').textContent = title + ' — Complete';
  const pathEl = document.getElementById('rmod-save-path');
  if (reportPath) {
    pathEl.textContent = '▸ Report saved to:  ' + reportPath;
    pathEl.style.display = '';
  } else {
    pathEl.textContent = '▸ Session summary only — no file saved for this step.';
    pathEl.style.display = '';
  }
  document.getElementById('rmod-pre').textContent = text;
  _populateErrorActions(title);
  sessionReports[title] = { text, reportPath, ts: Date.now() };
  _sbFadeBd('report-modal-backdrop', true);
  const box = document.getElementById('report-modal');
  void box.offsetWidth;
  _sbAnim(box, 'sb-modal-in', '.28s');
}

function _populateErrorActions(scanTitle) {
  const s = _lastErrorSummary;
  const wrap = document.getElementById('rmod-error-actions');
  const btns = document.getElementById('rmod-ea-btns');
  if (!wrap || !btns) return;
  btns.innerHTML = '';
  let hasAny = false;

  // Open Quarantine folder
  if (s && s.corrupt && s.corrupt.length > 0 && s.quarantine_dir) {
    hasAny = true;
    const b = document.createElement('button');
    b.className = 'rmod-ea-btn quarantine';
    b.textContent = `Open Quarantine (${s.corrupt.length} file${s.corrupt.length === 1 ? '' : 's'})`;
    b.onclick = () => fetch(`/api/open-file?path=${encodeURIComponent(s.quarantine_dir)}`);
    btns.appendChild(b);
  }

  // Retry with force — only tag-write and other failures (not decode failures, those need conversion)
  const retryable = [
    ...((s && s.tag_failed) || []),
    ...((s && s.other)      || []),
  ];
  if (retryable.length > 0) {
    hasAny = true;
    const retryPaths = retryable.map(f => f.path).filter(Boolean);
    const b = document.createElement('button');
    b.className = 'rmod-ea-btn retry';
    b.textContent = `Retry ${retryPaths.length} failed track${retryPaths.length === 1 ? '' : 's'} with Force`;
    b.onclick = () => {
      closeReportModal(false);
      _runProcessRetry({
        paths:  retryPaths,
        bpm_mode: document.getElementById('process-bpm-mode')?.value || 'passive',
        key_mode: document.getElementById('process-key-mode')?.value || 'passive',
      });
    };
    btns.appendChild(b);
  }

  // Convert hint — decode failures need to be converted first
  if (s && s.decode_failed && s.decode_failed.length > 0) {
    hasAny = true;
    const b = document.createElement('button');
    b.className = 'rmod-ea-btn convert';
    b.textContent = `${s.decode_failed.length} need conversion first — open Convert tool`;
    b.onclick = () => {
      closeReportModal(false);
      document.getElementById('step-convert')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const parents = [...new Set(s.decode_failed.map(f => {
        const parts = (f.path || '').split('/'); parts.pop(); return parts.join('/');
      }).filter(Boolean))];
      parents.forEach(p => addFolderPill('convert-pills', p));
    };
    btns.appendChild(b);
  }

  wrap.style.display = hasAny ? 'flex' : 'none';
  // Don't clear _lastErrorSummary here — still needed if user re-opens the card
}

function closeReportModal(shrinkToPill) {
  const box   = document.getElementById('report-modal');
  const title = (document.getElementById('rmod-title')?.textContent||'').replace(' — Complete','');
  if (shrinkToPill) {
    _sbAnim(box, 'sb-modal-shrink', '.32s', () => {
      _sbFadeBd('report-modal-backdrop', false);
      _addOrUpdateSummaryPill(title, true);
    });
  } else {
    _sbAnim(box, 'sb-modal-out', '.18s', () => {
      _sbFadeBd('report-modal-backdrop', false);
      _addOrUpdateSummaryPill(title);
    });
  }
}

// Escape key handled in the global keydown listener below
