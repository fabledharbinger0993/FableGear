/* ════════════════════════════════════════════════════════════════════════
   FableGear — chop_shop / pipeline
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 2426-3512
   ──────────────────────────────────────────────────────────────────────── */


/* ══ Pipeline Wizard ═════════════════════════════════════════════════════════ */

function openPipelineWizard() {
  const backdrop = document.getElementById('pipeline-wizard-backdrop');
  backdrop.classList.remove('hidden');
  // Check for a saved checkpoint and offer to resume
  const ckpt   = _loadPipeCheckpoint();
  const banner = document.getElementById('pipe-resume-banner');
  if (ckpt && ckpt.steps && ckpt.steps.length > 0) {
    const nextIdx  = ckpt.completedIdx + 1;
    const nextStep = ckpt.steps[nextIdx];
    const nextName = nextStep ? ((PIPE_STEPS[nextStep.type] || {}).name || nextStep.type) : '—';
    const age      = Math.round((Date.now() - (ckpt.ts || 0)) / 60000);
    const ageText  = age < 1 ? 'just now' : age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`;
    document.getElementById('pipe-resume-text').textContent =
      `Checkpoint — resume at step ${nextIdx + 1} of ${ckpt.steps.length}: "${nextName}"`;
    document.getElementById('pipe-resume-sub').textContent =
      `Saved ${ageText} · ${ckpt.dryRun ? 'Dry Run' : 'Live Run'}`;
    if (banner) banner.classList.remove('hidden');
  } else {
    if (banner) banner.classList.add('hidden');
  }
  // Reset to phase 1
  document.getElementById('pipe-wiz-p1').style.opacity = '1';
  document.getElementById('pipe-wiz-p1').classList.remove('hidden');
  document.getElementById('pipe-wiz-p2').classList.add('hidden');
  document.getElementById('pipeline-wizard').classList.remove('wizard-wide');
  const _pwb = document.getElementById('pipeline-wizard');
  void _pwb.offsetWidth; _sbAnim(_pwb, 'sb-modal-in', '.28s');
}

function closePipelineWizard() {
  document.getElementById('pipeline-wizard-backdrop').classList.add('hidden');
  // Resolve any pending gate promise so it doesn't leak across sessions
  if (_pipeGateResolve) { _pipeGateResolve('stop'); _pipeGateResolve = null; }
}

/* Resume a previously interrupted pipeline run from its saved checkpoint */
function resumeFromCheckpoint() {
  const ckpt = _loadPipeCheckpoint();
  if (!ckpt || !ckpt.steps || ckpt.steps.length === 0) return;
  pipelineSteps = ckpt.steps.map(s => ({
    id: ++pipeUid, type: s.type, _config: s._config || {}, _draftConfig: s._config || {},
  }));
  pipelineRender();
  const banner = document.getElementById('pipe-resume-banner');
  if (banner) banner.classList.add('hidden');
  // Transition straight to Phase 2, focused on the next uncompleted step
  const resumeIdx = Math.min(ckpt.completedIdx + 1, pipelineSteps.length - 1);
  const p1  = document.getElementById('pipe-wiz-p1');
  const p2  = document.getElementById('pipe-wiz-p2');
  const wiz = document.getElementById('pipeline-wizard');
  p1.style.transition = 'opacity .2s';
  p1.style.opacity    = '0';
  setTimeout(() => {
    p1.classList.add('hidden'); p1.style.opacity = ''; p1.style.transition = '';
    wiz.classList.add('wizard-wide');
    pipeWizBuildConfigs();
    p2.classList.remove('hidden');
    p2.style.opacity    = '0';
    p2.style.transition = 'opacity .25s';
    document.getElementById('wiz-dry-run-2').checked       = ckpt.dryRun !== false;
    document.getElementById('wiz-confirm-steps-2').checked = true; // always confirm on resume
    requestAnimationFrame(() => requestAnimationFrame(() => {
      p2.style.opacity = '1';
      setTimeout(() => { p2.style.transition = ''; p2.style.opacity = ''; }, 280);
      pipeWizSelectStep(resumeIdx);
    }));
  }, 220);
}

function discardCheckpoint() {
  _clearPipeCheckpoint();
  const banner = document.getElementById('pipe-resume-banner');
  if (banner) banner.classList.add('hidden');
}

function pipeWizNext() {
  if (pipelineSteps.length === 0) {
    showToast('Add at least one step to the pipeline first.', 'warning');
    return;
  }
  const p1 = document.getElementById('pipe-wiz-p1');
  const p2 = document.getElementById('pipe-wiz-p2');
  const wiz = document.getElementById('pipeline-wizard');

  // Fade out phase 1
  p1.style.transition = 'opacity .2s';
  p1.style.opacity = '0';

  setTimeout(() => {
    p1.classList.add('hidden');
    p1.style.opacity = '';
    p1.style.transition = '';

    // Widen the modal
    wiz.classList.add('wizard-wide');

    // Build and show phase 2
    pipeWizBuildConfigs();
    p2.classList.remove('hidden');
    p2.style.opacity = '0';
    p2.style.transition = 'opacity .25s';

    // Sync checkboxes
    document.getElementById('wiz-dry-run-2').checked   = document.getElementById('wiz-dry-run').checked;
    document.getElementById('wiz-confirm-steps-2').checked = document.getElementById('wiz-confirm-steps').checked;

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        p2.style.opacity = '1';
        setTimeout(() => { p2.style.transition = ''; p2.style.opacity = ''; }, 280);
      });
    });
  }, 220);
}

function pipeWizBack() {
  const p1 = document.getElementById('pipe-wiz-p1');
  const p2 = document.getElementById('pipe-wiz-p2');
  const wiz = document.getElementById('pipeline-wizard');

  p2.style.transition = 'opacity .2s';
  p2.style.opacity = '0';

  setTimeout(() => {
    p2.classList.add('hidden');
    p2.style.opacity = '';
    p2.style.transition = '';
    wiz.classList.remove('wizard-wide');
    p1.classList.remove('hidden');
    p1.style.opacity = '0';
    p1.style.transition = 'opacity .25s';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        p1.style.opacity = '1';
        setTimeout(() => { p1.style.transition = ''; p1.style.opacity = ''; }, 280);
      });
    });
  }, 220);
}

function pipeWizRun() {
  if (pipelineSteps.length === 0) return;

  // Read draft from currently active panel before validating
  const active = pipelineSteps[_wizActiveIndex];
  if (active) _pipeWizReadDraft(active);

  // Validate all steps
  const incomplete = pipelineSteps.filter(s => !_stepIsReady(s));
  if (incomplete.length > 0) {
    // Highlight the first incomplete step in the sidebar
    const firstIdx = pipelineSteps.indexOf(incomplete[0]);
    pipeWizSelectStep(firstIdx);
    // Show tooltip over Run button
    const tip = document.getElementById('wiz-next-tooltip');
    tip.style.display = 'block';
    clearTimeout(tip._hideTimer);
    tip._hideTimer = setTimeout(() => { tip.style.display = 'none'; }, 5000);
    const hide = () => { tip.style.display = 'none'; document.removeEventListener('click', hide); };
    setTimeout(() => document.addEventListener('click', hide), 10);
    return;
  }

  // Commit all draft configs
  pipelineSteps.forEach(s => {
    s._config = s._draftConfig || {};
    _savePipeCfg(s.type, s._config);
  });

  const dryRun      = document.getElementById('wiz-dry-run-2').checked;
  const confirmMode = document.getElementById('wiz-confirm-steps-2').checked;
  closePipelineWizard();
  runPipeline(dryRun, confirmMode);
}

/* ── Count type occurrences for duplicate-step labeling ─────────────────── */
function _typeLabel(steps, step, i) {
  const siblings = steps.filter((s, j) => s.type === step.type && j <= i);
  const count = siblings.length;
  const def = PIPE_STEPS[step.type] || { name: step.type };
  return count > 1 ? `${def.name} (${count})` : def.name;
}

/* ── Required fields per step type ──────────────────────────────────────── */
const STEP_REQUIRED_FIELDS = {
  audit:      [],           // paths is optional
  process:    ['paths'],
  rename:     ['paths'],
  normalize:  ['paths'],
  duplicates: ['paths'],
  prune:      [],           // auto-uses CSV from prior duplicates step
  convert:    ['paths'],
  relocate:   ['old_root','new_root'],
  import:     ['paths'],
  link:       ['paths'],
  organize:   ['sources','target'],
  novelty:    ['sources','dest'],
};

function _stepIsReady(step) {
  const required = STEP_REQUIRED_FIELDS[step.type] || [];
  if (required.length === 0) return true;
  const cfg = step._draftConfig || {};
  return required.every(f => {
    const val = cfg[f];
    if (Array.isArray(val)) return val.length > 0 && val.some(s => s.trim() !== '');
    return (val || '').trim() !== '';
  });
}

function _wizUpdateProgress() {
  const total = pipelineSteps.length;
  const ready = pipelineSteps.filter(_stepIsReady).length;
  const pct   = total === 0 ? 0 : Math.round((ready / total) * 100);

  document.getElementById('wiz-progress-label').textContent = `${ready} / ${total} steps ready`;
  document.getElementById('wiz-progress-bar').style.width   = pct + '%';

  const allReady = ready === total && total > 0;
  const btn = document.getElementById('wiz-run-btn');
  if (allReady) {
    btn.style.opacity    = '1';
    btn.style.boxShadow  = '';
    btn.style.cursor     = 'pointer';
  } else {
    btn.style.opacity    = '0.45';
    btn.style.boxShadow  = '0 0 12px 2px rgba(239,68,68,.35)';
    btn.style.cursor     = 'default';
  }

  // Update sidebar ready indicators
  pipelineSteps.forEach((s, i) => {
    const si = document.getElementById(`pipe-wiz-si-${i}`);
    if (!si) return;
    const dot = si.querySelector('.wiz-ready-dot');
    if (!dot) return;
    const ready = _stepIsReady(s);
    dot.style.background = ready ? 'var(--safe)' : 'rgba(239,68,68,.6)';
    dot.title = ready ? 'Ready' : 'Needs configuration';
  });
}

let _wizActiveIndex = 0;

function pipeWizBuildConfigs() {
  const stack = document.getElementById('pipe-wiz-stack');
  stack.innerHTML = '';

  // Initialise _draftConfig for each step from saved or empty
  pipelineSteps.forEach(step => {
    if (!step._draftConfig) {
      step._draftConfig = step._config ? { ...step._config } : (_loadPipeCfg(step.type) || {});
    }
  });

  pipelineSteps.forEach((step, i) => {
    const def   = PIPE_STEPS[step.type] || { name: step.type, icon: '/static/FableGear-logo.png', desc: '' };
    const label = _typeLabel(pipelineSteps, step, i);
    const ready = _stepIsReady(step);

    const si = document.createElement('div');
    si.className = 'pipe-wiz-stack-item' + (i === 0 ? ' active' : '');
    si.id        = `pipe-wiz-si-${i}`;
    si.onclick   = () => pipeWizSelectStep(i);
    si.innerHTML = `
      <div class="pipe-step-num" style="width:18px;height:18px;font-size:.65rem;flex-shrink:0">${i + 1}</div>
      <img src="${def.icon}" style="width:15px;height:15px;object-fit:contain;flex-shrink:0">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.8rem">${label}</span>
      <span class="wiz-ready-dot" title="${ready ? 'Ready' : 'Needs config'}"
            style="width:7px;height:7px;border-radius:50%;flex-shrink:0;margin-left:4px;
                   background:${ready ? 'var(--safe)' : 'rgba(239,68,68,.6)'}"></span>`;
    stack.appendChild(si);
  });

  _wizActiveIndex = 0;
  pipeWizSelectStep(0);
  _wizUpdateProgress();
}

