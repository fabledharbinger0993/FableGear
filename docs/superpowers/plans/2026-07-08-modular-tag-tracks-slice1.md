# Modular Tag Tracks — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Tag Tracks OPTIONS panel into a per-effect model — independent BPM / Key / Normalize toggles, each with its own force/overwrite — expose loudness normalization (today hard-disabled in the UI), journal per-effect counts, and remove the standalone Balance Loudness tab.

**Architecture:** Extend the existing `audio_processor.process_file` / `process_directory` pipeline rather than adding a new abstraction. Split today's single global `force` into per-effect `force_bpm` / `force_key` (keeping `force` as a back-compat "both"). Surface normalization by no longer sending `no_normalize=1` from the UI. Record per-effect counts in the `tag_tracks` archive journal. Frontend is verified via the browser preview (no JS unit harness for the tool panels).

**Tech Stack:** Python 3.12, pytest (run from repo root: `python3 -m pytest tests/... -v`), Flask (`routes_tools.py`), vanilla JS (`static/chop_shop/runners.js`), Jinja templates. Audio via ffmpeg/librosa/mutagen.

## Global Constraints

- **Per-effect force, not one global toggle** — each writing effect owns its overwrite control.
- **Keep engines, remove tabs** — no engine/algorithm code is deleted; only UI entry points move.
- **Fixed effect order:** `decode → BPM → Key → Normalize → Enrich → Rename` (this slice touches BPM/Key/Normalize).
- **Journal-as-you-go** — per-effect outcomes recorded into the `tag_tracks` row (`archive.log_operation`).
- **Per-file error isolation preserved** — one bad file records an error and the pass continues.
- **No new heavy dependencies.**
- **Back-compat:** the existing `--force` CLI flag and `force=` param must keep meaning "force both BPM and key" (the process-retry path at `routes_tools.py:298-304` relies on `--force`).

---

## File Structure

- `audio_processor.py` — add `force_bpm` / `force_key` to `process_file` (:646) and `process_directory` (:768); thread them through the single `process_file` call in `_process_one` (:905).
- `cli.py` — add `--force-bpm` / `--force-key` args to the `process` parser (:2333); pass them from `cmd_process` (:1124) into `process_directory` (:1343); add `normalized` + `enrich_written` counts to `_persist_process_results` (:633).
- `routes_tools.py` — accept `force_bpm` / `force_key` query params in `api_process` (:223) and append the new CLI flags.
- `templates/partials/physical_library/fingerprinting.html` — restructure the OPTIONS panel (:56-73) into per-effect rows + a Normalize toggle.
- `static/chop_shop/runners.js` — rebuild the param set in `_doRunProcess` (:103-121); stop forcing `no_normalize=1`.
- `templates/index.html` — remove the Balance Loudness step-tab (:80-82).
- `templates/partials/physical_library/pipeline_wizard.html` — remove the Balance Loudness step button (:71-74).
- `tests/test_tagger_effects.py` — new test file for this slice.

---

### Task 1: Per-effect force in `process_file`

**Files:**
- Modify: `audio_processor.py:646-654` (signature), `audio_processor.py:697-698` (needs logic)
- Test: `tests/test_tagger_effects.py`

**Interfaces:**
- Produces: `process_file(path, *, detect_bpm=True, detect_key=True, normalise=True, force=False, force_bpm=False, force_key=False, enrich_tags=False) -> ProcessResult`. Effective per-effect force is `force or force_bpm` / `force or force_key`. `ProcessResult.skipped_bpm` / `.skipped_key` are `True` when an existing tag is present and that effect is not forced.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tagger_effects.py
import subprocess, sys
from pathlib import Path

import pytest
from mutagen.id3 import ID3, TBPM

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import audio_processor as ap


def _silent_mp3_with_bpm(tmp_path: Path) -> Path:
    """1s silent MP3 tagged with an existing BPM, via ffmpeg + mutagen."""
    p = tmp_path / "track.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", "1", "-q:a", "9", str(p)],
        check=True, capture_output=True,
    )
    tags = ID3()
    tags.add(TBPM(encoding=3, text=["120"]))
    tags.save(str(p))
    return p


def test_existing_bpm_skipped_without_force(tmp_path, monkeypatch):
    f = _silent_mp3_with_bpm(tmp_path)
    # isolate: no real detection needed for the skip path, but guard anyway
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: None)
    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False)
    assert r.skipped_bpm is True
    assert r.bpm_written is False


