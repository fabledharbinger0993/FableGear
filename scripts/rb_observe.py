#!/usr/bin/env python3
"""
rb_observe — record what Rekordbox actually does to ``master.db``.

Everything FableGear knows about Rekordbox's behaviour is either read off the
schema or inferred from watching it work. Inference rots: someone writes
"relocate probably also rewrites AnalysisDataPath" in a comment, nobody checks,
and three releases later a tool is built on it. This harness replaces inference
with evidence by doing the dullest possible thing —

    1. snapshot master.db
    2. perform ONE action in Rekordbox
    3. snapshot again
    4. diff, and report exactly which tables/columns moved

— and emitting a record suitable for pasting into ``docs/rekordbox_behavior.md``.

WHY SNAPSHOTS AND NOT THE LIVE FILE
    master.db is SQLCipher-encrypted SQLite with ``-wal``/``-shm`` sidecars.
    Rekordbox holds locks on it while running, and pyrekordbox refuses to open
    it in that state for good reason. So we copy the file (with both sidecars,
    via the same verified-copy helper every other backup path here uses) and
    diff the copies.

THE WAL TRAP — the reason ``snapshot`` refuses to run while Rekordbox is open
    Committed-but-not-checkpointed transactions live in ``-wal``, not in the
    main file. Snapshot a running Rekordbox and you may capture a file whose
    visible contents lag what you just did in the UI. Diff two such snapshots
    and the tool reports "nothing changed" — the single most misleading
    possible result, because it looks like a finding rather than a mistake.
    Quitting Rekordbox checkpoints the WAL, so the snapshot is the truth.
    ``--force`` exists for deliberate experiments and taints the record.

Usage::

    ./scripts/rb_observe.py snapshot before
    #   ... perform exactly one action in Rekordbox, then quit it ...
    ./scripts/rb_observe.py snapshot after
    ./scripts/rb_observe.py diff before after --action "Relocate one track"

    ./scripts/rb_observe.py list

PRIVACY
    This diffs a real personal library. Values are REDACTED by default — the
    document wants to record *which* columns an action writes, which needs no
    file paths, track titles, or comments. ``--show-values`` opts in, truncated,
    for when a value's shape actually matters (an encoding, a sentinel).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

log = logging.getLogger("rb_observe")

SNAPSHOT_ROOT = Path.home() / ".fablegear" / "rb_observe"
DOC_PATH = REPO_ROOT / "docs" / "rekordbox_behavior.md"

#: Columns that move on their own — clock fields Rekordbox touches on almost
#: every write. Listing them as "changed" for every action buries the signal,
#: so they are collapsed into a single note per table instead.
NOISE_COLUMNS = frozenset({"created_at", "updated_at", "USNChanged", "rb_local_usn"})

#: How much of a value to show under --show-values.
_VALUE_CLIP = 80


class ObserveError(RuntimeError):
    """Raised when a snapshot or diff cannot be performed safely."""


# ─── Snapshotting ─────────────────────────────────────────────────────────────

def snapshot(label: str, *, force: bool = False) -> Path:
    """Copy the live ``master.db`` (+ sidecars) into a timestamped snapshot dir.

    Parameters
    ----------
    label : str
        Short name for this capture point, e.g. ``before`` / ``after``.
    force : bool
        Snapshot even if Rekordbox is running. The snapshot is tagged
        ``rekordbox_running: true`` so any diff built from it is flagged as
        untrustworthy rather than silently believed.

    Returns
    -------
    Path
        The snapshot directory.
    """
    from db_connection import copy_db_with_sidecars
    from rekordbox_safe_write import LIVE_DB, rekordbox_running

    if not LIVE_DB.is_file():
        raise ObserveError(f"No master.db at {LIVE_DB}")

    running = rekordbox_running()
    if running and not force:
        raise ObserveError(
            "Rekordbox is running. Committed changes may still be sitting in the "
            "-wal sidecar, so this snapshot could lag what you just did — and the "
            "resulting diff would report 'no changes' as if that were a finding.\n"
            "Quit Rekordbox (that checkpoints the WAL), then snapshot. "
            "Use --force only for a deliberate WAL experiment."
        )

    sdir = SNAPSHOT_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{label}"
    sdir.mkdir(parents=True, exist_ok=True)
    copy_db_with_sidecars(LIVE_DB, sdir / LIVE_DB.name)

    meta = {
        "label": label,
        "captured_at": datetime.now().isoformat(),
        "source": str(LIVE_DB),
        "rekordbox_running": running,
        "forced": bool(force and running),
    }
    (sdir / "meta.json").write_text(json.dumps(meta, indent=2))
    log.info("Snapshot %-8s -> %s", label, sdir)
    return sdir


def find_snapshot(ref: str) -> Path:
    """Resolve a snapshot reference — an exact dir name, a path, or a label
    (in which case the most recent snapshot carrying that label wins)."""
    p = Path(ref)
    if p.is_dir():
        return p
    exact = SNAPSHOT_ROOT / ref
    if exact.is_dir():
        return exact
    matches = sorted(SNAPSHOT_ROOT.glob(f"*_{ref}")) if SNAPSHOT_ROOT.is_dir() else []
    if not matches:
        raise ObserveError(f"No snapshot matching {ref!r} under {SNAPSHOT_ROOT}")
    return matches[-1]


def list_snapshots() -> list[tuple[Path, dict]]:
    """Every snapshot on disk, oldest first, paired with its metadata."""
    if not SNAPSHOT_ROOT.is_dir():
        return []
    out: list[tuple[Path, dict]] = []
    for d in sorted(SNAPSHOT_ROOT.iterdir()):
        if not d.is_dir():
            continue
        meta_file = d / "meta.json"
        meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
        out.append((d, meta))
    return out


# ─── Dumping ──────────────────────────────────────────────────────────────────

def dump_from_session(session: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Dump every user table to ``{table: {row_key: {column: value}}}``.

    Table discovery goes through ``sqlite_master`` rather than pyrekordbox's
    declared models on purpose: Rekordbox 7 carries tables pyrekordbox does not
    model, and those are exactly the interesting ones for this exercise. A
    hardcoded list would make the harness blind precisely where it needs to see.

    Rows are keyed by primary key where the table declares one, else by a stable
    hash of the row, so a diff can tell "this row changed" from "this row is new".
    """
    tables = [
        r[0] for r in session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' "
                 "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        )
    ]

    dump: dict[str, dict[str, dict[str, Any]]] = {}
    for tbl in tables:
        info = list(session.execute(text(f'PRAGMA table_info("{tbl}")')))
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        columns = [row[1] for row in info]
        pk_cols = [row[1] for row in sorted((r for r in info if r[5]), key=lambda r: r[5])]

        rows: dict[str, dict[str, Any]] = {}
        try:
            result = session.execute(text(f'SELECT * FROM "{tbl}"'))
        except Exception as exc:  # a table we cannot read must not kill the run
            log.warning("Skipping table %s — %s", tbl, exc)
            continue

        for raw in result:
            record = dict(zip(columns, raw))
            if pk_cols:
                key = "|".join(str(record.get(c)) for c in pk_cols)
            else:
                key = f"#{hash(tuple(str(v) for v in raw)) & 0xFFFFFFFF:08x}"
            rows[key] = record
        dump[tbl] = rows

    return dump