function pipeWizSelectStep(i) {
  _wizActiveIndex = i;
  document.querySelectorAll('.pipe-wiz-stack-item').forEach((el, j) => {
    el.classList.toggle('active', j === i);
  });

  const step  = pipelineSteps[i];
  if (!step) return;
  const def   = PIPE_STEPS[step.type] || { name: step.type, icon: '/static/FableGear-logo.png', desc: '' };
  const label = _typeLabel(pipelineSteps, step, i);
  const panel = document.getElementById('pipe-wiz-active-cfg');

  panel.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px">
      <img src="${def.icon}" style="width:24px;height:24px;object-fit:contain">
      <div>
        <div style="font-weight:700;font-size:1rem">${label}</div>
        <div style="font-size:.75rem;color:var(--text-dim)">${def.desc}</div>
      </div>
    </div>
    <hr style="border:none;border-top:1px solid var(--border);margin:4px 0 12px">
    ${_pipeWizConfigHTML(step, step._draftConfig)}`;

  // Wire up all inputs in the panel to update _draftConfig live
  panel.querySelectorAll('input[type=text], input[type=number], select, textarea').forEach(el => {
    el.addEventListener('input', () => {
      _pipeWizReadDraft(step);
      _wizUpdateProgress();
    });
  });
  panel.querySelectorAll('input[type=checkbox]').forEach(el => {
    el.addEventListener('change', () => {
      _pipeWizReadDraft(step);
      _wizUpdateProgress();
    });
  });
  // Wire drop zones — single-path inputs use setupDropZone; multi-path textareas use setupMultiDropZone
  panel.querySelectorAll('input[type=text]').forEach(setupDropZone);
  panel.querySelectorAll('textarea.pipe-cfg-input').forEach(setupMultiDropZone);
}

function _pipeWizConfigHTML(step, saved) {
  /* Renders config fields for the active step using data-cfg attributes.
     saved is the _draftConfig object (not localStorage). */
  const v  = (field, fallback) => (saved && saved[field] !== undefined && saved[field] !== '') ? saved[field] : (fallback || '');

  const pathRow = (field, label, placeholder, required = true) => `
    <div class="pipe-cfg-field">
      <label class="pipe-cfg-label">${label}${required ? ' <span style="color:var(--danger)">*</span>' : ''}</label>
      <div class="drop-wrap" style="flex:1">
        <input type="text" class="pipe-cfg-input" data-cfg="${field}"
               value="${v(field)}" placeholder="${placeholder}" style="width:100%">
        <span class="drop-badge">⤵ drop</span>
      </div>
    </div>`;

  /* Multi-path textarea — each line is a folder path, drop appends a new line */
  const multiPathRow = (field, label, placeholder, required = true) => {
    const rawVal     = saved && saved[field] !== undefined ? saved[field] : '';
    const displayVal = Array.isArray(rawVal) ? rawVal.join('\n') : (rawVal || '');
    return `
    <div class="pipe-cfg-field">
      <label class="pipe-cfg-label">${label}${required ? ' <span style="color:var(--danger)">*</span>' : ''}</label>
      <div class="drop-wrap" style="flex:1">
        <textarea class="pipe-cfg-input" data-cfg="${field}" rows="3"
                  placeholder="${placeholder}"
                  style="width:100%;resize:vertical;min-height:58px;font-family:inherit;line-height:1.5;">${displayVal}</textarea>
        <span class="drop-badge" style="top:8px">⤵ drop</span>
      </div>
      <div style="font-size:.72rem;color:var(--text-dim);margin-top:3px;padding-left:2px">One folder per line — drop to append.</div>
    </div>`;
  };

  const workersRow = (def = 4) => `
    <div class="pipe-cfg-field" style="max-width:180px">
      <label class="pipe-cfg-label">Workers</label>
      <select class="pipe-cfg-input" data-cfg="workers"
              style="background:var(--surface-hi);border:1px solid var(--border-hi);color:var(--text);border-radius:var(--radius);padding:8px 10px;font-size:.84rem;">
        ${[1,2,4,6,8].map(n => `<option value="${n}" ${parseInt(v('workers', def)) === n ? 'selected' : ''}>${n} worker${n>1?'s':''}</option>`).join('')}
      </select>
    </div>`;

  switch (step.type) {
    case 'audit':
      return multiPathRow('paths', 'Music folders (optional)', 'Choose or enter a music folder', false);

    case 'process': {
      const bpmMode = v('bpm_mode', 'passive');
      const keyMode = v('key_mode', 'passive');
      const normalizeLegacy = saved && saved.no_normalize !== undefined ? (saved.no_normalize ? 'off' : 'passive') : '';
      const normalizeMode = v('normalize_mode', normalizeLegacy || 'off');
      const enrichMode = v('enrich_mode', 'off');
      const renameMode = v('rename_mode', 'off');
      return multiPathRow('paths', 'Music folders', 'Choose or enter a music folder') + workersRow(4) + `
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px 14px">
          <div class="pipe-cfg-field">
            <label class="pipe-cfg-label">BPM mode</label>
            <select class="pipe-cfg-input" data-cfg="bpm_mode">
              <option value="passive" ${bpmMode === 'passive' ? 'selected' : ''}>Passive</option>
              <option value="aggressive" ${bpmMode === 'aggressive' ? 'selected' : ''}>Aggressive</option>
              <option value="off" ${bpmMode === 'off' ? 'selected' : ''}>Off</option>
            </select>
          </div>
          <div class="pipe-cfg-field">
            <label class="pipe-cfg-label">Key mode</label>
            <select class="pipe-cfg-input" data-cfg="key_mode">
              <option value="passive" ${keyMode === 'passive' ? 'selected' : ''}>Passive</option>
              <option value="aggressive" ${keyMode === 'aggressive' ? 'selected' : ''}>Aggressive</option>
              <option value="off" ${keyMode === 'off' ? 'selected' : ''}>Off</option>
            </select>
          </div>
          <div class="pipe-cfg-field">
            <label class="pipe-cfg-label">Normalize mode</label>
            <select class="pipe-cfg-input" data-cfg="normalize_mode">
              <option value="off" ${normalizeMode === 'off' ? 'selected' : ''}>Off</option>
              <option value="passive" ${normalizeMode === 'passive' ? 'selected' : ''}>Passive</option>
              <option value="aggressive" ${normalizeMode === 'aggressive' ? 'selected' : ''}>Aggressive</option>
            </select>
          </div>
          <div class="pipe-cfg-field">
            <label class="pipe-cfg-label">MusicBrainz mode</label>
            <select class="pipe-cfg-input" data-cfg="enrich_mode">
              <option value="off" ${enrichMode === 'off' ? 'selected' : ''}>Off</option>
              <option value="passive" ${enrichMode === 'passive' ? 'selected' : ''}>Passive</option>
              <option value="aggressive" ${enrichMode === 'aggressive' ? 'selected' : ''}>Aggressive</option>
            </select>
          </div>
          <div class="pipe-cfg-field">
            <label class="pipe-cfg-label">Rename mode</label>
            <select class="pipe-cfg-input" data-cfg="rename_mode">
              <option value="off" ${renameMode === 'off' ? 'selected' : ''}>Off</option>
              <option value="passive" ${renameMode === 'passive' ? 'selected' : ''}>Passive</option>
              <option value="aggressive" ${renameMode === 'aggressive' ? 'selected' : ''}>Aggressive</option>
            </select>
          </div>
        </div>`;
    }

    case 'rename':
      return multiPathRow('paths', 'Folders to rename', 'Choose or enter a music folder') + workersRow(1);

    case 'normalize':
      return multiPathRow('paths', 'Music folders', 'Choose or enter a music folder') + workersRow(4);

    case 'duplicates':
      return multiPathRow('paths', 'Folders to scan', 'Choose or enter a music folder') + workersRow(4);

    case 'prune':
      return `<p class="pipe-cfg-note" style="color:var(--text-muted);font-size:.84rem;">
        Prune reads the duplicate report produced by the Find Duplicates step above.
        No additional configuration needed — the report path is passed automatically.
      </p>`;

    case 'convert':
      return multiPathRow('paths', 'Folders to convert', 'Choose or enter a music folder') + `
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div class="pipe-cfg-field" style="flex:1">
            <label class="pipe-cfg-label">Target format <span style="color:var(--danger)">*</span></label>
            <select class="pipe-cfg-input" data-cfg="format"
                    style="background:var(--surface-hi);border:1px solid var(--border-hi);color:var(--text);border-radius:var(--radius);padding:8px 10px;font-size:.84rem;width:100%">
              ${['aiff','mp3','wav','flac'].map(f =>
                `<option value="${f}" ${v('format','aiff') === f ? 'selected' : ''}>${f.toUpperCase()}</option>`
              ).join('')}
            </select>
          </div>
          ${workersRow(4)}
        </div>`;

    case 'relocate':
      return pathRow('old_root', 'Old path prefix (where files were)', 'Old library path') +
             pathRow('new_root', 'New path prefix (where files are now)', 'New library path');

    case 'import':
      return multiPathRow('paths', 'Import from (folders)', 'Choose or enter a music folder');

    case 'link':
      return multiPathRow('paths', 'Library folders', 'Choose or enter a music folder');

    case 'organize':
      return multiPathRow('sources', 'Source folders', 'Choose or enter a music folder') +
             pathRow('target', 'Target (organized root)', 'Choose or enter a music folder') + `
        <div style="display:flex;gap:12px;flex-wrap:wrap">
          <div class="pipe-cfg-field" style="flex:1">
            <label class="pipe-cfg-label">Mode</label>
            <select class="pipe-cfg-input" data-cfg="mode"
                    style="background:var(--surface-hi);border:1px solid var(--border-hi);color:var(--text);border-radius:var(--radius);padding:8px 10px;font-size:.84rem;width:100%">
              <option value="assimilate" ${v('mode','assimilate')==='assimilate'?'selected':''}>Assimilate — move &amp; clean source</option>
              <option value="integrate"  ${v('mode','assimilate')==='integrate'?'selected':''}>Integrate — copy only</option>
            </select>
          </div>
          ${workersRow(1)}
        </div>`;

    case 'novelty':
      return multiPathRow('sources', 'Source drive(s) / folder(s)', 'Choose or enter a source drive') +
             pathRow('dest',   'Home library destination', 'Choose or enter a music folder') +
             `<div class="pipe-cfg-field">
                <label class="pipe-cfg-label">Comparison mode</label>
                <select class="pipe-cfg-input" data-cfg="match_mode"
                        style="background:var(--surface-hi);border:1px solid var(--border-hi);color:var(--text);border-radius:var(--radius);padding:8px 10px;font-size:.84rem;width:100%">
                  <option value="fingerprint" ${v('match_mode','fingerprint')==='fingerprint'?'selected':''}>Fingerprint confirm (safer)</option>
                  <option value="filename" ${v('match_mode','fingerprint')==='filename'?'selected':''}>Filename compare only (faster)</option>
                </select>
              </div>` +
             workersRow(4);

    default:
      return `<p class="pipe-cfg-note">No configuration needed for this step.</p>`;
  }
}

function _pipeWizReadDraft(step) {
  /* Read current values from the active panel into step._draftConfig */
  const panel    = document.getElementById('pipe-wiz-active-cfg');
  const get      = field => panel.querySelector(`[data-cfg="${field}"]`)?.value?.trim() || '';
  const getN     = (field, def) => parseInt(panel.querySelector(`[data-cfg="${field}"]`)?.value || def);
  // Read a multi-path textarea: split on newlines, trim, drop blanks
  const getLines = field => {
    const el = panel.querySelector(`[data-cfg="${field}"]`);
    if (!el) return [];
    return el.value.split('\n').map(s => s.trim()).filter(Boolean);
  };
  const getChecked = (field, def = false) => {
    const el = panel.querySelector(`[data-cfg="${field}"]`);
    return el ? el.checked : def;
  };

  const draft = {};
  switch (step.type) {
    case 'audit':
      draft.paths = getLines('paths'); break;
    case 'import':
      draft.paths = getLines('paths'); break;
    case 'link':
      draft.paths = getLines('paths'); break;
    case 'process':
      draft.paths        = getLines('paths');
      draft.workers      = getN('workers', 1);
      draft.bpm_mode = get('bpm_mode') || 'passive';
      draft.key_mode = get('key_mode') || 'passive';
      draft.normalize_mode = get('normalize_mode') || 'off';
      draft.enrich_mode = get('enrich_mode') || 'off';
      draft.rename_mode = get('rename_mode') || 'off';
      break;
    case 'rename':
      draft.paths   = getLines('paths');
      draft.workers = getN('workers', 1); break;
    case 'normalize':
      draft.paths   = getLines('paths');
      draft.workers = getN('workers', 1); break;
    case 'duplicates':
      draft.paths   = getLines('paths');
      draft.workers = getN('workers', 1); break;
    case 'prune':
      break; // no required fields
    case 'convert':
      draft.paths   = getLines('paths');
      draft.format  = get('format') || 'aiff';
      draft.workers = getN('workers', 1); break;
    case 'relocate':
      draft.old_root = get('old_root');
      draft.new_root = get('new_root'); break;
    case 'organize':
      draft.sources = getLines('sources');
      draft.target  = get('target');
      draft.mode    = get('mode') || 'assimilate';
      draft.workers = getN('workers', 1); break;
    case 'novelty':
      draft.sources    = getLines('sources');
      draft.dest       = get('dest');
      draft.match_mode = get('match_mode') || 'fingerprint';
      draft.workers    = getN('workers', 1); break;
  }
  step._draftConfig = draft;
  step._config      = draft;  // keep _config in sync for the runner
}


function _savePipeCfg(type, cfg) {
  try { localStorage.setItem(`sb_pipe_cfg_${type}`, JSON.stringify(cfg)); } catch(_) {}
}

function _loadPipeCfg(type) {
  try { return JSON.parse(localStorage.getItem(`sb_pipe_cfg_${type}`)) || {}; } catch(_) { return {}; }
}

/* ── Per-tool run checkpoint ───────────────────────────────────────────────
   Each long-running tool saves its config to localStorage when it starts.
   On page load, stale checkpoints become "Interrupted run" banners on the
   card, offering Resume (re-run same config) or Dismiss (start fresh).      */

const _TOOL_CKPT = key => `rb_ckpt_${key}`;

function _saveToolCkpt(toolKey, cfg) {
  try { localStorage.setItem(_TOOL_CKPT(toolKey), JSON.stringify({ ...cfg, ts: Date.now() })); }
  catch(_) {}
}
function _loadToolCkpt(toolKey) {
  try { return JSON.parse(localStorage.getItem(_TOOL_CKPT(toolKey))); }
  catch(_) { return null; }
}
function _clearToolCkpt(toolKey) {
  try { localStorage.removeItem(_TOOL_CKPT(toolKey)); } catch(_) {}
}

// Resume-function registry — populated by _showToolResumeBanner
const _toolResumeFns = {};

function _showToolResumeBanner(toolKey, cardId, resumeFn) {
  const ckpt = _loadToolCkpt(toolKey);
  const card = document.getElementById(cardId);
  if (!card || card.style.display === 'none') return;
  // Remove stale banner if checkpoint is gone
  const existing = card.querySelector('.tool-resume-banner');
  if (!ckpt) { existing?.remove(); return; }
  if (existing) return; // already showing

  const age      = Math.round((Date.now() - (ckpt.ts || 0)) / 60000);
  const ageText  = age < 1 ? 'just now' : age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`;
  const mainPaths = ckpt.paths || ckpt.sources || [];
  const pathsText = mainPaths.length ? mainPaths.join(', ') : 'previous paths';

  const banner = document.createElement('div');
  banner.className = 'tool-resume-banner';
  banner.innerHTML = `
    <div class="trb-icon">⏸</div>
    <div class="trb-text">
      <div class="trb-title">Interrupted run — ${ageText}</div>
      <div class="trb-paths">${pathsText}</div>
    </div>
    <button class="btn btn-neon trb-btn-resume" onclick="_resumeTool('${toolKey}')"
            title="Continue from where this run left off — files already done are skipped">Resume</button>
    <button class="btn trb-btn-restart" onclick="_restartToolFresh('${toolKey}', '${cardId}')"
            title="Discard saved progress and reprocess this folder from the beginning">Start Fresh</button>`;

  const form = card.querySelector('.card-form');
  if (form) form.prepend(banner);
  else card.appendChild(banner);
  _toolResumeFns[toolKey] = resumeFn;
}