def test_force_bpm_overrides_existing_tag(tmp_path, monkeypatch):
    f = _silent_mp3_with_bpm(tmp_path)
    monkeypatch.setattr(ap, "_load_audio_ffmpeg", lambda path: ("AUDIO", 44100))
    monkeypatch.setattr(ap, "_detect_bpm", lambda *a, **k: 128.0)
    written = {}
    monkeypatch.setattr(ap, "_write_tags",
                        lambda path, bpm=None, key=None: written.update(bpm=bpm))
    r = ap.process_file(f, detect_bpm=True, detect_key=False, normalise=False,
                        force_bpm=True)
    assert r.skipped_bpm is False
    assert r.bpm_detected == 128.0
    assert written.get("bpm") == 128.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_tagger_effects.py -v`
Expected: FAIL — `process_file() got an unexpected keyword argument 'force_bpm'`.

- [ ] **Step 3: Add the params and per-effect needs logic**

In `audio_processor.py`, change the signature (was ending `force: bool = False,`):

```python
def process_file(
    path: Path,
    *,
    detect_bpm: bool = True,
    detect_key: bool = True,
    normalise: bool = True,
    force: bool = False,
    force_bpm: bool = False,
    force_key: bool = False,
    enrich_tags: bool = False,
) -> ProcessResult:
```

Replace the `needs_bpm` / `needs_key` lines (currently 697-698):

```python
    # Per-effect force: the global `force` still forces both (back-compat).
    _force_bpm = force or force_bpm
    _force_key = force or force_key
    needs_bpm = detect_bpm and not (_existing("TBPM", "bpm") and not _force_bpm)
    needs_key = detect_key and not (_existing("TKEY", "initialkey") and not _force_key)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_tagger_effects.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add audio_processor.py tests/test_tagger_effects.py
git commit -m "feat(tagger): per-effect force_bpm/force_key in process_file

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Thread per-effect force through `process_directory`

**Files:**
- Modify: `audio_processor.py:768-778` (signature), `audio_processor.py:905-912` (the `process_file` call in `_process_one`)
- Test: `tests/test_tagger_effects.py`

**Interfaces:**
- Consumes: `process_file(..., force_bpm=, force_key=)` from Task 1.
- Produces: `process_directory(root, *, ..., force=False, force_bpm=False, force_key=False, ...)` forwards both flags to every file.

- [ ] **Step 1: Write the failing test**

```python
def test_process_directory_forwards_per_effect_force(tmp_path, monkeypatch):
    captured = {}

    def fake_process_file(path, **kwargs):
        captured.update(kwargs)
        return ap.ProcessResult(path=path)

    # one file so scan_directory yields something
    (tmp_path / "a.mp3").write_bytes(b"\x00" * 32)
    monkeypatch.setattr(ap, "process_file", fake_process_file)
    monkeypatch.setattr("scanner.scan_directory",
                        lambda root: [type("T", (), {"path": tmp_path / "a.mp3"})()])

    ap.process_directory(tmp_path, force_bpm=True, force_key=False, normalise=False)
    assert captured.get("force_bpm") is True
    assert captured.get("force_key") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_tagger_effects.py::test_process_directory_forwards_per_effect_force -v`
Expected: FAIL — `KeyError: 'force_bpm'` (kwarg not forwarded).

- [ ] **Step 3: Add params and forward them**

Change the `process_directory` signature (insert after `force: bool = False,` at line 774):

```python
    force: bool = False,
    force_bpm: bool = False,
    force_key: bool = False,
```

Update the single `process_file(...)` call inside `_process_one` (currently 905-912):

```python
        r = process_file(
            track.path,
            detect_bpm=detect_bpm,
            detect_key=detect_key,
            normalise=normalise,
            force=force,
            force_bpm=force_bpm,
            force_key=force_key,
            enrich_tags=enrich_tags,
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_tagger_effects.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add audio_processor.py tests/test_tagger_effects.py
git commit -m "feat(tagger): forward force_bpm/force_key through process_directory

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Journal per-effect counts

**Files:**
- Modify: `cli.py:633-643` (the `log_operation("tag_tracks", ...)` metadata in `_persist_process_results`)
- Test: `tests/test_tagger_effects.py`

**Interfaces:**
- Consumes: `ProcessResult.normalised` / `.enrich_written` (existing fields).
- Produces: the `tag_tracks` journal metadata additionally contains `normalized` and `enrich_written` integer counts.

- [ ] **Step 1: Write the failing test**

```python
def test_journal_records_per_effect_counts(tmp_path):
    import cli
    from fablegear_database.database import FableGearDatabase
    from fablegear_database.schema import DatabaseConfig

    archive = FableGearDatabase(DatabaseConfig(db_path=tmp_path / "archive.db"))
    r1 = ap.ProcessResult(path=tmp_path / "a.mp3", bpm_detected=120.0,
                          bpm_written=True, normalised=True)
    r2 = ap.ProcessResult(path=tmp_path / "b.mp3", key_detected="8A",
                          key_written=True, enrich_written=True)
    cli._persist_process_results([r1, r2], archive)

    rows = archive.query(
        "SELECT metadata FROM fg_processing_log WHERE operation_type='tag_tracks'"
    )
    import json
    meta = json.loads(rows[-1]["metadata"])
    assert meta["normalized"] == 1
    assert meta["enrich_written"] == 1
