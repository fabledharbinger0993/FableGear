# How Rekordbox behaves — an observed record

This document records what Rekordbox **actually does** to `master.db`, established
by watching it, not by reading its schema and guessing.

It exists because the alternative kept biting. Someone writes *"relocate probably
also updates `AnalysisDataPath`"* in a code comment, nobody verifies it, and three
releases later a tool is built on top of the guess. Schema knowledge tells you
what a column is *called*. It does not tell you which columns a given user action
writes, in what order, or what it leaves stale — and that second thing is what
FableGear needs in order to interoperate without corrupting anything.

Entries below marked **Observed** were produced by `scripts/rb_observe.py`, which
diffs a before/after pair of real snapshots. Entries marked **Unverified** are
schema-derived or inherited from documentation and are explicitly *not* evidence.

---

## Method

```bash
./scripts/rb_observe.py snapshot before
#   ... perform exactly ONE action in Rekordbox ...
#   ... then QUIT Rekordbox (this is not optional — see below) ...
./scripts/rb_observe.py snapshot after
./scripts/rb_observe.py diff before after --action "Relocate one track" --append
```

Three rules make the results mean anything:

**One action per pair.** Two changes in one diff cannot be told apart afterwards.
If you relocate a track *and* rate it, the report shows both and attributes
neither.

**Quit Rekordbox before each snapshot.** `master.db` is SQLite in WAL mode.
Committed transactions can sit in the `-wal` sidecar rather than the main file
until a checkpoint, and quitting is what forces that checkpoint. Snapshot a
*running* Rekordbox and you may capture a file that lags the UI — the diff then
reports "no changes," which reads like a finding but is an artifact. `rb_observe
snapshot` refuses to run while Rekordbox is open for exactly this reason;
`--force` overrides it and permanently marks the resulting diff as unreliable.

**A null result is a question, not an answer.** "No row-level changes" almost
always means the method slipped (Rekordbox still running, wrong snapshot pair,
action didn't commit) rather than that Rekordbox genuinely wrote nothing.

### Privacy

The harness diffs a real personal library, so values are **redacted by default** —
recording *which* columns an action writes requires no file paths, track titles,
or comments. `--show-values` opts in, truncated to 80 characters, for the cases
where a value's shape is the point (an encoding, a sentinel, a path prefix).

---

## Safety constraints that shaped this tooling

These are inherited from `rekordbox_safe_write.py` and `db_connection.py`, and
apply to anything that touches a live library:

1. **Never open the live `master.db` while Rekordbox is running.** It holds locks;
   concurrent access risks corruption. `rekordbox_is_running()` in
   `db_connection.py` is the canonical check.
2. **Always copy the sidecars.** `master.db`, `master.db-wal`, and `master.db-shm`
   are one unit. A copy of the main file alone can be missing recent commits.
   `copy_db_with_sidecars()` does this with a size verification, so a truncated
   copy can't pass as a good one.
3. **`FolderPath` is the source of truth** for a track's location — not
   `FileNameL` or `FileNameS`.
4. **Don't change `AnalysisDataPath` without moving the ANLZ files too.** Players
   read that path directly.
5. **BPM is stored as `int(bpm * 100)`.** Read and write accordingly.

---

## Schema landmarks

**Unverified** — read off the schema and the pyrekordbox model layer. Useful for
orientation; not a behavioural claim.

### `DjmdContent` — one row per track

| Column | Notes |
|---|---|
| `ID` | Primary key |
| `Title` | Track title |
| `FolderPath` | Full absolute file path — **the field that breaks on drive remounts** |
| `FileNameL` / `FileNameS` | Long/short filename; derived, not authoritative |
| `BPM` | Stored ×100 |
| `Length` | Seconds |
| `AnalysisDataPath` | Path to the track's ANLZ files |
| `Analysed` | 1 = analysed |
| `Rating` | 0–5 |
| `ColorID` | FK → `DjmdColor` |
| `Commnt` | Comment |
| `DJPlayCount` | Stored as a string |

### Other tables of interest

- `DjmdPlaylist` — playlist tree; `ParentID`, `IsFolder`, `Seq` for ordering
- `DjmdSongPlaylist` — playlist membership (the join table)
- `DjmdCue` — memory cues and hot cues (`Kind`, `Number`, `InMsec`, `InBeatNo`)
- `DjmdArtist`, `DjmdAlbum`, `DjmdGenre`, `DjmdKey`, `DjmdColor` — lookups

---

## The questions this document exists to answer

Open, in rough priority order for FableGear. Each becomes an **Observed** entry
once someone runs the pair.

1. **Relocate a single track** — which columns move? Does `AnalysisDataPath`
   follow `FolderPath`, or go stale?
2. **Relocate a whole folder** — is it N single-track writes, or something bulk?
3. **A drive remounts under a different name** (`/Volumes/X` → `/Volumes/X 1`) —
   does Rekordbox record anything at all, or do the rows simply stop resolving?
   *This is the failure that orphaned an entire 80k-track library mid-gig, and
   the reason tier-0 volume identity is worth building.*
4. **Analyse a track** — which columns, and when does `Analysed` flip?
5. **Import a folder** — insert order across `DjmdContent` and the lookup tables.
6. **Create / reorder a playlist** — how `Seq` is rewritten; whether reordering
   one item rewrites the whole sibling set.
7. **Delete a track from the collection** — hard delete or soft-delete flag?
8. **Edit a tag in Rekordbox** — does it write the file's tags, the DB, or both?

---

## Observed

<!-- rb_observe:append-below -->

*No observations recorded yet. Run the harness — the first entry lands here.*