function _resumeTool(toolKey) {
  const ckpt = _loadToolCkpt(toolKey);
  if (ckpt && _toolResumeFns[toolKey]) _toolResumeFns[toolKey](ckpt);
}

// Frontend checkpoint "toolKey" values that don't match the backend's CLI
// subcommand/checkpoint-tool name 1:1 — Normalize is process (with BPM/key
// detection switched off), not a separate CLI tool.
const _CKPT_BACKEND_TOOL = { normalize: 'process' };

// "Start Fresh": clears the local "interrupted run" banner AND tells the
// server to discard the saved checkpoint for this tool. Previously this only
// cleared the browser-side marker, so the *server* checkpoint survived and
// the next Run silently resumed from stale state anyway — exactly what this
// button is supposed to prevent.
async function _restartToolFresh(toolKey, cardId) {
  _clearToolCkpt(toolKey);
  document.getElementById(cardId)?.querySelector('.tool-resume-banner')?.remove();
  const backendTool = _CKPT_BACKEND_TOOL[toolKey] || toolKey;
  try {
    await fetch(`/api/checkpoint/reset?tool=${encodeURIComponent(backendTool)}`, { method: 'POST' });
  } catch (e) {
    console.warn('[checkpoint reset] failed:', e);
  }
}

function _populatePills(pillsId, paths) {
  const c = document.getElementById(pillsId);
  if (c) c.innerHTML = '';
  (paths || []).forEach(p => addFolderPill(pillsId, p));
}