```

> Note: if `FableGearDatabase` exposes a different read helper than `.query(...)`,
> match the pattern used in `tests/test_tagger_dedupe_edge.py` for reading
> `fg_processing_log`.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_tagger_effects.py::test_journal_records_per_effect_counts -v`
Expected: FAIL — `KeyError: 'normalized'`.

- [ ] **Step 3: Add the counts**

In `cli.py`, extend the metadata dict (currently 636-642):

```python
            metadata={
                "files_processed": len(all_results),
                "analysis_persisted": written,
                "bpm_written": sum(1 for r in all_results if r.bpm_written),
                "key_written": sum(1 for r in all_results if r.key_written),
                "normalized": sum(1 for r in all_results if r.normalised),
                "enrich_written": sum(1 for r in all_results if r.enrich_written),
                "errors": sum(1 for r in all_results if not r.ok),
            },
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_tagger_effects.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_tagger_effects.py
git commit -m "feat(tagger): journal normalized + enrich_written counts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: CLI `--force-bpm` / `--force-key` flags

**Files:**
- Modify: `cli.py:2333-2338` (add args to the `process` parser), `cli.py:1343-1348` (pass to `process_directory` in `cmd_process`)
- Test: `tests/test_tagger_effects.py`

**Interfaces:**
- Consumes: `process_directory(..., force_bpm=, force_key=)` from Task 2.
- Produces: `python cli.py process PATH --force-bpm --force-key` sets `args.force_bpm` / `args.force_key`, forwarded into `process_directory`.

- [ ] **Step 1: Write the failing test**

```python
def test_cli_parser_has_per_effect_force():
    import cli
    parser = cli.build_parser()  # see note below
    ns = parser.parse_args(["process", "/tmp/x", "--force-bpm"])
    assert ns.force_bpm is True
    assert ns.force_key is False
```

> Note: if the parser is built inside `main()` rather than a `build_parser()`
> helper, add a thin `build_parser()` that returns the configured parser and
> have `main()` call it — this is the smallest change that makes the parser
> unit-testable. Otherwise call the existing factory.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_tagger_effects.py::test_cli_parser_has_per_effect_force -v`
Expected: FAIL — unrecognized arguments `--force-bpm`.

- [ ] **Step 3: Add the flags and forward them**

In `cli.py`, after the `--force` argument (line 2338), add:

```python
    p_process.add_argument(
        "--force-bpm",
        action="store_true",
        dest="force_bpm",
        help="Re-detect and overwrite BPM even if a BPM tag already exists",
    )
    p_process.add_argument(
        "--force-key",
        action="store_true",
        dest="force_key",
        help="Re-detect and overwrite key even if a key tag already exists",
    )
```

In `cmd_process`, update the main `process_directory(...)` call (currently 1343-1348) to pass the new flags:

```python
            results = process_directory(
                root,
                detect_bpm=detect_bpm,
                detect_key=detect_key,
                normalise=normalise,
                force=args.force,
                force_bpm=getattr(args, "force_bpm", False),
                force_key=getattr(args, "force_key", False),
```

