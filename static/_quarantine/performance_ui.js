import { DeckManager } from './deck_control.js';

function fmtTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function toast(msg, kind = 'success') {
  if (typeof window.showToast === 'function') {
    window.showToast(msg, kind);
    return;
  }
  console.log(`[${kind}] ${msg}`);
}

function selectedTrackFromRow() {
  const row = document.querySelector('.le-track-row.selected');
  if (!row) return null;
  const trackId = row.dataset.id;
  if (!trackId) return null;

  const title = row.querySelector('.le-col-title')?.textContent?.trim() || '';
  const artist = row.querySelector('.le-col-artist')?.textContent?.trim() || '';
  const bpmText = row.querySelector('.le-col-bpm')?.textContent?.trim() || '';
  const key = row.querySelector('.le-key-badge')?.textContent?.trim() || row.querySelector('.le-col-key')?.textContent?.trim() || '';
  const bpm = Number.parseFloat(bpmText);

  return {
    id: trackId,
    title,
    artist,
    bpm: Number.isFinite(bpm) ? bpm : null,
    key: key && key !== '-' && key !== '--' ? key : null,
  };
}

async function loadSelectedToDeck(deckId) {
  const t = selectedTrackFromRow();
  if (!t) {
    toast('Select a track row first.', 'error');
    return;
  }
  try {
    const streamUrl = `/api/library/tracks/${encodeURIComponent(t.id)}/stream`;
    await DeckManager.loadTrackToDeck(deckId, streamUrl, t);
    toast(`Loaded ${t.title || 'track'} to Deck ${deckId}.`, 'success');
  } catch (err) {
    toast(`Could not load Deck ${deckId}.`, 'error');
    console.error(err);
  }
}

function bindOnce() {
  const panel = document.getElementById('li-panel');
  if (!panel || panel.dataset.bound === '1') return;
  panel.dataset.bound = '1';

  const q = (id) => document.getElementById(id);

  q('li-load-a')?.addEventListener('click', () => loadSelectedToDeck('A'));
  q('li-load-b')?.addEventListener('click', () => loadSelectedToDeck('B'));

  q('li-play-a')?.addEventListener('click', async () => {
    const state = DeckManager.getState().deckA;
    if (state.isPlaying) DeckManager.pauseDeck('A');
    else await DeckManager.playDeck('A');
  });

  q('li-play-b')?.addEventListener('click', async () => {
    const state = DeckManager.getState().deckB;
    if (state.isPlaying) DeckManager.pauseDeck('B');
    else await DeckManager.playDeck('B');
  });

  q('li-sync-a-to-b')?.addEventListener('click', () => {
    const ok = DeckManager.syncDeck('A', 'B', { phase: true });
    toast(ok ? 'Deck B synced from Deck A.' : 'Sync requires BPM metadata on both decks.', ok ? 'success' : 'error');
  });

  q('li-sync-b-to-a')?.addEventListener('click', () => {
    const ok = DeckManager.syncDeck('B', 'A', { phase: true });
    toast(ok ? 'Deck A synced from Deck B.' : 'Sync requires BPM metadata on both decks.', ok ? 'success' : 'error');
  });

  q('li-nudge-a-neg')?.addEventListener('click', () => {
    const d = DeckManager.getState().deckA;
    DeckManager.seekDeck('A', d.position - 0.06);
  });
  q('li-nudge-a-pos')?.addEventListener('click', () => {
    const d = DeckManager.getState().deckA;
    DeckManager.seekDeck('A', d.position + 0.06);
  });
  q('li-nudge-b-neg')?.addEventListener('click', () => {
    const d = DeckManager.getState().deckB;
    DeckManager.seekDeck('B', d.position - 0.06);
  });
  q('li-nudge-b-pos')?.addEventListener('click', () => {
    const d = DeckManager.getState().deckB;
    DeckManager.seekDeck('B', d.position + 0.06);
  });

  q('li-tempo-a')?.addEventListener('input', (evt) => DeckManager.setDeckTempo('A', evt.target.value));
  q('li-tempo-b')?.addEventListener('input', (evt) => DeckManager.setDeckTempo('B', evt.target.value));
  q('li-key-shift-a')?.addEventListener('input', (evt) => DeckManager.setDeckKeyShift('A', evt.target.value));
  q('li-key-shift-b')?.addEventListener('input', (evt) => DeckManager.setDeckKeyShift('B', evt.target.value));

  q('li-key-lock-a')?.addEventListener('change', (evt) => DeckManager.setDeckKeyLock('A', evt.target.checked));
  q('li-key-lock-b')?.addEventListener('change', (evt) => DeckManager.setDeckKeyLock('B', evt.target.checked));

  q('li-crossfader')?.addEventListener('input', (evt) => DeckManager.setCrossfader(evt.target.value));

  q('li-jog-a')?.addEventListener('input', (evt) => {
    const state = DeckManager.getState().deckA;
    if (!state.duration) return;
    DeckManager.seekDeck('A', Number(evt.target.value) * state.duration);
  });

  q('li-jog-b')?.addEventListener('input', (evt) => {
    const state = DeckManager.getState().deckB;
    if (!state.duration) return;
    DeckManager.seekDeck('B', Number(evt.target.value) * state.duration);
  });

  const apply = (state) => {
    const fill = (deckState, prefix) => {
      q(`li-${prefix}-track`).textContent = deckState.title ? `${deckState.title}${deckState.artist ? ` - ${deckState.artist}` : ''}` : 'No track loaded';
      q(`li-${prefix}-time`).textContent = `${fmtTime(deckState.position)} / ${fmtTime(deckState.duration)}`;
      q(`li-${prefix}-bpm`).textContent = deckState.bpm ? `${deckState.bpm.toFixed(2)} BPM` : '-- BPM';
      q(`li-${prefix}-key`).textContent = deckState.key || '--';

      const jog = q(`li-jog-${prefix}`);
      if (jog) {
        jog.value = deckState.duration > 0 ? String(deckState.position / deckState.duration) : '0';
      }

      const tempo = q(`li-tempo-${prefix}`);
      if (tempo) tempo.value = String(deckState.tempo);

      const shift = q(`li-key-shift-${prefix}`);
      if (shift) shift.value = String(deckState.keyShift || 0);

      const lock = q(`li-key-lock-${prefix}`);
      if (lock) lock.checked = !!deckState.keyLock;
    };

    fill(state.deckA, 'a');
    fill(state.deckB, 'b');

    const cross = q('li-crossfader');
    if (cross) cross.value = String(state.crossfader);
  };

  if (window.FablePerformance?.onState) {
    window.FablePerformance.onState(apply);
  }
  apply(DeckManager.getState());
}

function init() {
  bindOnce();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