def dump_snapshot(sdir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """Open a snapshot's ``master.db`` read-only and dump it."""
    from db_connection import read_db

    db_file = sdir / "master.db"
    if not db_file.is_file():
        raise ObserveError(f"Snapshot {sdir.name} has no master.db")
    with read_db(db_file) as db:
        return dump_from_session(db.session)


# ─── Diffing ──────────────────────────────────────────────────────────────────

@dataclass
class TableDiff:
    """What moved in one table between two snapshots."""

    table: str
    added: int = 0
    removed: int = 0
    modified: int = 0
    #: column -> number of rows in which it changed
    columns_changed: dict[str, int] = field(default_factory=dict)
    #: column -> [(before, after), ...] — only populated with --show-values
    samples: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    noise_only: bool = False

    @property
    def touched(self) -> bool:
        return bool(self.added or self.removed or self.modified)


@dataclass
class DiffReport:
    """The full before/after comparison."""

    before: str
    after: str
    tables: list[TableDiff] = field(default_factory=list)
    tables_added: list[str] = field(default_factory=list)
    tables_removed: list[str] = field(default_factory=list)
    tainted: bool = False
    taint_reason: str = ""

    @property
    def touched_tables(self) -> list[TableDiff]:
        return [t for t in self.tables if t.touched]


def _clip(value: Any) -> str:
    s = "NULL" if value is None else str(value)
    return s if len(s) <= _VALUE_CLIP else s[:_VALUE_CLIP] + "…"


def diff_dumps(
    before: dict[str, dict[str, dict[str, Any]]],
    after: dict[str, dict[str, dict[str, Any]]],
    *,
    show_values: bool = False,
) -> DiffReport:
    """Compare two dumps produced by :func:`dump_from_session`."""
    report = DiffReport(before="before", after="after")
    report.tables_added = sorted(set(after) - set(before))
    report.tables_removed = sorted(set(before) - set(after))

    for tbl in sorted(set(before) & set(after)):
        b_rows, a_rows = before[tbl], after[tbl]
        td = TableDiff(table=tbl)
        td.added = len(set(a_rows) - set(b_rows))
        td.removed = len(set(b_rows) - set(a_rows))

        for key in set(b_rows) & set(a_rows):
            b_rec, a_rec = b_rows[key], a_rows[key]
            changed = [c for c in set(b_rec) | set(a_rec) if b_rec.get(c) != a_rec.get(c)]
            if not changed:
                continue
            td.modified += 1
            for col in changed:
                td.columns_changed[col] = td.columns_changed.get(col, 0) + 1
                if show_values and len(td.samples.setdefault(col, [])) < 3:
                    td.samples[col].append((_clip(b_rec.get(col)), _clip(a_rec.get(col))))

        if td.columns_changed and not (set(td.columns_changed) - NOISE_COLUMNS):
            td.noise_only = True
        if td.touched:
            report.tables.append(td)

    return report


def diff_snapshots(before_dir: Path, after_dir: Path, *, show_values: bool = False) -> DiffReport:
    """Dump both snapshots and diff them, carrying forward any WAL taint."""
    report = diff_dumps(
        dump_snapshot(before_dir), dump_snapshot(after_dir), show_values=show_values
    )
    report.before, report.after = before_dir.name, after_dir.name

    for d in (before_dir, after_dir):
        meta_file = d / "meta.json"
        if not meta_file.is_file():
            continue
        meta = json.loads(meta_file.read_text())
        if meta.get("rekordbox_running"):
            report.tainted = True
            report.taint_reason = (
                f"{d.name} was captured while Rekordbox was running (--force). "
                "Committed changes may still have been in the WAL, so an empty or "
                "partial result here is not evidence of anything."
            )
    return report


# ─── Rendering ────────────────────────────────────────────────────────────────

def render_markdown(report: DiffReport, action: str, note: str = "") -> str:
    """Render a diff as a ``rekordbox_behavior.md`` entry."""
    lines: list[str] = []
    lines.append(f"### {action}")
    lines.append("")
    lines.append(f"*Observed {datetime.now():%Y-%m-%d} — `{report.before}` → `{report.after}`*")
    lines.append("")
    if note:
        lines.append(note)
        lines.append("")
    if report.tainted:
        lines.append(f"> **Unreliable.** {report.taint_reason}")
        lines.append("")

    if report.tables_added:
        lines.append(f"**Tables created:** {', '.join(f'`{t}`' for t in report.tables_added)}")
        lines.append("")
    if report.tables_removed:
        lines.append(f"**Tables dropped:** {', '.join(f'`{t}`' for t in report.tables_removed)}")
        lines.append("")

    touched = report.touched_tables
    if not touched:
        lines.append("No row-level changes in any table.")
        lines.append("")
        lines.append(
            "Treat a null result as a question, not an answer: confirm Rekordbox was "
            "quit before the second snapshot, or the change may simply still be in the WAL."
        )
        return "\n".join(lines) + "\n"

    lines.append("| Table | +rows | −rows | changed | columns written |")
    lines.append("|---|---:|---:|---:|---|")
    for td in touched:
        cols = ", ".join(
            f"`{c}`" for c in sorted(td.columns_changed, key=lambda c: -td.columns_changed[c])
            if c not in NOISE_COLUMNS
        ) or "—"
        if td.noise_only:
            cols = "*(timestamps only)*"
        lines.append(f"| `{td.table}` | {td.added} | {td.removed} | {td.modified} | {cols} |")
    lines.append("")

    samples = [(td, td.samples) for td in touched if td.samples]
    if samples:
        lines.append("<details><summary>Sample values</summary>")
        lines.append("")
        for td, cols in samples:
            for col, pairs in sorted(cols.items()):
                if col in NOISE_COLUMNS:
                    continue
                for b, a in pairs:
                    lines.append(f"- `{td.table}.{col}`: `{b}` → `{a}`")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines) + "\n"


