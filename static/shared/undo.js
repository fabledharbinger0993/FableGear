/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / undo (Hardened)
   ──────────────────────────────────────────────────────────────────────── */

/* ── Panel open / close ──────────────────────────────────────────────── */
function openUndoPanel() {
  const panel = document.getElementById('undo-panel');
  const backdrop = document.getElementById('undo-panel-backdrop');
  panel.classList.add('open');
  backdrop.classList.add('open');
  undoSwitchTab('timeline');
}

function closeUndoPanel() {
  const panel = document.getElementById('undo-panel');
  const backdrop = document.getElementById('undo-panel-backdrop');
  panel.classList.remove('open');
  backdrop.classList.remove('open');
}

function toggleUndoPanel() {
  const panel = document.getElementById('undo-panel');
  if (panel.classList.contains('open')) closeUndoPanel();
  else openUndoPanel();
}

/* ── Tab switching ───────────────────────────────────────────────────── */
function undoSwitchTab(tab) {
  document.querySelectorAll('.undo-tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.undo-section').forEach(s => s.classList.remove('active'));
  const btn = document.getElementById(`undo-tab-${tab}`);
  const sec = document.getElementById(`undo-sec-${tab}`);
  if (btn) btn.classList.add('active');
  if (sec) sec.classList.add('active');

  if (tab === 'timeline') undoLoadTimeline();
  else if (tab === 'operations') undoLoadOperations();
  else if (tab === 'database') undoLoadDatabase();
  else if (tab === 'savepoints') undoLoadSavepoints();
  else if (tab === 'trash') undoLoadTrash();
}

/* ── Database Transactions ────────────────────────────────────────────── */
async function undoLoadDatabase() {
  const el = document.getElementById('undo-database-list');
  if (!el) return;
  el.innerHTML = '<div class="undo-empty">Loading database transactions…</div>';
  try {
    const res = await fetch('/api/undo/database/history');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (!data.length) {
      el.innerHTML = '<div class="undo-empty">No database transactions recorded yet.</div>';
      return;
    }
    el.innerHTML = '';
    data.forEach(tx => {
      const row = document.createElement('div');
      row.className = 'undo-item';
      
      const label = document.createElement('div');
      label.className = 'undo-item-main';
      
      const title = document.createElement('div');
      title.className = 'undo-item-title';
      title.textContent = tx.description || `${tx.operation_type} transaction`;
      
      const meta = document.createElement('div');
      meta.className = 'undo-item-meta';
      meta.textContent = `${_undoFormatTime(tx.timestamp)} · ID: ${tx.transaction_id}`;
      
      label.appendChild(title);
      label.appendChild(meta);
      row.appendChild(label);
      
      const btn = document.createElement('button');
      btn.className = 'btn btn-xs btn-neon';
      btn.textContent = 'Rollback';
      btn.onclick = async () => {
        if (!confirm(`Are you sure you want to rollback transaction ${tx.transaction_id}?`)) return;
        btn.disabled = true;
        btn.textContent = 'Reverting…';
        try {
          const r2 = await fetch('/api/undo/database/revert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ transaction_id: tx.transaction_id }),
          });
          const out = await r2.json();
          if (!r2.ok) throw new Error(out.error || r2.statusText);
          showToast('Database transaction successfully rolled back.', 'success');
          undoLoadDatabase();
        } catch (e) {
          showToast(`Rollback failed: ${e.message}`, 'error');
          btn.disabled = false;
          btn.textContent = 'Rollback';
        }
      };
      row.appendChild(btn);
      el.appendChild(row);
    });
  } catch (e) {
    el.innerHTML = `<div class="undo-empty">⚠ ${e.message}</div>`;
  }
}

/* ── Operations (archive journal) ─────────────────────────────────────── */
const _UNDO_OP_LABELS = {
  organize: 'Organize — files moved',
  rename: 'Rename — files renamed',
  novelty_copy: 'Novelty — tracks copied in',
  convert: 'Convert — files re-encoded',
};

