/* ════════════════════════════════════════════════════════════════════════
   FableGear — shared / info
   Auto-extracted from static/fablegear.js by scripts/split_fablegear_js.py
   Loaded as a classic script; shares one global scope with the other slices.
   Original source lines: 4243-4517
   ──────────────────────────────────────────────────────────────────────── */

/* ── info / Glossary system ─────────────────────────────────────────────────── */
const GLOSSARY = [
  // ── Tech ──────────────────────────────────────────────────────────────────
  { id:'db',  cat:'Tech', term:'DB',
    short:'Database — where RekordBox stores everything',
    body:`<p><strong>Database</strong> — a structured file that stores information in organized tables, like a very powerful spreadsheet that the computer reads and writes directly.</p>
<p>RekordBox uses one file called <code>master.db</code> to remember your entire library: track names, BPM, key, playlists, cue points, loops — all of it lives in there.</p>
<p>Every write operation in FableGear creates a timestamped backup of this file before touching it.</p>`},

  { id:'cli', cat:'Tech', term:'CLI',
    short:'Command-Line Interface — terminal window',
    body:`<p><strong>Command-Line Interface</strong> — the text window (Terminal on Mac) where you type instructions directly to the computer instead of clicking buttons in an app.</p>
<p>FableGear's CLI is the actual engine doing the work. This web dashboard is just a control panel that talks to the engine so you never have to type commands yourself.</p>`},

  { id:'py',  cat:'Tech', term:'.py / Python',
    short:'The programming language FableGear is built in',
    body:`<p><strong>Python</strong> — the programming language FableGear is written in. You don't need to know it or read it.</p>
<p>What you do need: <strong>Python 3.12 or later</strong> installed on your Mac. If something won't start, a wrong Python version is usually the reason. Check with <code>python3 --version</code> in Terminal.</p>`},

  { id:'csv', cat:'Tech', term:'CSV',
    short:'Spreadsheet file — opens in Excel or Numbers',
    body:`<p><strong>Comma-Separated Values</strong> — a plain text file that any spreadsheet app (Excel, Numbers, Google Sheets) can open as a table.</p>
<p>The duplicate detector writes its results to a CSV so you can sort, filter, and decide what to remove at your own pace. FableGear never deletes files — that decision is always yours.</p>`},

  { id:'sha', cat:'Tech', term:'SHA-256',
    short:'Content fingerprint — proves two files are identical',
    body:`<p>A mathematical fingerprint of a file's content. If two files have the same SHA-256, they are byte-for-byte identical — regardless of filename, location, or metadata.</p>
<p>The Relocate tool uses this to find files you moved and renamed. Even if you changed the filename completely, if the audio content is the same, it gets matched and the database path gets updated.</p>`},

  { id:'sse', cat:'Tech', term:'SSE',
    short:'How live output streams to your browser',
    body:`<p><strong>Server-Sent Events</strong> — the mechanism this app uses to send command output to your browser in real time as it happens.</p>
<p>When you click Execute and see lines appearing live in the output panel, that's SSE at work. The command is running on your computer; the results are being pushed to the page line by line.</p>`},

  { id:'path', cat:'Tech', term:'File Path',
    short:'The full address of a file on your computer',
    body:`<p>The exact location of a file on your drive, written out as a chain of folders.</p>
<p>Example: <code>/path/to/music/House/Track.aiff</code></p>
<p>RekordBox stores the path of every track in its database. If you move a file without telling RekordBox, the path in the database points to nowhere — the track shows as missing. The Relocate tool fixes this.</p>`},

  // ── Audio ──────────────────────────────────────────────────────────────────
  {
    id: 'bpm', cat: 'Audio', term: 'BPM',
    short: 'Beats Per Minute — how fast a track is',
    body: `<p><strong>Beats Per Minute</strong> — the tempo of a track. A kick drum at 128 BPM fires 128 times per minute.</p>
<p>RekordBox stores BPM internally as BPM × 100 (so 128.0 BPM is stored as 12800). FableGear handles that conversion automatically so you never see raw database values.</p>
<p>Detection uses <strong>librosa</strong>, which analyzes the actual audio waveform for beat patterns — not guessing from the filename.</p>`},
  { id:'key', cat:'Audio', term:'Musical Key',
    short:'The harmonic "home base" of a track',
    body:`<p>The musical scale a track is built around — determines which other tracks it will sound harmonically compatible with when mixed.</p>
<p>FableGear detects key using the <strong>Krumhansl-Schmuckler algorithm</strong> on the audio's chroma features. It understands all three common notations and stores whichever format your database already uses:</p>
<ul><li><strong>Standard</strong> — Am, C, F#m, Bb…</li>
<li><strong>Camelot</strong> — 1A, 8B, 11A…</li>
<li><strong>Open Key</strong> — 1m, 8d, 11m…</li></ul>`},

  { id:'lufs', cat:'Audio', term:'LUFS',
    short:'How loud audio actually sounds — the real measure',
    body:`<p><strong>Loudness Units relative to Full Scale</strong> — the correct way to measure perceived loudness, accounting for how human ears hear different frequencies.</p>
<p><strong>−8.0 LUFS</strong> is the DJ standard — it leaves headroom for the mixer and matches most commercial releases. A track at −14 LUFS will sound noticeably quieter at the same channel level on a CDJ.</p>
<p>Peak levels (waveform height) are a different, less useful measurement. LUFS is what your ears actually hear.</p>`},

  { id:'ebu', cat:'Audio', term:'EBU R128',
    short:'The international loudness measurement standard',
    body:`<p><strong>European Broadcasting Union Recommendation R128</strong> — the international standard defining how to measure integrated loudness correctly.</p>
<p>The same standard Spotify, YouTube, Apple Music, and broadcast TV use for their loudness normalization. FableGear uses R128 analysis to measure your tracks and target them to −8.0 LUFS.</p>`},

  { id:'cbr', cat:'Audio', term:'CBR 320',
    short:'Highest-quality MP3 encoding setting',
    body:`<p><strong>Constant Bitrate at 320 kbps</strong> — the highest quality setting for MP3 encoding. Every second of audio uses the same amount of data.</p>
<p>When FableGear normalizes an MP3, it re-encodes at 320 kbps CBR. This is still a lossy process — any re-encode of a lossy file costs some quality — which is why normalization is optional and having a backup first is strongly recommended.</p>
<p>AIFF and WAV files are re-encoded losslessly, so no quality loss at all.</p>`},

  { id:'aiff', cat:'Audio', term:'AIFF / AIF',
    short:'Lossless audio format — full quality, larger file',
    body:`<p><strong>Audio Interchange File Format</strong> — Apple's lossless audio format. Common in professional DJ libraries because it preserves full recording quality and supports embedded cue points that survive a drive wipe.</p>
<p>When FableGear normalizes an AIFF it re-encodes losslessly at the same bit depth as your original — no generation loss whatsoever.</p>`},

  { id:'id3', cat:'Audio', term:'ID3 Tags',
    short:'Metadata embedded inside the audio file itself',
    body:`<p>The format used to store metadata <em>inside</em> audio files — title, artist, album, BPM, key, year, track number, and more.</p>
<p>When you see track info in RekordBox, Finder, or iTunes, you're reading ID3 tags. FableGear writes BPM and key into these tags so the data <strong>travels with the file</strong>, not just in the database. If you ever re-import, the tags are already there.</p>`},

  { id:'fp', cat:'Audio', term:'Chromaprint / fpcalc',
    short:'Acoustic fingerprinting — identifies songs by sound',
    body:`<p><strong>Chromaprint</strong> is the fingerprinting library (used by AcoustID and MusicBrainz) that identifies recordings by their acoustic content — not their metadata.</p>
<p><code>fpcalc</code> is the command-line tool it ships with. FableGear calls it to analyze the first 120 seconds of each file and generate a fingerprint. Two identical fingerprints = same recording, no matter what the files are named or what format they're in.</p>
<p>Requires <code>fpcalc</code> installed on your system: <code>brew install chromaprint</code></p>`},

  // ── RekordBox ──────────────────────────────────────────────────────────────
  { id:'mdb', cat:'RekordBox', term:'master.db',
    short:'RekordBox\'s main database file — back this up',
    body:`<p>The single SQLite file where RekordBox stores your entire library — every track, playlist, cue point, loop, hot cue color, and rating.</p>
<p>Locations:<br>
<code>~/Library/Pioneer/rekordbox/master.db</code> — your Mac<br>
<code>/path/to/drive/PIONEER/Master/master.db</code> — some legacy/export targets<br>
<code>/path/to/drive/PIONEER/rekordbox/exportLibrary.db</code> + <code>export.pdb</code> — common Pioneer USB export layout</p>
<p><strong>Every FableGear write operation creates a timestamped copy of this file in your configured backup directory (visible in Settings or returned by <code>/api/config</code>) before touching it.</strong> The backup header in this app shows you when the last one was made.</p>`},

  { id:'cont', cat:'RekordBox', term:'DjmdContent',
    short:'The track table inside master.db',
    body:`<p>The database table where each track gets one row. Every attribute RekordBox knows about a track — title, artist, BPM, key, file path, bit depth, sample rate, cue points — lives here.</p>
<p>When you import, FableGear writes rows to this table. When you relocate, it updates the <code>FolderPath</code> column. It's the heart of your library.</p>`},

  { id:'fp2', cat:'RekordBox', term:'FolderPath',
    short:'The stored file path in the database',
    body:`<p>The exact file path stored in <code>DjmdContent</code> pointing to where a track lives on disk.</p>
<p>When you move files to a new folder or drive, the old path no longer resolves — RekordBox shows a broken link icon. The Relocate tool fixes this by updating <code>FolderPath</code> values to where the files actually are now.</p>`},

  { id:'cdj', cat:'RekordBox', term:'CDJ / XDJ',
    short:'Pioneer hardware DJ players used in clubs',
    body:`<p>Pioneer's professional media players — the industry standard hardware in most clubs, festivals, and touring setups.</p>
<p>These players read Pioneer export metadata from your USB drive or rekordbox link. Depending on the export type, that may be a <code>master.db</code> or a Rekordbox USB export bundle like <code>exportLibrary.db</code> and <code>export.pdb</code>. A corrupt or broken export means tracks won't load mid-set. This is why the backup-before-every-write rule is not negotiable.</p>`},

  { id:'cam', cat:'RekordBox', term:'Camelot / Open Key',
    short:'Harmonic mixing notation systems',
    body:`<p>Two notation systems for musical keys designed to make harmonic mixing easy by replacing key names with numbers and letters.</p>
<p><strong>Camelot</strong> — 1A through 12B. Adjacent numbers are harmonically compatible.<br>
<strong>Open Key</strong> — 1m through 12d. Same concept, different notation.</p>
<p>FableGear maps all notations — including standard (Am, C#, F#m, etc.) — to whichever format your database already uses.</p>`},

  // ── FableGear ───────────────────────────────────────────────────────────────
  { id:'dry', cat:'FableGear', term:'Dry Run',
    short:'Preview mode — shows what would happen, writes nothing',
    body:`<p>Running a command with dry run enabled shows you exactly what <em>would</em> happen — how many tracks would be imported, what paths would change — without writing a single byte to the database.</p>
<p><strong>Always run the Preview Import step before the real import.</strong> If the track count looks wrong, you haven't broken anything yet. The dry run is free.</p>`},

  { id:'bat', cat:'FableGear', term:'Batch Commit',
    short:'Writing changes in chunks of 250',
    body:`<p>Instead of writing one track at a time (slow) or all tracks at once (risky), FableGear collects 250 changes and writes them as a single transaction.</p>
<p>If that transaction fails, the entire chunk rolls back — you never end up with 137 tracks written and 113 missing in a half-finished state.</p>`},

  { id:'rol', cat:'FableGear', term:'Rollback',
    short:'Auto-undo on failure — prevents partial writes',
    body:`<p>If any unhandled error occurs during a write operation, the database transaction is automatically cancelled — every pending change in that session is undone as if it never started.</p>
<p>This is the mechanism that prevents partial imports. Either a full batch of 250 tracks lands cleanly, or none of them do. You will never have a half-imported library.</p>`},

  { id:'orp', cat:'FableGear', term:'Orphan File',
    short:'File on disk that RekordBox doesn\'t know about',
    body:`<p>An audio file that exists in your music folder but has no matching row in the RekordBox database — RekordBox doesn't know it's there.</p>
<p>Orphans appear in the Audit report. Common causes: files copied directly into the folder without going through an import, or leftovers from a failed previous import. The import step is how you bring them in.</p>`},

  { id:'fuz', cat:'FableGear', term:'Fuzzy Match',
    short:'Approximate name matching — catches near-misses',
    body:`<p>Instead of requiring an exact string match, fuzzy matching scores text similarity and accepts anything above a threshold.</p>
<p>FableGear uses it in two places:</p>
<ul><li><strong>Playlist linking</strong> — folder name vs. playlist name, 85% threshold</li>
<li><strong>File relocation</strong> — filename stem similarity, 90% threshold</li></ul>
<p>Higher threshold = stricter = fewer false positives, but more unmatched items. The defaults are tuned for DJ library naming conventions.</p>`},

  { id:'rarp', cat:'FableGear', term:'RARP',
    short:'Duplicate ranking: Pioneer Numbered → MIK → Raw',
    body:`<p>The hierarchy used to recommend which copy to keep when duplicate tracks are found:</p>
<ul>
<li><strong>PN (Pioneer Numbered)</strong> — filename starts with digits + separator, e.g. <code>01 - Title</code>. Suggests it came from a curated, numbered source.</li>
<li><strong>MIK (Mixed In Key tagged)</strong> — has a <code>TKEY</code>/<code>initialkey</code> tag already written by Mix In Key.</li>
<li><strong>RAW</strong> — neither. Likely an unprocessed download.</li>
</ul>
<p>The CSV marks the top-ranked file in each group as KEEP. You review and make the final call — FableGear never deletes anything.</p>`},

  { id:'bak', cat:'FableGear', term:'.bak File',
    short:'Temporary safety copy kept during audio processing',
    body:`<p>When normalizing loudness, the original file is renamed to <code>filename.mp3.bak</code> before the replacement is written.</p>
<p>The <code>.bak</code> is only deleted after FableGear confirms the new file is valid and readable using <code>soundfile</code>. If anything fails, your original is still there — just rename it to remove <code>.bak</code>.</p>
<p>If you see leftover <code>.bak</code> files after an interrupted run, treat them as your originals. Verify the non-<code>.bak</code> version is intact before removing them.</p>`},

  { id:'norm', cat:'FableGear', term:'Normalization',
    short:'Matching loudness levels across your library',
    body:`<p>The process of analyzing each track's integrated loudness (LUFS) and re-encoding it so every track hits the same target level — <strong>−8.0 LUFS</strong>.</p>
<p>Why it matters: without normalization, different tracks have different volumes. On CDJs you end up riding the channel gain between tracks during a mix. Normalized libraries let you keep gain at unity and focus on the mix.</p>
<p>This is the highest-risk operation in the toolkit because it rewrites audio files. The <code>.bak</code> safety system means your originals are protected, but an independent drive backup first is strongly recommended.</p>`},
];