def append_to_doc(entry: str) -> None:
    """Append a rendered entry to the observations section of the document."""
    marker = "<!-- rb_observe:append-below -->"
    if not DOC_PATH.is_file():
        raise ObserveError(f"{DOC_PATH} not found — create it before appending.")
    body = DOC_PATH.read_text()
    if marker not in body:
        raise ObserveError(f"Append marker {marker!r} missing from {DOC_PATH.name}")
    DOC_PATH.write_text(body.replace(marker, f"{marker}\n\n{entry}", 1))
    log.info("Appended entry to %s", DOC_PATH)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rb_observe",
        description="Record what Rekordbox does to master.db, by diffing snapshots.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="copy master.db to a labelled snapshot")
    s.add_argument("label")
    s.add_argument("--force", action="store_true",
                   help="snapshot even while Rekordbox runs (taints the record)")

    d = sub.add_parser("diff", help="diff two snapshots")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--action", default="Unnamed action", help="what you did in Rekordbox")
    d.add_argument("--note", default="", help="extra prose for the entry")
    d.add_argument("--show-values", action="store_true",
                   help="include truncated before/after values (personal data)")
    d.add_argument("--append", action="store_true",
                   help="append the entry to docs/rekordbox_behavior.md")

    sub.add_parser("list", help="list snapshots on disk")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        if args.cmd == "snapshot":
            snapshot(args.label, force=args.force)
            return 0

        if args.cmd == "list":
            snaps = list_snapshots()
            if not snaps:
                log.info("No snapshots under %s", SNAPSHOT_ROOT)
                return 0
            for d, meta in snaps:
                flag = " [TAINTED: rekordbox was running]" if meta.get("rekordbox_running") else ""
                log.info("%-40s %s%s", d.name, meta.get("captured_at", "?"), flag)
            return 0

        if args.cmd == "diff":
            report = diff_snapshots(
                find_snapshot(args.before),
                find_snapshot(args.after),
                show_values=args.show_values,
            )
            entry = render_markdown(report, args.action, args.note)
            if args.append:
                append_to_doc(entry)
            else:
                sys.stdout.write(entry)
            return 0

    except ObserveError as exc:
        log.error("%s", exc)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