(Leave the remaining kwargs of that call unchanged.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_tagger_effects.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cli.py tests/test_tagger_effects.py
git commit -m "feat(cli): --force-bpm/--force-key flags for process

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Route accepts `force_bpm` / `force_key`

**Files:**
- Modify: `routes_tools.py:229-257` (`api_process`)

**Interfaces:**
- Consumes: CLI `--force-bpm` / `--force-key` from Task 4.
- Produces: `GET /api/run/process?force_bpm=1&force_key=1` appends `--force-bpm` / `--force-key` to the CLI command. When these are set, the smart-skip fast path is bypassed (they imply re-processing).

- [ ] **Step 1: Read the current param block and command builder** (`routes_tools.py:229-266`) to preserve ordering.

- [ ] **Step 2: Add the two params**

After the `force = request.args.get("force") == "1"` line (232), add:

```python
    force_bpm = request.args.get("force_bpm") == "1"
    force_key = request.args.get("force_key") == "1"
```

After the `if force: cmd.append("--force")` block (249-250), add:

```python
    if force_bpm:
        cmd.append("--force-bpm")
    if force_key:
        cmd.append("--force-key")
```

Update the smart-skip guard (currently 260-266) so any force variant disables it:

```python
    if (
        smart_skip
        and not force and not force_bpm and not force_key
        and no_normalize
        and not enrich_tags
        and (detect_bpm or detect_key)
    ):
```

- [ ] **Step 3: Verify the command is built correctly**

Run (with the dev server on 5099):
```bash
python3 -c "import ast; print('force-bpm wired')"  # placeholder-free sanity
curl -s -N 'http://localhost:5099/api/run/process?path=/nonexistent&force_bpm=1&no_normalize=1' | head -c 200
```
Expected: an SSE/error stream (not a 400 for unknown param); the process starts with the flag accepted. Confirm no server traceback in `preview_logs`.

- [ ] **Step 4: Commit**

```bash
git add routes_tools.py
git commit -m "feat(route): accept force_bpm/force_key on /api/run/process

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Frontend — per-effect OPTIONS + expose Normalize

**Files:**
- Modify: `templates/partials/physical_library/fingerprinting.html:56-83`
- Modify: `static/chop_shop/runners.js:103-121` (`_doRunProcess`)

**Interfaces:**
- Consumes: the route params from Task 5 (`force_bpm`, `force_key`), and the already-supported `no_normalize`.
- Produces: checkboxes `process-force-bpm`, `process-force-key`, `process-normalize`. `_doRunProcess` sends `force_bpm=1` / `force_key=1` when checked, and sends `no_normalize=1` **only when the Normalize box is unchecked**.

- [ ] **Step 1: Restructure the OPTIONS markup**

Replace the Options `<div class="field">` block (currently 56-73) with per-effect rows. Keep `process-no-bpm` / `process-no-key` (they still drive detect on/off) and `process-retry-errored`; replace the single `process-force` with two per-effect force boxes, and add a Normalize toggle:

```html
      <div class="field">
        <label>Effects</label>
        <div class="checkbox-row">
          <label class="checkbox-label">
            <input type="checkbox" id="process-no-bpm"> Skip BPM detection
          </label>
          <label class="checkbox-label">
            <input type="checkbox" id="process-force-bpm"> Re-detect BPM even if tagged
          </label>
          <label class="checkbox-label">
            <input type="checkbox" id="process-no-key"> Skip key detection
          </label>
          <label class="checkbox-label">
            <input type="checkbox" id="process-force-key"> Re-detect key even if tagged
          </label>
          <label class="checkbox-label">
            <input type="checkbox" id="process-normalize"> Normalize loudness (fold-in of Balance Loudness)
          </label>
          <label class="checkbox-label" id="process-retry-errored-row">
            <input type="checkbox" id="process-retry-errored">
            <span>Retry errored tracks only <span class="field-badge caution" id="process-retry-count"></span></span>
          </label>
        </div>
      </div>
```

- [ ] **Step 2: Update `_doRunProcess` to send the new params**

In `static/chop_shop/runners.js`, replace the param-building block (currently 105-108) — note the retry-body reads `process-no-bpm`/`process-no-key`, leave those ids intact:

```javascript
  if (document.getElementById('process-no-bpm').checked)  p.set('no_bpm', '1');
  if (document.getElementById('process-no-key').checked)  p.set('no_key', '1');
  if (document.getElementById('process-force-bpm')?.checked) p.set('force_bpm', '1');
  if (document.getElementById('process-force-key')?.checked) p.set('force_key', '1');
  if (document.getElementById('process-enrich-tags')?.checked) p.set('enrich_tags', '1');
  // Normalize is now user-controlled: only skip it when the box is unchecked.
  if (!document.getElementById('process-normalize')?.checked) p.set('no_normalize', '1');
```

Delete the old line `p.set('no_normalize', '1');` (currently 109) and the old `process-force` reference (currently 107). Update the checkpoint payload (`_saveToolCkpt('process', {...})`, currently 112-118) to store the new fields:

```javascript
  _saveToolCkpt('process', {
    paths,
    no_bpm:      document.getElementById('process-no-bpm').checked,
    no_key:      document.getElementById('process-no-key').checked,
    force_bpm:   document.getElementById('process-force-bpm')?.checked || false,
    force_key:   document.getElementById('process-force-key')?.checked || false,
    normalize:   document.getElementById('process-normalize')?.checked || false,
    enrich_tags: document.getElementById('process-enrich-tags')?.checked || false,
  });
```

- [ ] **Step 3: Verify in the browser preview**

Restart the preview server (template + JS are cached at process start):
- `preview_stop` then `preview_start` (`fablegear`).
- `preview_eval`: enter Chop Shop (`setFableGearSpace('chop')`), then read the checkbox ids exist:

```javascript
['process-force-bpm','process-force-key','process-normalize'].map(id => !!document.getElementById(id))
```
Expected: `[true, true, true]`.

- Check a couple of boxes and confirm the built query string via a temporary eval of the URL params logic, or check `preview_logs` after clicking Run against a tiny folder shows `--force-bpm` / no `--no-normalize` when Normalize is checked. Confirm no console errors (`preview_console_logs` level error).

- [ ] **Step 4: Commit**

```bash
git add templates/partials/physical_library/fingerprinting.html static/chop_shop/runners.js
git commit -m "feat(tagger-ui): per-effect force toggles + expose Normalize effect

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Remove the Balance Loudness tab + pipeline wizard step

**Files:**
- Modify: `templates/index.html:80-82` (Normalize step-tab)
- Modify: `templates/partials/physical_library/pipeline_wizard.html:71-74` (Balance Loudness pipeline button)

**Interfaces:**
- Consumes: nothing. Removes UI entry points only; the normalize engine (`_measure_lufs` / `_normalise_file`) and the `step-normalize` panel content are untouched by other tools.

- [ ] **Step 1: Confirm nothing else targets the tab**

Run:
```bash
grep -rn "step-normalize" templates/ static/
```
Expected: the `data-target="step-normalize"` tab (index.html:80) and the `#step-normalize` panel. Note the panel id for the next step.

- [ ] **Step 2: Remove the tool-rail tab**

In `templates/index.html`, delete the Normalize step-tab button (currently 80-82):

```html
  <button type="button" class="step-tab" data-target="step-normalize">
    <img class="step-icon" src="/static/icon-normalizer.png" alt="Normalize">
  </button>
```

- [ ] **Step 3: Remove the pipeline-wizard step**

In `templates/partials/physical_library/pipeline_wizard.html`, delete the Balance Loudness button (currently 71-74):

```html
          <button type="button" class="pipe-action-btn" onclick="pipelineAddStep('normalize')" title="Re-encode tracks that are too loud or too quiet">
            <img src="/static/icon-normalizer.png" class="pipe-action-icon" alt="Balance Loudness">
            <span class="pipe-action-text"><strong>Balance Loudness</strong><span>Brings every track to the same volume level</span></span>
          </button>
```

- [ ] **Step 4: Verify in the browser preview**

Restart preview (`preview_stop`/`preview_start`), enter Chop Shop, screenshot the tool rail. Expected: no NORMALIZE tab; TAGGER now owns the Normalize effect. Open the Pipeline Wizard (`openPipelineWizard()`) and confirm no "Balance Loudness" step in the Available list. Confirm no console/server errors.

- [ ] **Step 5: Commit**

```bash
git add templates/index.html templates/partials/physical_library/pipeline_wizard.html
git commit -m "refactor(ui): remove standalone Balance Loudness tab (folded into Tagger)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage (Slice 1 rows only):**
- Modular effect UI → Task 6. ✅
- Per-effect force → Tasks 1, 2, 4, 5, 6. ✅
- Expose/fold Normalize → Tasks 6 (toggle), 7 (remove tab). ✅
- Journal counts → Task 3. ✅
- Remove Balance Loudness from Pipeline Wizard → Task 7. ✅
- (Slices 2 & 3 — Rename fold-in, AcoustID force + submission — are out of scope for this plan; separate plans.)

**Placeholder scan:** The only soft references are the two explicit "Note:" callouts in Tasks 3 and 4 (DB read helper name; parser factory name) — both give a concrete fallback and a pattern to match, not a blank TODO.

**Type consistency:** `force_bpm` / `force_key` are used identically across `process_file` (Task 1), `process_directory` (Task 2), `cmd_process` (Task 4), and the route/JS param names `force_bpm` / `force_key` (Tasks 5, 6). Checkbox ids `process-force-bpm` / `process-force-key` / `process-normalize` are consistent between the markup (Task 6 Step 1) and the JS reads (Task 6 Step 2). `ProcessResult.normalised` (British spelling, existing field) is read correctly in Task 3.

**Open follow-ups (not blockers):** configurable target LUFS for the Normalize effect is deferred (this slice reuses the existing `TARGET_LUFS` constant); a Workers input box in the UI is deferred (the route + CLI already accept `workers`, so it is additive).