// ── Resume functions — restore form state and re-run ─────────────────────────
function _resumeProcess(ckpt) {
  _populatePills('process-pills', ckpt.paths);
  const bpmMode = document.getElementById('process-bpm-mode');
  if (bpmMode) bpmMode.value = ckpt.bpm_mode || 'passive';
  const keyMode = document.getElementById('process-key-mode');
  if (keyMode) keyMode.value = ckpt.key_mode || 'passive';
  const enrichMode = document.getElementById('process-enrich-mode');
  if (enrichMode) enrichMode.value = ckpt.enrich_mode || 'off';
  const normalizeMode = document.getElementById('process-normalize-mode');
  if (normalizeMode) normalizeMode.value = ckpt.normalize_mode || 'off';
  const renameMode = document.getElementById('process-rename-mode');
  if (renameMode) renameMode.value = ckpt.rename_mode || 'off';
  document.getElementById('step-process')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runProcess();
}

function _resumeNormalize(ckpt) {
  _populatePills('normalize-pills', ckpt.paths);
  const w = document.getElementById('normalize-workers');
  if (w && ckpt.workers) w.value = ckpt.workers;
  document.getElementById('step-normalize')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runNormalize(true); // _skipConfirm = true
}

function _resumeConvert(ckpt) {
  _populatePills('convert-pills', ckpt.paths);
  const fmt = document.getElementById('convert-format');
  if (fmt && ckpt.format) fmt.value = ckpt.format;
  const w = document.getElementById('convert-workers');
  if (w && ckpt.workers) w.value = ckpt.workers;
  document.getElementById('step-convert')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runConvert();
}

