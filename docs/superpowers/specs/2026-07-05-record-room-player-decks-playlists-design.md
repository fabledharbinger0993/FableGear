# Record Room — Inline Sample Player, Performance Decks & Playlist Hardening

**Date:** 2026-07-05
**Status:** Approved design → implementation plan next
**Area:** Record Room (`static/record_room/`, `static/chop_shop/tool_modal.js`, `templates/`)

## Summary

Four connected changes to the Record Room, all fully local/offline:

1. **Slim inline sample player** becomes the default playback. Clicking ▶ on a
   track row streams that track inline (pausable, one at a time) with a progress
   fill behind the title. ▶ no longer opens the DJ decks.
2. **The A/B decks become a deliberate "performance mode" pop-out**, reachable
   only via the 🎛 Decks toggle or by dragging a track onto a deck. The inline
   player and the decks never sound at the same time.
3. **A real CDJ tempo/key engine** for the decks, built on the Web Audio API +
   a vendored pitch-shift library (SoundTouchJS), so the tempo fader changes
   tempo and the key control changes key — independently. Runs 100% offline.
4. **Playlist hardening** — the playlist system already exists end to end; drive
   it through the browser, confirm every operation round-trips and persists on
   reload, and fix whatever does not hold together.

## Motivation

- Today the row ▶ button routes **all** playback through the decks
  (`leToggleTrackPlayback` → `deckSetPanel(true)` + `deckLoadTrack`), so every
  play forces the two-turntable performance view open. Auditioning a track
  should be lightweight; performance mode should be deliberate.
- The deck **tempo fader is muted**: with keylock on (default) it sets
  `playbackRate` but with `preservesPitch = true` over a narrow ±8% range, so it
  time-stretches so gently it feels inert. Meanwhile the **key control secretly
  changes speed** — it drives `playbackRate = (1+tempo)·2^(st/12)` with
  `preservesPitch = false`, up to ±41% at ±6 semitones. The controls behave
  backwards from a real deck. (`_deckApplyRate`, `static/record_room/deck.js`.)

## Current state (what already exists)

- **Inline audio element:** `#le-player-audio` is already in the DOM
  (`templates/index.html`), currently used only as a legacy fallback.
- **Row rendering:** `_leBuildTrackRow` (`static/chop_shop/tool_modal.js`) builds
  each `.le-track-row` with a `.le-play-btn` and an editable `.le-col-title`.
  Large libraries are virtualized (only the visible window is in the DOM).
- **Playback routing:** `leToggleTrackPlayback` sends the row ▶ to the decks;
  `lePlaybackStateFor` derives the ▶/❚❚ state from `deckIsPlaying`.
- **Decks:** `static/record_room/deck.js` + `templates/partials/record_room/deck.html`.
  Public API: `deckLoadTrack, deckPlay, deckPause, deckFindTrack, deckIsPlaying,
  deckSetPanel, deckTogglePanel`. Opened via the `🎛 Decks` toggle
  (`deck-toggle-btn` → `deckTogglePanel()`) and drag-to-deck (`text/fg-track`
  drop on `deck-half-*`). Waveforms are synthetic (sine + seeded noise), not
  decoded peaks.
- **Playlists (already built):**
  - Backend (`routes_player.py`): `GET/POST /api/library/playlists`,
    `GET/POST/DELETE /api/library/playlists/<id>/tracks`,
    `PUT /api/library/playlists/<id>` (rename),
    `DELETE /api/library/playlists/<id>`,
    `PUT /api/library/playlists/<id>/tracks/order` (reorder).
  - UI (`tool_modal.js` + `index.html`): playlist tree, `＋ Playlist`
    (`leStartCreate`), rename, delete, export-to-USB, drag-track-to-playlist,
    in-playlist drag reorder.

## Design

### 1. Inline sample player (new default)

**Behavior**
- ▶ on a row toggles the inline player:
  - same track currently playing → **pause**
  - same track paused → **resume**
  - different track → load `/api/library/tracks/{id}/stream` and **play**
- Only one inline preview alive at a time (switching rows swaps the `<audio>`
  source). Starting a preview pauses any playing deck (see §5).
- ▶ **never** opens the decks.

**Button state**
- New module state `_leInlineTrackId` tracks which row owns the inline player.
- `lePlaybackStateFor(id)` is rewritten to reflect the **inline** player
  (`_leInlineTrackId === id && !player.paused`), not deck state.
- `leRefreshPlaybackButtons()` keeps its current DOM sweep but reads the new
  state source.

**Progress fill behind the title**
- The active row's `.le-col-title` gets a `pointer-events:none` fill layer whose
  width tracks `currentTime/duration` on `timeupdate`. Non-active rows never show
  it. This is a plain color fill (no waveform shape) per the chosen "simplest"
  option.
- **Seek:** only the active playing row exposes a thin clickable strip along the
  fill; clicking it seeks. Because the strip exists only on the playing row,
  normal rows keep click-to-select and double-click-to-edit-title unchanged.
- Virtualization note: rows are rebuilt on scroll, so the fill/strip must be
  reconstructed for the active row inside `_leBuildTrackRow` from
  `_leInlineTrackId` + the live player position (not stored on the element).

### 2. Decks become the deliberate performance pop-out

- Remove the auto-open path from ▶: `leToggleTrackPlayback` no longer calls
  `deckSetPanel(true)` / `deckLoadTrack` / `deckPlay`.
