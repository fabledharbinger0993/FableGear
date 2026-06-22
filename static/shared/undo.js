/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / undo
   Undo Wizard panel: timeline, savepoints, trash recovery
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
  else if (tab === 'savepoints') undoLoadSavepoints();
  else if (tab === 'trash') undoLoadTrash();
}

/* ── Timeline ────────────────────────────────────────────────────────── */

async function undoLoadTimeline() {
  const list = document.getElementById('undo-timeline-list');
  list.innerHTML = '<div class="undo-loading">Loading…</div>';

  const tool = document.getElementById('undo-filter-tool')?.value || '';
  const state = document.getElementById('undo-filter-state')?.value || '';

  const params = new URLSearchParams();
  params.set('limit', '50');
  if (tool) params.set('tool', tool);
  if (state) params.set('state', state);

  try {
    const res = await fetch(`/api/undo/timeline?${params}`);
    if (!res.ok) throw new Error(await res.text());
    const jobs = await res.json();
    _renderTimeline(list, jobs);
  } catch (e) {
    list.innerHTML = '<div class="undo-empty">Could not load history</div>';
  }
}

const _TOOL_LABELS = {
  process: 'Tag Tracks', normalize: 'Normalize', organize: 'Organize',
  rename: 'Rename', prune: 'Prune', convert: 'Convert',
  import: 'Import', relocate: 'Relocate', dedupe: 'Dedup',
  audit: 'Audit', pipeline: 'Pipeline',
};

function _renderTimeline(container, jobs) {
  if (!jobs.length) {
    container.innerHTML = '<div class="undo-empty">No jobs found</div>';
    return;
  }
  container.innerHTML = '';
  jobs.forEach(job => {
    const item = document.createElement('div');
    item.className = 'undo-item';

    const stateClass = job.state === 'done' ? 'undo-state-done'
                     : job.state === 'failed' ? 'undo-state-failed'
                     : 'undo-state-running';

    const toolLabel = _TOOL_LABELS[job.tool] || job.tool || '?';
    const ts = _undoFormatTime(job.completed_at || job.started_at || job.dispatched_at);
    const summary = job.result_summary || '';
    const duration = job.duration_seconds ? `${Math.round(job.duration_seconds)}s` : '';
    const hasCheckpoint = !!job.checkpoint_path;

    item.innerHTML = `
      <div class="undo-item-top">
        <span class="undo-tool-pill">${_escHtml(toolLabel)}</span>
        <span class="undo-state ${stateClass}">${_escHtml(job.state)}</span>
        <span class="undo-ts">${_escHtml(ts)}</span>
        ${duration ? `<span class="undo-duration">${_escHtml(duration)}</span>` : ''}
      </div>
      ${summary ? `<div class="undo-item-summary">${_escHtml(summary)}</div>` : ''}
      <div class="undo-item-actions">
        <button type="button" class="btn btn-xs btn-secondary" onclick="undoViewJobDetail('${_escHtml(job.job_id)}')">Details</button>
        ${hasCheckpoint ? `<button type="button" class="btn btn-xs btn-neon" onclick="undoRestoreCheckpoint('${_escHtml(job.job_id)}','${_escHtml(job.checkpoint_path)}')">Restore checkpoint</button>` : ''}
      </div>
    `;
    container.appendChild(item);
  });
}

async function undoViewJobDetail(jobId) {
  try {
    const res = await fetch(`/api/undo/job/${encodeURIComponent(jobId)}`);
    if (!res.ok) throw new Error(await res.text());
    const detail = await res.json();
    const output = detail.output || detail.result_summary || 'No output recorded.';
    _undoShowDetail(detail.tool, output);
  } catch (e) {
    _undoShowDetail('Error', 'Could not load job detail.');
  }
}

function _undoShowDetail(tool, text) {
  const existing = document.getElementById('undo-detail-modal');
  if (existing) existing.remove();

  const overlay = document.createElement('div');
  overlay.id = 'undo-detail-modal';
  overlay.className = 'undo-detail-overlay';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const box = document.createElement('div');
  box.className = 'undo-detail-box';
  box.innerHTML = `
    <div class="undo-detail-head">
      <span>${_escHtml(_TOOL_LABELS[tool] || tool || 'Job Output')}</span>
      <button type="button" class="undo-panel-close" onclick="document.getElementById('undo-detail-modal').remove()">✕</button>
    </div>
    <pre class="undo-detail-body">${_escHtml(text)}</pre>
  `;
  overlay.appendChild(box);
  document.body.appendChild(overlay);
}