function _resumeDuplicates(ckpt) {
  _populatePills('dupes-pills', ckpt.paths);
  const w = document.getElementById('dupes-workers');
  if (w && ckpt.workers) w.value = ckpt.workers;
  if (ckpt.matchMode) {
    const radio = document.querySelector(`input[name="dupes-match-mode"][value="${ckpt.matchMode}"]`);
    if (radio) { radio.checked = true; radio.dispatchEvent(new Event('change')); }
  }
  document.getElementById('step-duplicates')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runDuplicates();
}

function _resumeOrganize(ckpt) {
  _populatePills('organize-source-pills', ckpt.sources);
  const t = document.getElementById('organize-target');
  if (t && ckpt.target) t.value = ckpt.target;
  const mode = document.getElementById('organize-mode');
  if (mode && ckpt.mode) mode.value = ckpt.mode;
  const w = document.getElementById('organize-workers');
  if (w && ckpt.workers) w.value = ckpt.workers;
  const dr = document.getElementById('organize-dry-run');
  if (dr) dr.checked = !!ckpt.dryRun;
  document.getElementById('step-organize')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runOrganize();
}

function _resumeNovelty(ckpt) {
  _populatePills('novelty-pills', ckpt.sources);
  const d = document.getElementById('novelty-dest');
  if (d && ckpt.dest) d.value = ckpt.dest;
  const ct = document.getElementById('novelty-copy-to');
  if (ct && ckpt.copyTo) ct.value = ckpt.copyTo;
  const dr = document.getElementById('novelty-dry-run');
  if (dr) dr.checked = !!ckpt.dryRun;
  document.getElementById('step-novelty')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runNovelty();
}