/* ── info interaction ──────────────────────────────────────────────────────── */
let infoHoverTimer  = null;
let infoCardsActive = false;
const pinnedCards  = new Map();   // id → DOM element
let cardZ          = 1000;

function _buildinfoList() {
  const list = document.getElementById('info-panel-list');
  if (list.children.length) return;
  const groups = ['Tech','Audio','RekordBox','FableGear'];
  groups.forEach(g => {
    const lbl = document.createElement('div');
    lbl.className = 'info-group-label';
    lbl.textContent = g;
    list.appendChild(lbl);
    GLOSSARY.filter(t => t.cat === g).forEach(t => {
      const row = document.createElement('div');
      row.className = 'info-item';
      row.id = `info-item-${t.id}`;
      row.innerHTML = `<span class="info-term">${t.term}</span><span class="info-short">${t.short}</span>`;
      row.onclick = e => { e.stopPropagation(); toggleCard(t.id); };
      list.appendChild(row);
    });
  });
}

function infoHoverIn() {
  clearTimeout(infoHoverTimer);
  _buildinfoList();
  document.getElementById('info-hover-panel').classList.add('visible');
}
function infoHoverOut() {
  infoHoverTimer = setTimeout(() =>
    document.getElementById('info-hover-panel').classList.remove('visible'), 220);
}