async function undoRestoreCheckpoint(jobId, checkpointPath) {
  if (!confirm('Restore this checkpoint? A backup of the current database will be created first.')) return;
  try {
    const res = await fetch('/api/undo/savepoint/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: checkpointPath }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Restore failed'); return; }
    alert(`Restored: ${data.restored}`);
  } catch (e) {
    alert('Restore failed — check console.');
  }
}

/* ── Savepoints ──────────────────────────────────────────────────────── */

async function undoLoadSavepoints() {
  const list = document.getElementById('undo-savepoints-list');
  list.innerHTML = '<div class="undo-loading">Loading…</div>';

  try {
    const res = await fetch('/api/undo/savepoints?limit=100');
    if (!res.ok) throw new Error(await res.text());
    const points = await res.json();
    _renderSavepoints(list, points);
  } catch (e) {
    list.innerHTML = '<div class="undo-empty">Could not load savepoints</div>';
  }
}

function _renderSavepoints(container, points) {
  if (!points.length) {
    container.innerHTML = '<div class="undo-empty">No savepoints found</div>';
    return;
  }
  container.innerHTML = '';
  points.forEach(sp => {
    const item = document.createElement('div');
    item.className = 'undo-item';
    item.innerHTML = `
      <div class="undo-item-top">
        <span class="undo-ts">${_escHtml(sp.display_time)}</span>
        <span class="undo-size">${sp.size_mb} MB</span>
      </div>
      <div class="undo-item-summary">${_escHtml(sp.filename)}</div>
      <div class="undo-item-actions">
        <button type="button" class="btn btn-xs btn-neon" onclick="_undoRestoreSavepoint('${_escHtml(sp.path)}')">Restore</button>
      </div>
    `;
    container.appendChild(item);
  });
}

async function _undoRestoreSavepoint(path) {
  if (!confirm('Restore this savepoint? A backup of the current database will be created first.')) return;
  try {
    const res = await fetch('/api/undo/savepoint/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Restore failed'); return; }
    alert(`Restored: ${data.restored}`);
    undoLoadSavepoints();
  } catch (e) {
    alert('Restore failed — check console.');
  }
}

/* ── Trash ───────────────────────────────────────────────────────────── */

async function undoLoadTrash() {
  const list = document.getElementById('undo-trash-list');
  list.innerHTML = '<div class="undo-loading">Loading…</div>';

  try {
    const res = await fetch('/api/undo/trash?limit=50');
    if (!res.ok) throw new Error(await res.text());
    const folders = await res.json();
    _renderTrashFolders(list, folders);
  } catch (e) {
    list.innerHTML = '<div class="undo-empty">Could not load trash</div>';
  }
}

function _renderTrashFolders(container, folders) {
  if (!folders.length) {
    container.innerHTML = '<div class="undo-empty">No pruned files in Trash</div>';
    return;
  }
  container.innerHTML = '';
  folders.forEach(f => {
    const item = document.createElement('div');
    item.className = 'undo-item';
    item.innerHTML = `
      <div class="undo-item-top">
        <span class="undo-ts">${_escHtml(f.display_time)}</span>
        <span class="undo-size">${f.file_count} files</span>
      </div>
      <div class="undo-item-actions">
        <button type="button" class="btn btn-xs btn-secondary" onclick="undoShowTrashFiles('${_escHtml(f.name)}')">Browse</button>
        <button type="button" class="btn btn-xs btn-neon" onclick="undoRestoreTrash('${_escHtml(f.name)}')">Restore all</button>
      </div>
    `;
    container.appendChild(item);
  });
}

async function undoShowTrashFiles(folderName) {
  try {
    const res = await fetch(`/api/undo/trash/${encodeURIComponent(folderName)}/files`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const lines = data.files.map(f => f.relative).join('\n');
    _undoShowDetail('Pruned Files', lines || 'No files.');
  } catch (e) {
    alert('Could not list files.');
  }
}

async function undoRestoreTrash(folderName) {
  if (!confirm('Move all files in this trash folder back to your music library?')) return;
  try {
    const res = await fetch('/api/undo/trash/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: folderName }),
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Restore failed'); return; }
    alert(`Restored ${data.restored} files.` + (data.errors.length ? `\n${data.errors.length} errors.` : ''));
    undoLoadTrash();
  } catch (e) {
    alert('Restore failed — check console.');
  }
}

/* ── Helpers ─────────────────────────────────────────────────────────── */

function _undoFormatTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  } catch { return iso; }
}

function _escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