function _resumeRename(ckpt) {
  _populatePills('rename-pills', ckpt.paths || (ckpt.path ? [ckpt.path] : []));
  const dr = document.getElementById('rename-dry-run');
  if (dr) dr.checked = !!ckpt.dryRun;
  document.getElementById('step-rename')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  runRename();
}

// ── Init — show banners for any stale checkpoints on page load ───────────────
function _initToolCheckpoints() {
  _showToolResumeBanner('process',    'step-process',    _resumeProcess);
  _showToolResumeBanner('normalize',  'step-normalize',  _resumeNormalize);
  _showToolResumeBanner('convert',    'step-convert',    _resumeConvert);
  _showToolResumeBanner('duplicates', 'step-duplicates', _resumeDuplicates);
  _showToolResumeBanner('organize',   'step-organize',   _resumeOrganize);
  _showToolResumeBanner('novelty',    'step-novelty',    _resumeNovelty);
  _showToolResumeBanner('rename',     'step-rename',     _resumeRename);
}

/* ── Pipeline checkpoint: survive interruptions and resume ────────────────── */
function _savePipeCheckpoint(steps, completedIdx, dryRun) {
  try {
    localStorage.setItem('sb_pipe_checkpoint', JSON.stringify({
      steps: steps.map(s => ({ type: s.type, _config: s._config || {} })),
      completedIdx,
      dryRun,
      ts: Date.now(),
    }));
  } catch(_) {}
}
function _loadPipeCheckpoint() {
  try { return JSON.parse(localStorage.getItem('sb_pipe_checkpoint')); } catch(_) { return null; }
}
function _clearPipeCheckpoint() {
  try { localStorage.removeItem('sb_pipe_checkpoint'); } catch(_) {}
}

function pipelineAddStep(type) {
  // Prune requires a preceding Find Duplicates step — it has no CSV without one
  if (type === 'prune') {
    const hasDuplicates = pipelineSteps.some(s => s.type === 'duplicates');
    if (!hasDuplicates) {
      showToast('Add a "Find Duplicates" step first — Prune reads its report.', 'warning');
      // Pulse the duplicates button to guide the user
      const dupBtn = [...document.querySelectorAll('#pipe-wiz-p1 .pipe-action-btn')]
        .find(b => (b.getAttribute('onclick') || '').includes("'duplicates'"));
      if (dupBtn) {
        dupBtn.classList.remove('pipe-added'); void dupBtn.offsetWidth; dupBtn.classList.add('pipe-added');
        dupBtn.addEventListener('animationend', () => dupBtn.classList.remove('pipe-added'), { once: true });
      }
      return;
    }
  }
  pipelineSteps.push({ id: ++pipeUid, type });
  pipelineRender();
  // Flash the clicked button
  const btn = [...document.querySelectorAll('#pipe-wiz-p1 .pipe-action-btn')]
    .find(b => (b.getAttribute('onclick') || '').includes(`'${type}'`));
  if (btn) {
    btn.classList.remove('pipe-added');
    void btn.offsetWidth;
    btn.classList.add('pipe-added');
    btn.addEventListener('animationend', () => btn.classList.remove('pipe-added'), { once: true });
  }
}

function pipelineRemoveStep(id) {
  pipelineSteps = pipelineSteps.filter(s => s.id !== id);
  pipelineRender();
}

function pipelineMoveStep(id, dir) {
  const i = pipelineSteps.findIndex(s => s.id === id);
  if (i < 0) return;
  const j = i + dir;
  if (j < 0 || j >= pipelineSteps.length) return;
  [pipelineSteps[i], pipelineSteps[j]] = [pipelineSteps[j], pipelineSteps[i]];
  pipelineRender();
}

function pipelineClear() {
  pipelineSteps = [];
  pipelineRender();
  document.getElementById('pipe-recommended-note').classList.add('hidden');
}

function pipelineLoadRecommended() {
  pipelineSteps = RECOMMENDED.map(type => ({ id: ++pipeUid, type }));
  pipelineRender();
  document.getElementById('pipe-recommended-note').classList.remove('hidden');
}

function pipelineRender() {
  const queue   = document.getElementById('pipeline-queue');
  const empty   = document.getElementById('pipe-empty-msg');
  const note    = document.getElementById('pipe-config-note');

  // Remove only step elements — leave pipe-empty-msg in the DOM so getElementById finds it next time
  queue.querySelectorAll('.pipe-step').forEach(el => el.remove());

  if (pipelineSteps.length === 0) {
    if (empty) empty.classList.remove('hidden');
    if (note) note.classList.add('hidden');
    return;
  }

  if (empty) empty.classList.add('hidden');
  if (note) note.classList.remove('hidden');

  pipelineSteps.forEach((step, i) => {
    const def = PIPE_STEPS[step.type] || { name: step.type, icon: '⚙', desc: '' };
    const el  = document.createElement('div');
    el.className = 'pipe-step';
    el.id = `pipe-step-${step.id}`;
    el.innerHTML = `
      <div class="pipe-step-num">${i + 1}</div>
      <div class="pipe-step-body">
        <div class="pipe-step-name"><img src="${def.icon}" style="width:16px;height:16px;object-fit:contain;vertical-align:middle;margin-right:5px">${def.name}</div>
        <div class="pipe-step-desc">${def.desc}</div>
      </div>
      <div class="pipe-step-controls">
        <button onclick="pipelineMoveStep(${step.id}, -1)" title="Move up" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button onclick="pipelineMoveStep(${step.id},  1)" title="Move down" ${i === pipelineSteps.length - 1 ? 'disabled' : ''}>↓</button>
        <button class="pipe-remove" onclick="pipelineRemoveStep(${step.id})" title="Remove">✕</button>
      </div>`;
    queue.appendChild(el);
  });
}

/* ── Pipeline confirm-gate state ──────────────────────────────────────────── */
let _pipeGateResolve = null;   // resolves with action string: 'finish' | 'redo' | 'skip' | 'stop'