function toggleCard(id) {
  pinnedCards.has(id) ? closeCard(id) : openCard(id);
}

function openCard(id) {
  const t = GLOSSARY.find(x => x.id === id);
  if (!t) return;

  // cascading spawn positions — stays inside viewport, avoids top nav bars
  const col  = pinnedCards.size % 3;
  const row  = Math.floor(pinnedCards.size / 3) % 4;
  const top  = 130 + row  * 44;
  const left =  20 + col  * 308;

  const card = document.createElement('div');
  card.className = 'gls-card';
  card.style.cssText = `top:${top}px;left:${left}px;z-index:${++cardZ}`;
  card.innerHTML = `
    <div class="gls-card-head">
      <span class="gls-card-term">${t.term}</span>
      <span class="gls-card-cat">${t.cat}</span>
      <button class="gls-card-close" onclick="closeCard('${id}')">✕</button>
    </div>
    <div class="gls-card-body">${t.body}</div>`;
  card.addEventListener('mouseenter', () => { card.style.zIndex = ++cardZ; });
  document.body.appendChild(card);
  pinnedCards.set(id, card);

  const item = document.getElementById(`info-item-${id}`);
  if (item) item.classList.add('pinned');
  infoCardsActive = true;
}

function closeCard(id) {
  const c = pinnedCards.get(id);
  if (c) { c.remove(); pinnedCards.delete(id); }
  const item = document.getElementById(`info-item-${id}`);
  if (item) item.classList.remove('pinned');
  if (pinnedCards.size === 0) {
    infoCardsActive = false;
  }
}

document.getElementById('settings-backdrop').addEventListener('click', function(e) {
  if (e.target === this) closeSettings();
});
document.getElementById('report-modal-backdrop').addEventListener('click', function(e) {
  if (e.target === this) closeReportModal();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeSettings();
    closeReportModal();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
    e.preventDefault();
    toggleFileBrowser();
  }
});

/* patch openLog / closeLog to shift info button above the log panel */
const _origOpenLog  = openLog;
const _origCloseLog = closeLog;
openLog  = function(t) { _origOpenLog(t);  document.body.classList.add('log-open');    };
closeLog = function()   { _origCloseLog();  document.body.classList.remove('log-open'); };