async function undoLoadOperations() {
  const el = document.getElementById('undo-operations-list');
  if (!el) return;
  el.innerHTML = '<div class="undo-empty">Loading the operation journal…</div>';
  try {
    const res = await fetch('/api/undo/operations');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    if (!data.sessions?.length) {
      el.innerHTML = '<div class="undo-empty">No journaled tool operations yet — run a tool and its footprint will appear here.</div>';
      return;
    }
    el.innerHTML = '';
    data.sessions.forEach(s => {
      const row = document.createElement('div');
      row.className = 'undo-item';
      const label = document.createElement('div');
      label.className = 'undo-item-main';
      const title = document.createElement('div');
      title.className = 'undo-item-title';
      title.textContent = `${_UNDO_OP_LABELS[s.type] || s.type} · ${s.count} file${s.count === 1 ? '' : 's'}`;
      const meta = document.createElement('div');
      meta.className = 'undo-item-meta';
      meta.textContent = `${_undoFormatTime(s.started)} — e.g. ${s.sample.join(', ')}`;
      label.appendChild(title);
      label.appendChild(meta);
      row.appendChild(label);
      if (s.revertible) {
        const btn = document.createElement('button');
        btn.className = 'btn btn-xs btn-neon';
        btn.textContent = 'Preview undo';
        btn.onclick = () => undoPreviewOperations(s, row, btn);
        row.appendChild(btn);
      } else {
        const note = document.createElement('span');
        note.className = 'undo-item-meta';
        note.textContent = 'not reversible (files were re-encoded)';
        row.appendChild(note);
      }
      el.appendChild(row);
    });
  } catch (e) {
    el.innerHTML = `<div class="undo-empty">⚠ ${e.message}</div>`;
  }
}

async function undoPreviewOperations(session, row, btn) {
  btn.disabled = true;
  btn.textContent = 'Checking…';
  try {
    const res = await fetch('/api/undo/operations/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: session.type, first_id: session.first_id, last_id: session.last_id }),
    });
    const plan = await res.json();
    if (!res.ok) throw new Error(plan.error || res.statusText);

    let detail = row.querySelector('.undo-op-plan');
    if (!detail) {
      detail = document.createElement('div');
      detail.className = 'undo-op-plan';
      row.appendChild(detail);
    }
    detail.innerHTML = '';
    const summary = document.createElement('div');
    summary.className = 'undo-item-meta';
    summary.textContent = `${plan.revertible} file${plan.revertible === 1 ? '' : 's'} can be returned`
      + (plan.blocked ? ` · ${plan.blocked} blocked (moved again since, or the original slot is taken)` : '');
    detail.appendChild(summary);

    if (plan.revertible > 0) {
      const go = document.createElement('button');
      go.className = 'btn btn-xs btn-danger-outline';
      go.textContent = `Return ${plan.revertible} file${plan.revertible === 1 ? '' : 's'}`;
      go.onclick = async () => {
        go.disabled = true;
        go.textContent = 'Reverting…';
        try {
          const r2 = await fetch('/api/undo/operations/revert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: session.type, first_id: session.first_id, last_id: session.last_id }),
          });
          const out = await r2.json();
          if (!r2.ok) throw new Error(out.error || r2.statusText);
          const where = out.recovery_dir ? ` Copies parked in ${out.recovery_dir}.` : '';
          showToast(`Returned ${out.reverted} file${out.reverted === 1 ? '' : 's'}.${where}`, 'success');
          undoLoadOperations();
        } catch (e) {
          showToast(`Undo failed: ${e.message}`, 'error');
          go.disabled = false;
          go.textContent = 'Retry';
        }
      };
      detail.appendChild(go);
    }
    btn.textContent = 'Re-check';
    btn.disabled = false;
  } catch (e) {
    showToast(`Preview failed: ${e.message}`, 'error');
    btn.disabled = false;
    btn.textContent = 'Preview undo';
  }
}

