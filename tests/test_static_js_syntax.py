"""
Parse every JavaScript file the app ships.

Why this exists: `static/shared/settings.js` shipped with `const acoustidEl`
declared twice in the same block. That is a SyntaxError, so the browser threw
the *entire file* away — every function in it, including `openSettings` —
and the Settings button silently did nothing. No error was visible anywhere
except the browser console, the server was perfectly healthy, and every other
panel kept working, so it looked like a UI bug rather than a dead script.

A syntax error in any of these files takes out every function that file
defines. That is a whole-feature outage from a one-line mistake, and it is
exactly the class of thing a parser catches for free.

Requires node. Skipped when node is unavailable rather than failing, matching
how the ffmpeg-dependent tests here behave.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC = REPO_ROOT / "static"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed — cannot syntax-check shipped JavaScript")
    return node


def _iter_js():
    return sorted(p for p in STATIC.rglob("*.js") if p.is_file())


def test_there_are_js_files_to_check():
    """Guards against the glob silently matching nothing and the whole suite
    passing vacuously."""
    assert len(_iter_js()) > 10


@pytest.mark.parametrize("path", _iter_js(), ids=lambda p: str(p.relative_to(STATIC)))
def test_js_file_parses(path):
    node = _node()

    src = path.read_text(encoding="utf-8", errors="replace")
    # `node --check` parses as CommonJS, which rejects import/export. Files the
    # app loads with <script type="module"> are checked as modules instead.
    is_module = "\nimport " in src or src.startswith("import ") or "\nexport " in src

    if is_module:
        proc = subprocess.run(
            [node, "--input-type=module", "--check"],
            input=src, capture_output=True, text=True, timeout=60,
        )
    else:
        proc = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True, text=True, timeout=60,
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        head = "\n".join(detail[:6])
        pytest.fail(
            f"{path.relative_to(REPO_ROOT)} does not parse — the browser will "
            f"discard the whole file and every function it defines:\n{head}"
        )


def test_settings_js_defines_open_settings():
    """The specific regression: settings.js must parse *and* actually define
    openSettings, since the Settings button calls it by name from onclick."""
    node = _node()
    settings = STATIC / "shared" / "settings.js"
    modals = STATIC / "shared" / "modals.js"
    assert settings.exists() and modals.exists()

    # Minimal DOM stub — enough for these two files to evaluate at load time.
    script = f"""
const fs = require('fs'), vm = require('vm');
const ctx = {{
  document: {{
    querySelectorAll: () => [], querySelector: () => null,
    getElementById: () => null, addEventListener() {{}},
  }},
  fetch: () => Promise.resolve({{ json: () => ({{}}) }}),
  console,
}};
ctx.window = ctx;
vm.createContext(ctx);
for (const f of [{str(modals)!r}, {str(settings)!r}]) {{
  vm.runInContext(fs.readFileSync(f, 'utf8'), ctx, {{ filename: f }});
}}
if (typeof ctx.openSettings !== 'function') {{
  console.error('openSettings is not defined');
  process.exit(1);
}}
"""
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        "settings.js loaded but openSettings is not defined — the Settings "
        f"button would do nothing.\n{proc.stderr.strip()}"
    )
