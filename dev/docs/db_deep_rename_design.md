# Database-Deep Rename Engine — Design Document

**Status:** Design pass. Not yet implemented. Awaiting path selection.
**Author:** drafted with Claude, 2026-07-24.
**Origin:** QA pass on the Chop Shop tools surfaced that the renamer is the one
core tool whose discovery model is out of step with the rest of the app. This
documents the case for a database-deep rename mode and how it would slot in
beside the existing file-deep one.

---

## 1. How rename works today

`chop_shop/renamer.py :: rename_directory()` is **file-walk-deep**:

1. **Discovery** — `_walk_audio_files(root)` walks the directory tree and
   returns every audio file under it.
2. **Metadata** — for each file, tags are read off disk (mutagen) to build the
   new name via `_generate_filename(artist, title, ext, …, album=…)`.
3. **Filesystem** — the file is renamed in place.
4. **DB relink (best-effort)** — `_sync_db_path_or_revert()` then looks up the
   rekordbox row by exact `FolderPath == old_path` and calls
   `update_content_path`. If no row matches (file isn't in the library), it is
   silently skipped; the rename still happened.

So the DB is a *follower*: the filesystem is renamed first, then the DB is
patched to match, and only if an exact path row is found.

### What the QA pass exposed

- On a library where the tracks were **not** in the rekordbox DB, every rename
  succeeded but 0 DB rows updated — correctly, but silently. The old log line
  even claimed "N DB path updates" where N was the *rename* count, not rows
  touched (fixed in the same pass; the count is now real).
- The relink is **fragile by construction**: it keys on an exact
  `FolderPath` string match against the pre-rename path. Any drift (casing, a
  trailing slash, a volume remount under a different mount point, a path
  already rewritten by a prior tool) means the rename lands on disk while the
  DB quietly keeps the stale path — the exact orphaning rekordbox users fear,
  because cues/beatgrids/playlists are keyed by that path.

## 2. The precedent: FableGear already ships two layers

The rest of the toolkit deliberately offers a **physical-file-deep** tool and a
**database-deep** sibling for the same job:

| Job | Physical-file-deep | Database-deep |
|-----|--------------------|---------------|
| De-duplication | `duplicates` (walk + fingerprint files) | `rekordbox-dedupe` (dedupe from DB rows) |
| Presence check | `novelty` (source files vs dest files) | `dead-files` (DB rows vs what's on disk) |
| **Renaming** | `rename` (walk + tag-read) | **— missing —** |

Rename is the odd one out: it has only the file-deep layer. This document
proposes filling that cell, not replacing the file-deep tool.

## 3. Why not just make rename database-deep?

Because the single most common rename use case is cleaning up a **messy
download/import folder before it is in rekordbox at all**. A pure DB-deep tool
can touch nothing the library doesn't already know about — it would lose the
tool's best job. So: keep file-deep as the default for pre-import cleanup, and
add a DB-deep mode for the *other* real use case — "make my existing library's
filenames consistent, safely."

## 4. What a database-deep rename mode does better

For an already-imported library, DB-deep wins on four axes:

1. **Scope safety.** File-deep renames *everything* under a root — samples,
   stems, half-finished downloads, non-library audio. DB-deep only touches
   tracks the library actually contains. No collateral renames.
2. **No orphaning.** The path update becomes the *primary, atomic* operation
   instead of a fragile after-the-fact string match. Rekordbox's cues,
   beatgrids, and playlist membership all survive because the row is updated in
   the same transaction that renames the file — and only for rows that exist.
3. **Curated metadata.** The DB holds the artist/title the DJ *hand-corrected
   in rekordbox*, which is frequently better than the raw file tags. DB-deep
   names from the curated source of truth, with file tags as fallback.
4. **Speed.** Iterating indexed DB rows beats walking a slow USB drive and
   reading mutagen tags off every file.

## 5. Design

### 5.1 Discovery

Source of truth is the rekordbox DB (`DEVICE_DB` when the device drive is
mounted, else `LOCAL_DB` — the same `_resolve_active_db_path()` the prune and
dedupe tools already use). Iterate `DjmdContent` rows, optionally filtered to a
`--under PATH` prefix so a DJ can rename just one drive/folder's worth.

Each row yields `(content_id, current_path, curated_artist, curated_title,
curated_album, …)`.

### 5.2 Metadata source (precedence)

For each row, build the target name from the **best** available metadata:

1. Curated rekordbox fields (`Title`, `Artist.Name`, `Album.Name`) when present
   and non-junk — reuse the existing `_looks_like_junk_*` guards and the
   album-redundancy logic from `_generate_filename`.
2. Fall back to on-disk file tags (mutagen) when a curated field is empty.
3. Fall back to the current filename stem last.

Name generation reuses `_generate_filename()` verbatim — the album-aware,
length-capped, redundancy-guarded logic is identical; only the *inputs* change
from file-tags-first to DB-first.

### 5.3 The atomic unit

Per track, in order, with a single rollback boundary:

1. Compute `new_path = current_path.parent / new_name` (same-dir rename, as
   today). Resolve collisions with the existing `_resolve_filename_collision`.
2. `os.rename(current_path, new_path)` on disk.
3. `db.update_content_path(row, new_path)` — updates `FolderPath`,
   `OrgFolderPath`, `FileNameL`, and (via pyrekordbox) the ANLZ analysis file
   paths, then commits.
4. Mirror into the FableGear archive (`archive.relink_content`) exactly as the
   file-deep path already does.

If step 3 fails, revert step 2 (rename back) — the inverse of today's
`_sync_db_path_or_revert`, but now the DB update is the *gate*, not an
afterthought: no filesystem change is considered done until its DB row is.

### 5.4 Scope safety / guardrails

- Never touch a path outside the DB set — that is the whole point.
- Honor the existing source guardrails (`_forbidden_source_reason`) for the
  `--under` prefix.
- Files referenced by the DB but **missing on disk** are reported, not
  renamed — this is where DB-deep naturally overlaps `dead-files`, and the two
  should share the "row points at nothing" reporting.

### 5.5 ANLZ / rekordbox-running

`update_content_path` rewrites ANLZ (`.DAT`/`.EXT`) analysis-file paths and
refuses to commit while Rekordbox is running. The mode must:
- enforce the `rekordbox_is_running()` preflight (already in `db_connection`),
  refusing to start rather than failing mid-batch;
- batch commits (reuse the `BATCH_SIZE` cadence already in `rename_directory`)
  so a large library doesn't hold one giant transaction.

## 6. Surface

### CLI
A sibling subcommand, mirroring `duplicates` → `rekordbox-dedupe`:

```
rbtk rekordbox-rename [--db-path PATH] [--under PREFIX]
                      [--source {curated,tags,auto}]   # default: auto (§5.2)
                      [--no-dry-run] [--workers N]
```

Dry-run stays the default (consistent with `rename`), and the report shows the
full before → after path table the file-deep dry-run currently omits.

### UI
In the Chop Shop rename card, a "Library-deep" toggle that switches the same
card between the two engines, with copy explaining the trade-off: file-deep for
pre-import folders, library-deep for an already-imported library.

## 7. Implementation stages

1. **Extract name-generation** — it already is (`_generate_filename`); confirm
   it has no file-walk coupling. (It doesn't.)
2. **`rekordbox_rename_library(db, *, under=None, source="auto", dry_run=True)`**
   in a new `chop_shop/rekordbox_renamer.py`, returning the same
   `RenameResult` list so reporting/checkpointing is shared.
3. **Wire CLI** `cmd_rekordbox_rename` + the argparse subparser.
4. **Wire the SSE route + UI toggle.**
5. **Checkpoint support** — reuse the per-file checkpoint plumbing the other
   tools already have (`skip_paths` / `on_result`).

## 8. Testing

The encrypted-DB fixture added alongside this doc
(`fablegear_database/rekordbox_fixture.py`) makes this directly testable
without mocks:

- Build a library with known `(path, title, artist)` rows.
- Run the DB-deep renamer dry-run → assert the proposed names.
- Run live against real stub files → assert both the on-disk rename **and**
  that `db.get_content(FolderPath=new)` returns the row (curated metadata
  round-tripped, path updated atomically).
- Assert a path **not** in the DB is never touched (scope safety).
- Assert a DB row whose file is missing on disk is reported, not renamed.

## 9. Open questions (need a decision before build)

1. **Metadata precedence default.** §5.2 proposes `auto` (curated-first, tags
   fallback). Is curated-first always right, or do some DJs want file tags to
   win because a prior Tag Tracks / AcoustID pass wrote fresher data to the
   files than rekordbox has re-read? `--source` exposes the choice; the
   question is only the default.
2. **Cross-directory moves.** This spec keeps renames same-directory (as the
   file-deep tool does). Should DB-deep also *relocate* into an Artist/Album
   tree — i.e. absorb part of `organize`? Recommendation: no, keep rename and
   organize separate; a combined mode is a later, bigger design.
3. **Multi-DB.** With the multi-drive work (`docs/multi_drive_library_design.md`)
   there may be more than one rekordbox DB. Does DB-deep rename operate on one
   selected DB, or reconcile across all known ones? Defer to whatever the
   multi-drive design settles on for "active DB."