function pipeGateAction(action) {
  document.getElementById('pipe-confirm-gate').style.display = 'none';
  if (_pipeGateResolve) { _pipeGateResolve(action); _pipeGateResolve = null; }
}
// Legacy aliases for any lingering calls
function _showPipeGate(succeeded, completedName, nextName, summaryLines) {
  /* Returns a Promise that resolves with an action string. */
  const gate     = document.getElementById('pipe-confirm-gate');
  const icon     = document.getElementById('pipe-gate-icon');
  const title    = document.getElementById('pipe-gate-title');
  const body     = document.getElementById('pipe-gate-body');
  const btnFinish = document.getElementById('pipe-btn-finish');
  const btnRedo   = document.getElementById('pipe-btn-redo');
  const btnSkip   = document.getElementById('pipe-btn-skip');
  const nextLabel = document.getElementById('pipe-gate-next-label');

  const nextText = nextName ? ` → ${nextName}` : '';

  if (succeeded) {
    gate.style.setProperty('--pipe-gate-border', 'rgba(52,211,153,.35)');
    gate.style.setProperty('--pipe-gate-bg',     'rgba(52,211,153,.05)');
    icon.textContent  = '✓';
    title.textContent = `"${completedName}" complete`;
    body.textContent  = summaryLines.length
      ? summaryLines.filter(l => l.trim()).slice(-5).join('  ·  ')
      : 'Step finished successfully.';
    btnFinish.textContent = nextName ? `Finish${nextText}` : 'Finish';
    btnFinish.style.display = '';
    btnRedo.style.display  = 'none';
    btnSkip.style.display  = 'none';
    nextLabel.textContent  = '';
  } else {
    gate.style.setProperty('--pipe-gate-border', 'rgba(239,68,68,.35)');
    gate.style.setProperty('--pipe-gate-bg',     'rgba(239,68,68,.05)');
    icon.textContent  = '⚠';
    title.textContent = `"${completedName}" did not complete`;
    body.textContent  = 'Step stopped or failed. Choose how to proceed:';
    btnFinish.style.display = 'none';
    btnRedo.style.display   = '';
    btnSkip.textContent     = nextName ? `Skip${nextText}` : 'Skip';
    btnSkip.style.display   = nextName ? '' : 'none';
    nextLabel.textContent   = '';
  }

  gate.style.display = '';
  return new Promise(resolve => { _pipeGateResolve = resolve; });
}

/* ── Run a single pipeline step via /api/run/pipeline ──────────────────────── */
async function _runOnePipelineStep(step, dryRun, capturedCsv) {
  /* Returns {exitCode, reportPath, outputLines} */
  const stepWithCsv = {
    ...step,
    config: (step.type === 'prune' && capturedCsv) ? { csv: capturedCsv } : (step.config || {}),
  };
  const resp = await fetch('/api/run/pipeline', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ dry_run: dryRun, steps: [stepWithCsv] }),
  });
  if (!resp.ok) throw new Error(await resp.text());

  const reader   = resp.body.getReader();
  const decoder  = new TextDecoder();
  let   buf      = '';
  let   reportPath = null;
  let   outputLines = [];
  let   exitCode = 0;
  let   inReport = false;

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
        const ev = JSON.parse(dataLine.slice(6));
        if (ev.step_start !== undefined) {
          /* already logged by caller */
        } else if (ev.step_end !== undefined) {
          exitCode = ev.exit_code;
        } else if (ev.line !== undefined) {
          const line = ev.line;
          if (line === 'FABLEGEAR_REPORT_BEGIN')       { inReport = true; }
          else if (line === 'FABLEGEAR_REPORT_END')    { inReport = false; }
          else if (line.startsWith('FABLEGEAR_PROGRESS: ')) {
            try { updateScanBar(JSON.parse(line.slice(19))); } catch(_) {}
          } else if (line.startsWith('FABLEGEAR_REPORT_PATH: ')) {
            reportPath = line.slice(22).trim();
          } else if (line.startsWith('FABLEGEAR_PHYSICAL_SCAN: ')) {
            appendLog(`  📁 Physical scan → ${line.slice(24).trim()}`, 'dim');
          } else {
            outputLines.push(line);
            appendLog(line, classifyLine(line));
          }
        } else if (ev.done) {
          exitCode = ev.exit_code || 0;
        }
      } catch (_) {}
    }
  }
  return { exitCode, reportPath, outputLines };
}