- Decks still open via the `🎛 Decks` toggle and via drag-to-deck (unchanged).
- All deck features (sync, key-match, dual synthetic waveform, harmony
  indicator) are untouched except the audio engine in §3.

### 3. Real CDJ tempo/key engine (Web Audio + SoundTouchJS, offline)

**Why:** native `<audio>` couples speed and pitch; independent tempo/key needs a
phase-vocoder.

**Offline guarantee:** the Web Audio API is part of the WebView engine (local
CPU DSP, no network). The pitch-shifter is a single JS file **vendored into
`static/`** and shipped in the app — never fetched from a CDN. License (LGPL for
SoundTouchJS) to be confirmed clean for bundling at vendor time; if not,
substitute an MIT-licensed phase-vocoder of equivalent capability.

**Audio graph (per deck):** load track → fetch stream → `decodeAudioData` into an
`AudioBuffer` → `AudioBufferSourceNode` → SoundTouch processing node → deck gain
→ `AudioContext.destination`. Transport (play/pause/cue/seek), position, and
duration move from `<audio>` timing to Web-Audio time tracking within `deck.js`.

**Control mapping**
- **TEMPO fader** → SoundTouch `tempo` (speed changes, pitch held when keylock
  on). Fixes the muted-tempo bug.
- **KEY control** → SoundTouch `pitchSemitones` (key changes, tempo unchanged).
  Fixes the secret speed change.
- **Keylock OFF** → SoundTouch `rate` (vinyl coupling: speed + pitch ride
  together).
- BPM readout, semitone/key readout, RPM, vinyl spin-rate, SYNC, and KEY-match
  all recompute from the SoundTouch tempo/pitch state instead of `playbackRate`.

**Tradeoff (accepted):** each loaded deck track is decoded into memory before
playback (sub-second decode, a few MB per deck). Fine for performance decks that
load one track at a time.

**Scope guard:** only the **decks** get the Web Audio engine. The inline sample
player (§1) stays on the plain `<audio>` element — it needs no tempo/pitch.

### 4. Playlist "stays together" — verify + harden

The system exists; this is a drive-and-fix pass, not new construction. Exercise
in the preview browser and fix whatever breaks:

1. Create a playlist (`＋ Playlist`).
2. Drag tracks in (row → tree item).
3. Select the playlist → tracks render in order.
4. Reorder via the drag handle → `PUT …/tracks/order` → persists.
5. Remove a track (`DELETE …/tracks`) — confirm a UI path exists; add one if
   missing.
6. Rename / delete the playlist.
7. **Reload the app and confirm the playlist is intact** (round-trips through the
   DB) — the real "stays together" test.
8. Sanity-check it still appears in Export-to-USB.

The concrete fix list emerges from running it; report what holds and what does
not.

### 5. Audio focus coordination ("one at a time")

- New `window.deckPauseAll()` in `deck.js` pauses both decks.
- New `window.leInlinePause()` in `tool_modal.js` pauses the inline player.
- Starting an inline preview calls `deckPauseAll()`; `deckPlay()` calls
  `leInlinePause()`. Guard both with `?.` so either module can load first.

## Files to change

- `static/chop_shop/tool_modal.js` — inline player rewrite (`leToggleTrackPlayback`,
  `lePlaybackStateFor`, `leRefreshPlaybackButtons`, `_leBuildTrackRow`), progress
  fill + seek, `_leInlineTrackId`, `leInlinePause()`.
- `static/record_room/deck.js` — Web Audio + SoundTouch engine (transport, rate
  engine, readouts), `deckPauseAll()`, remove ▶ auto-open coupling.
- `templates/partials/record_room/deck.html` — only if control wiring needs new
  hooks (expected minimal).
- `static/record_room/library_mode.js` — align the filesystem-row ▶ (`fs-play-btn`)
  with the same inline-player + one-at-a-time behavior.
- `static/fablegear.css` — inline progress-fill + seek-strip styles.
- `static/vendor/soundtouch*.js` (new) — vendored pitch-shift library.
- `templates/index.html` — load the vendored library; any small hooks.

## Testing / verification

- Use the preview server + browser tools (never manual "please check").
- **Inline player:** ▶ plays/pauses inline, progress fill sweeps, seek jumps,
  switching rows swaps, ▶ does not open decks.
- **Focus:** starting a deck pauses the inline preview and vice-versa (no double
  audio).
- **Deck engine:** after moving the tempo fader, assert the track audibly speeds
  up / slows (and `SoundTouch tempo` state changed) with pitch held; after moving
  the key control, assert key changes with tempo unchanged; verify with DevTools
  reads, not just ear.
- **Offline:** confirm no network requests to third-party origins when loading a
  deck (all `static/` + `/api/*` only).
- **Playlist:** the round-trip checklist in §4, including a full app reload.

## Out of scope (YAGNI)

- Real decoded-peak waveforms for the inline player (plain fill only).
- Beat-grid-accurate loop/hot-cue performance features beyond what decks already
  do.
- Any change to the playlist backend schema/routes unless the hardening pass
  proves one is required.

## Risks

- **Web-Audio transport rewrite** in `deck.js` is the largest change; position
  tracking, cue, and seek must be re-derived from the AudioContext clock. Mitigate
  by keeping the engine self-contained and verifying transport before wiring
  tempo/pitch.
- **Library license:** confirm SoundTouchJS (LGPL) is acceptable to bundle; have
  an MIT phase-vocoder fallback ready.
- **WKWebView audio quirks:** the audio element must stay DOM-attached; verify
  `AudioContext` resume-on-gesture works in pywebview/WKWebView.
