"""
Guard against duplicate top-level declarations across the static JS slices.

The frontend is one monolith (static/fablegear.js) split into per-area slices
by scripts/split_fablegear_js.py and loaded as plain classic <script> tags, so
*all slices share one global scope*. If two slices both declare the same
identifier with `let`/`const`/`var` at top level, the second script to parse
throws a SyntaxError ("Identifier 'X' has already been declared") and that
ENTIRE file fails to execute — silently undefining every function in it.

This actually happened: a merge reintroduced `let renamePreflightState` in
static/chop_shop/runners.js while it was already declared in
static/shared/state.js. The whole runners.js slice died, so every Chop Shop
tool (Tag Tracks, Normalize, Rename, Dedupe, Import, ...) stopped firing with
no console/terminal feedback.

Run from the repo root:
    python3 -m pytest tests/ -v
"""

import re
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories whose .js files are loaded as classic scripts into one shared
# global scope (see the <script src=...> block in templates/index.html).
SLICE_DIRS = ["static/shared", "static/chop_shop", "static/record_room"]

# Matches a top-level (column-0) declaration:  let X = ... / const X = ... / var X = ...
_TOP_LEVEL_DECL = re.compile(r"^(let|const|var)\s+([A-Za-z_$][\w$]*)\s*=")


def _iter_slice_files():
    for d in SLICE_DIRS:
        yield from sorted((REPO_ROOT / d).glob("*.js"))


def test_no_duplicate_top_level_declarations_across_slices():
    decls = defaultdict(list)  # identifier -> ["path:line", ...]
    for path in _iter_slice_files():
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _TOP_LEVEL_DECL.match(line)
            if m:
                decls[m.group(2)].append(f"{rel}:{lineno}")

    duplicates = {name: locs for name, locs in decls.items() if len(locs) > 1}

    assert not duplicates, (
        "Duplicate top-level let/const/var declarations across shared-scope JS "
        "slices — the second slice to load will throw a SyntaxError and silently "
        "fail to execute its whole file:\n"
        + "\n".join(
            f"  {name}:\n    " + "\n    ".join(locs)
            for name, locs in sorted(duplicates.items())
        )
    )
