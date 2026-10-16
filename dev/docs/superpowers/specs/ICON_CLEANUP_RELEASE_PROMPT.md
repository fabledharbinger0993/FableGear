# FableGear Icon Cleanup + Release Gate Prompt

Use this prompt before any release that touches UI icons, welcome modal shell, or launcher wiring.

## Objective
Create a clean, single-source icon system and prevent regressions from legacy filenames or duplicate welcome/logo layers.

## Scope
- Static assets in `static/`
- UI references in `templates/`, `static/`, and route-driven HTML fragments
- Legacy compatibility map in `app.py` (`_LEGACY_STATIC_ALIASES`)
- Welcome flow in `static/shared/launcher.js` + `templates/index.html`

## Rules
1. Canonical icon names only in frontend templates/CSS/JS.
2. Legacy icon names may exist only in backend compatibility aliases.
3. Welcome UI must have one source of truth (no duplicate overlay systems).
4. Header button/icon dimensions must remain normalized.
5. No DB write action should be auto-triggered by cleanup work.

## Legacy -> Canonical Map (Current)
- `RB_LOGO.png` -> `icon-logo-fablegear.png`
- `icon-fablego.png` -> `icon-logo-fablegear.png`
- `icon-audit.png` -> `icon-tool-rb-audit.png`
- `icon-convert.png` -> `icon-tool-audio-convert.png`
- `icon-fg-drives.png` -> `icon-tool-drives.png`
- `icon-fg-files.png` -> `icon-tool-files.png`
- `icon-fg-library.png` -> `icon-tool-library.png`
- `icon-fg-rb-tools.png` -> `icon-tool-rb.png`
- `icon-filename.png` -> `icon-tool-filename.png`
- `icon-find-duplicate.png` -> `icon-tool-dedupe.png`
- `icon-folder.png` -> `icon-tool-folder.png`
- `icon-import.png` -> `icon-tool-import.png`
- `icon-link.png` -> `icon-tool-link.png`
- `icon-move.png` -> `icon-tool-move.png`
- `icon-normalize.png` -> `icon-tool-normalize.png`
- `icon-prune.png` -> `icon-tool-prune.png`
- `icon-tag.png` -> `icon-tool-tag.png`
- `icon-track.png` -> `icon-tool-track.png`
- `icon-welcome-info.png` -> `icon-ui-welcome-info.png`
- `icon-start-wizard.png` -> `icon-ui-start-wizard.png`
- `icon-skip-to-next-step.png` -> `icon-ui-skip-step.png`
- `icon-restart-step.png` -> `icon-ui-restart-step.png`
- `icon-restart-from-interrupt.png` -> `icon-ui-restart-interrupt.png`
- `icon-interrupt-plus-stop-wizard.png` -> `icon-ui-stop-wizard.png`

## Verification Checklist
1. Run reference scan and confirm no legacy icon names appear outside `app.py`.
2. Confirm removed legacy files are absent in `static/`.
3. Validate welcome modal renders once, with no duplicate logo/header controls.
4. Validate header/toolbar icons are equal visual size.
5. Run syntax checks for touched Python/JS files.
6. Run smoke launch and inspect startup UI.

## Suggested Commands
```bash
# 1) Find legacy references
python3 - <<'PY'
import pathlib
legacy = [
  'RB_LOGO.png','icon-audit.png','icon-convert.png','icon-fablego.png',
  'icon-fg-drives.png','icon-fg-files.png','icon-fg-library.png','icon-fg-rb-tools.png',
  'icon-filename.png','icon-find-duplicate.png','icon-folder.png','icon-import.png',
  'icon-interrupt-plus-stop-wizard.png','icon-link.png','icon-move.png','icon-normalize.png',
  'icon-prune.png','icon-restart-from-interrupt.png','icon-restart-step.png',
  'icon-skip-to-next-step.png','icon-start-wizard.png','icon-tag.png','icon-track.png',
  'icon-welcome-info.png'
]
root = pathlib.Path('.')
for name in legacy:
  hits = []
  for p in root.rglob('*'):
    if p.is_file() and p.suffix.lower() in {'.py', '.js', '.css', '.html', '.md'}:
      txt = p.read_text(encoding='utf-8', errors='ignore')
      if name in txt:
        hits.append(str(p))
  if any(h != 'app.py' for h in hits):
    print(name)
    for h in sorted(hits):
      print('  ', h)
PY

# 2) Validate Python syntax on touched files
python3 -m py_compile app.py update_checker.py routes_tools.py

# 3) Validate JS syntax on touched files (example)
node --check static/shared/launcher.js
node --check static/record_room/library_mode.js
```

## Release Gate
Release can proceed only if all checks below are true:
- No non-backend legacy icon references remain.
- Obsolete icon files are deleted or explicitly ignored.
- Welcome flow loads without duplication.
- Startup and updater checks are healthy.
- Git status contains only intended cleanup changes.

## Commit Message Template
`chore(ui): finalize icon canonicalization and single-source welcome cleanup`

## Notes
Keep `_LEGACY_STATIC_ALIASES` until one full release cycle confirms no external stale links remain.