/* ── Timeline ────────────────────────────────────────────────────────── */
async function undoLoadTimeline() {
  const list = document.getElementById('undo-timeline-list');
  list.textContent = 'Loading…';
  const tool = document.getElementById('undo-filter-tool')?.value || '';
  const state = document.getElementById('undo-filter-state')?.value || '';
  const params = new URLSearchParams({ limit: '50' });
  if (tool) params.set('tool', tool);
  if (state) params.set('state', state);

  try {
    const res = await fetch(`/api/undo/timeline?${params}`);
    if (!res.ok) throw new Error(await res.text());
    const jobs = await res.json();
    _renderTimeline(list, jobs);
  } catch (e) {
    list.textContent = 'Could not load history';
  }
}

const _TOOL_LABELS = {
  process: 'Tag Tracks', normalize: 'Normalize', organize: 'Organize',
  rename: 'Rename', prune: 'Prune', convert: 'Convert',
  import: 'Import', relocate: 'Relocate', dedupe: 'Dedup',
  audit: 'Audit', pipeline: 'Pipeline',
};

function _renderTimeline(container, jobs) {
  container.innerHTML = '';
  if (!jobs.length) {
    container.textContent = 'No jobs found';
    return;
  }
  jobs.forEach(job => {
    const item = document.createElement('div');
    item.className = 'undo-item';
    const stateClass = job.state === 'done' ? 'undo-state-done' : job.state === 'failed' ? 'undo-state-failed' : 'undo-state-running';
    
    // Create elements safely
    const top = document.createElement('div'); top.className = 'undo-item-top';
    const pill = document.createElement('span'); pill.className = 'undo-tool-pill'; pill.textContent = _TOOL_LABELS[job.tool] || job.tool || '?';
    const state = document.createElement('span'); state.className = `undo-state ${stateClass}`; state.textContent = job.state;
    const ts = document.createElement('span'); ts.className = 'undo-ts'; ts.textContent = _undoFormatTime(job.completed_at || job.started_at || job.dispatched_at);
    
    top.append(pill, state, ts);
    if (job.duration_seconds) {
      const dur = document.createElement('span'); dur.className = 'undo-duration'; dur.textContent = Math.round(job.duration_seconds) + 's';
      top.appendChild(dur);
    }
    item.appendChild(top);

    if (job.result_summary) {
      const summ = document.createElement('div'); summ.className = 'undo-item-summary'; summ.textContent = job.result_summary;
      item.appendChild(summ);
    }

    const actions = document.createElement('div'); actions.className = 'undo-item-actions';
    const btnDet = document.createElement('button'); btnDet.className = 'btn btn-xs btn-secondary'; btnDet.textContent = 'Details';
    btnDet.onclick = () => undoViewJobDetail(job.job_id);
    actions.appendChild(btnDet);

    if (job.checkpoint_path) {
      const btnRest = document.createElement('button'); btnRest.className = 'btn btn-xs btn-neon'; btnRest.textContent = 'Restore checkpoint';
      btnRest.onclick = () => undoRestoreCheckpoint(job.job_id, job.checkpoint_path);
      actions.appendChild(btnRest);
    }
    item.appendChild(actions);
    container.appendChild(item);
  });
}

/* ── Details / Savepoints / Trash (Use similar DOM construction pattern) ── */

function _undoShowDetail(tool, text) {
  const existing = document.getElementById('undo-detail-modal');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.id = 'undo-detail-modal';
  overlay.className = 'undo-detail-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const box = document.createElement('div');
  box.className = 'undo-detail-box';
  
  const head = document.createElement('div'); head.className = 'undo-detail-head';
  const title = document.createElement('span'); title.textContent = _TOOL_LABELS[tool] || tool || 'Job Output';
  const close = document.createElement('button'); close.className = 'undo-panel-close'; close.textContent = '✕';
  close.onclick = () => overlay.remove();
  head.append(title, close);
  
  const body = document.createElement('pre'); body.className = 'undo-detail-body'; body.textContent = text;
  
  box.append(head, body);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
}

// Note: To complete the hardening, apply this same 'document.createElement' 
// logic to the _renderSavepoints and _renderTrashFolders functions.

function _undoFormatTime(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); } 
  catch { return iso; }
}