async function runPipeline(dryRun = true, confirmMode = false) {
  if (pipelineSteps.length === 0) {
    showToast('Add at least one step to the pipeline first.', 'warning');
    return;
  }
  if (isRunning) return;

  /* dryRun and confirmMode are passed in from pipeWizRun() */
  const label        = dryRun ? 'Pipeline — Dry Run (preview only)' : 'Running Pipeline';
  const total        = pipelineSteps.length;

  /* Reset step visual state */
  pipelineSteps.forEach(s => {
    const el = document.getElementById(`pipe-step-${s.id}`);
    if (el) el.className = 'pipe-step';
  });
  document.getElementById('pipe-confirm-gate').style.display = 'none';

  initLog(label);
  showScanBar(label);
  isRunning = true;
  setSpinner(true);
  setAllButtons(true);
  appendLog(`▸ ${label}${confirmMode ? '  (confirm between steps)' : ''}`, 'dim');
  appendLog('', 'dim');

  let reportBuffer = [];
  let capturedCsv  = null;   // last FABLEGEAR_REPORT_PATH from a duplicates step

  const finish = (exitCode, failedStep, stopped) => {
    isRunning = false;
    setSpinner(false);
    setAllButtons(false);
    finishScanBar();
    document.getElementById('pipe-confirm-gate').style.display = 'none';
    // Clean up any dangling gate promise
    if (_pipeGateResolve) { _pipeGateResolve('stop'); _pipeGateResolve = null; }
    // Clear checkpoint only when the full pipeline completed cleanly
    if (!failedStep && !stopped && exitCode === 0) _clearPipeCheckpoint();
    appendLog('', '');
    if (stopped) {
      appendLog('⏹ Pipeline stopped by user.', 'log-exit-fail');
    } else if (failedStep) {
      appendLog(`✗ Pipeline stopped — "${failedStep}" had an error.`, 'log-exit-fail');
    } else if (exitCode === 0) {
      appendLog(dryRun
        ? '✓ Preview complete. Uncheck Dry Run and run again to execute.'
        : '✓ Pipeline complete.', 'log-exit-ok');
    } else {
      appendLog(`✗ Exited with code ${exitCode}`, 'log-exit-fail');
    }
    if (reportBuffer.length > 0) {
      sessionReports[label] = { text: reportBuffer.join('\n'), reportPath: null };
      _addOrUpdateSummaryPill(label);
    }
  };

  /* ── CONFIRM MODE: run one step at a time with gate between each ─────────── */
  if (confirmMode) {
    let i = 0;
    while (i < pipelineSteps.length) {
      const s    = pipelineSteps[i];
      const def  = PIPE_STEPS[s.type] || { name: s.type, icon: '⚙', desc: '' };
      const step = { type: s.type, name: def.name, config: s._config || _loadPipeCfg(s.type) || {} };
      const el   = document.getElementById(`pipe-step-${s.id}`);

      if (el) el.className = 'pipe-step running';
      appendLog('', '');
      appendLog(`── Step ${i + 1} / ${total}: ${def.name} ──`, 'dim');

      let result;
      try {
        result = await _runOnePipelineStep(step, dryRun, capturedCsv);
      } catch (err) {
        if (el) el.className = 'pipe-step failed';
        appendLog('[Connection error] ' + err.message, 'error');
        finish(1, def.name, false);
        return;
      }

      if (el) el.className = result.exitCode === 0 ? 'pipe-step done' : 'pipe-step failed';
      if (result.reportPath) capturedCsv = result.reportPath;
      // Save a checkpoint so the run can be resumed from this point if interrupted
      if (result.exitCode === 0) _savePipeCheckpoint(pipelineSteps, i, dryRun);

      const succeeded = result.exitCode === 0;
      const nextStep  = pipelineSteps[i + 1];
      const nextName  = nextStep ? (PIPE_STEPS[nextStep.type] || { name: nextStep.type }).name : null;
      const summaryLines = result.outputLines.filter(l => l.trim());

      /* Show gate — returns 'finish' | 'redo' | 'skip' | 'stop' */
      const action = await _showPipeGate(succeeded, def.name, nextName, summaryLines);

      if (action === 'stop') {
        finish(succeeded ? 0 : result.exitCode, null, true);
        return;
      } else if (action === 'redo') {
        /* Re-do: mark step as pending again and restart the same index */
        if (el) el.className = 'pipe-step';
        appendLog(`↺ Re-doing "${def.name}"…`, 'dim');
        continue;   // i stays the same
      } else if (action === 'skip') {
        /* Skip: advance past this step */
        appendLog(`⤳ Skipped "${def.name}"`, 'dim');
        i++;
        continue;
      } else {
        /* finish: accept step result, advance to next */
        i++;
      }
    }
    finish(0, null, false);
    return;
  }

  /* ── AUTO MODE: send all steps at once (original behaviour) ─────────────── */
  const steps = pipelineSteps.map(s => ({
    type:   s.type,
    name:   (PIPE_STEPS[s.type] || {}).name || s.type,
    config: s._config || {},
  }));
  let stepIdMap = {};
  pipelineSteps.forEach((s, i) => { stepIdMap[i + 1] = `pipe-step-${s.id}`; });
  let inReport = false;

  try {
    const resp = await fetch('/api/run/pipeline', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ dry_run: dryRun, steps }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      appendLog('Pipeline error: ' + (err.error || resp.statusText), 'error');
      finish(1, null, false);
      return;
    }

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let   buf     = '';

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
          const ev = JSON.parse(dataLine.slice(6));
          if (ev.step_start !== undefined) {
            const el = document.getElementById(stepIdMap[ev.step_start]);
            if (el) el.className = 'pipe-step running';
            appendLog('', '');
            appendLog(`── Step ${ev.step_start} / ${ev.total_steps}: ${ev.step_name} ──`, 'dim');
          } else if (ev.step_end !== undefined) {
            const el = document.getElementById(stepIdMap[ev.step_end]);
            if (el) el.className = ev.exit_code === 0 ? 'pipe-step done' : 'pipe-step failed';
          } else if (ev.line !== undefined) {
            const line = ev.line;
            if (line === 'FABLEGEAR_REPORT_BEGIN')       { inReport = true; reportBuffer = []; }
            else if (line === 'FABLEGEAR_REPORT_END')    { inReport = false; }
            else if (line.startsWith('FABLEGEAR_PROGRESS: ')) {
              try { updateScanBar(JSON.parse(line.slice(19))); } catch(_) {}
            } else if (line.startsWith('FABLEGEAR_REPORT_PATH: ')) {
              /* silently capture */
            } else if (line.startsWith('FABLEGEAR_PHYSICAL_SCAN: ')) {
              /* silently capture */
            } else {
              if (inReport) reportBuffer.push(line);
              appendLog(line, classifyLine(line));
            }
          } else if (ev.done) {
            finish(ev.exit_code || 0, ev.failed_step || null, false);
          }
        } catch (_) {}
      }
    }
  } catch (err) {
    appendLog('[Connection error] ' + err.message, 'error');
    finish(1, null, false);
  }
}

function orgUpdateMode(val) {
  const badge = document.getElementById('organize-risk-badge');
  if (badge) {
    if (val === 'integrate') {
      badge.textContent = 'Copies Files';
      badge.className   = 'risk-badge safe';
    } else {
      badge.textContent = 'Moves Files';
      badge.className   = 'risk-badge warn';
    }
  }
}

function runOrganize() {
  const sources  = getFolderPaths('organize-source-pills');
  const target   = document.getElementById('organize-target').value.trim();
  const dryRun   = document.getElementById('organize-dry-run').checked;
  const workers  = document.getElementById('organize-workers')?.value || '1';
  const threshold = document.getElementById('organize-mix-threshold')?.value || '15';
  const mode     = document.getElementById('organize-mode')?.value || 'assimilate';
  if (!sources.length) { _flashNeedsInput('organize-source-pills'); showToast('Enter at least one source folder path.', 'warning'); return; }
  if (!target) { _flashNeedsInput('organize-target'); showToast('Enter a target library root folder path.', 'warning'); return; }
  const p = new URLSearchParams();
  sources.forEach(s => p.append('source', s));
  p.set('target', target);
  if (!dryRun) p.set('no_dry_run', '1');
  if (mode !== 'assimilate') p.set('mode', mode);
  if (parseInt(workers) > 1) p.set('workers', workers);
  if (threshold !== '15') p.set('mix_threshold', threshold);
  const modeLabel = mode === 'integrate' ? 'Integration (copies only, source untouched)' : 'Assimilation (move + clean source)';
  const label = dryRun ? `Organize — Dry Run · ${modeLabel}` : `Organize — ${modeLabel}`;
  if (!dryRun) {
    _saveToolCkpt('organize', { sources, target, mode, workers, dryRun: false });
    document.getElementById('step-organize')?.querySelector('.tool-resume-banner')?.remove();
  }
  const _orgTarget = target;
  const _orgDry    = dryRun;
  runCommand(`/api/run/organize?${p}`, label, (exitCode) => {
    if (exitCode === 0) {
      _clearToolCkpt('organize');
      if (!_orgDry) _promptSetLibraryRoot(_orgTarget);
    }
  });
}
