"""
Tests for scripts/rb_observe.py — the Rekordbox behaviour-observation harness.

The harness's whole value is that its output can be trusted as evidence, so the
diff engine is tested directly against synthetic SQLite databases. That is
possible because dump_from_session() takes a SQLAlchemy session rather than a
Rekordbox handle: the pyrekordbox layer only supplies decryption, and none of
the comparison logic depends on it.

What is deliberately NOT covered here: snapshot() and dump_snapshot(), which
need a real encrypted master.db and a real Rekordbox install. Those are
integration surface, and pretending to cover them with mocks would test the
mocks rather than the behaviour.
"""

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2] / "fablegear"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# scripts/ is not a package; load the module by path.
_SPEC = importlib.util.spec_from_file_location(
    "rb_observe", REPO_ROOT.parent / "dev" / "scripts" / "rb_observe.py"
)
rb_observe = importlib.util.module_from_spec(_SPEC)
# Must be registered before exec: @dataclass resolves annotations via
# sys.modules[cls.__module__], which is None for an unregistered module.
sys.modules["rb_observe"] = rb_observe
_SPEC.loader.exec_module(rb_observe)


def _session(tmp_path: Path, name: str, rows: list[tuple], *, extra_table: bool = False) -> Session:
    """Build a throwaway SQLite DB shaped loosely like DjmdContent, and return an
    open session onto it."""
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    with Session(engine) as setup:
        setup.execute(text(
            "CREATE TABLE DjmdContent ("
            "  ID TEXT PRIMARY KEY, Title TEXT, FolderPath TEXT,"
            "  AnalysisDataPath TEXT, Rating INTEGER, updated_at TEXT)"
        ))
        for r in rows:
            setup.execute(
                text("INSERT INTO DjmdContent VALUES (:i, :t, :f, :a, :r, :u)"),
                {"i": r[0], "t": r[1], "f": r[2], "a": r[3], "r": r[4], "u": r[5]},
            )
        if extra_table:
            setup.execute(text("CREATE TABLE DjmdBrandNew (ID TEXT PRIMARY KEY)"))
        setup.commit()
    return Session(engine)


BASE = [
    ("1", "Bad Girls", "/Volumes/Music/donna.mp3", "/anlz/1", 5, "t0"),
    ("2", "Dreams", "/Volumes/Music/mac.mp3", "/anlz/2", 4, "t0"),
]


def test_detects_single_column_change(tmp_path):
    """The core case: one column moved on one row."""
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", [
        ("1", "Bad Girls", "/Volumes/Music 1/donna.mp3", "/anlz/1", 5, "t0"),
        BASE[1],
    ])
    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(before), rb_observe.dump_from_session(after)
    )
    td = next(t for t in report.touched_tables if t.table == "DjmdContent")
    assert td.modified == 1
    assert td.added == 0 and td.removed == 0
    assert td.columns_changed == {"FolderPath": 1}
    # The question the doc actually asks: does AnalysisDataPath follow along?
    assert "AnalysisDataPath" not in td.columns_changed


def test_counts_rows_added_and_removed(tmp_path):
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", [
        BASE[0],
        ("3", "Rhiannon", "/Volumes/Music/r.mp3", "/anlz/3", 3, "t0"),
    ])
    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(before), rb_observe.dump_from_session(after)
    )
    td = next(t for t in report.touched_tables if t.table == "DjmdContent")
    assert (td.added, td.removed, td.modified) == (1, 1, 0)


def test_identical_dumps_report_nothing(tmp_path):
    """A null result must be genuinely null — no phantom diffs."""
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", BASE)
    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(before), rb_observe.dump_from_session(after)
    )
    assert report.touched_tables == []
    md = rb_observe.render_markdown(report, "Nothing")
    assert "No row-level changes" in md
    # and it must warn rather than let the reader treat it as a finding
    assert "not an answer" in md


def test_timestamp_only_change_is_flagged_as_noise(tmp_path):
    """Clock columns move on almost every write; they must not look like signal."""
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", [
        ("1", "Bad Girls", "/Volumes/Music/donna.mp3", "/anlz/1", 5, "t1"),
        BASE[1],
    ])
    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(before), rb_observe.dump_from_session(after)
    )
    td = next(t for t in report.touched_tables if t.table == "DjmdContent")
    assert td.noise_only is True
    assert "timestamps only" in rb_observe.render_markdown(report, "Touch")


def test_new_table_detected(tmp_path):
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", BASE, extra_table=True)
    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(before), rb_observe.dump_from_session(after)
    )
    assert report.tables_added == ["DjmdBrandNew"]


def test_values_redacted_unless_opted_in(tmp_path):
    """Default output must not leak paths from a real personal library."""
    before = _session(tmp_path, "b.db", BASE)
    after = _session(tmp_path, "a.db", [
        ("1", "Bad Girls", "/Volumes/Private/secret.mp3", "/anlz/1", 5, "t0"),
        BASE[1],
    ])
    dumps = (rb_observe.dump_from_session(before), rb_observe.dump_from_session(after))

    redacted = rb_observe.render_markdown(rb_observe.diff_dumps(*dumps), "Relocate")
    assert "secret.mp3" not in redacted
    assert "`FolderPath`" in redacted  # the column is still reported

    # Fresh filenames: re-running _session against an existing file would
    # re-issue CREATE TABLE on a DB that already has it.
    before2 = _session(tmp_path, "b2.db", BASE)
    after2 = _session(tmp_path, "a2.db", [
        ("1", "Bad Girls", "/Volumes/Private/secret.mp3", "/anlz/1", 5, "t0"),
        BASE[1],
    ])
    shown = rb_observe.render_markdown(
        rb_observe.diff_dumps(
            rb_observe.dump_from_session(before2),
            rb_observe.dump_from_session(after2),
            show_values=True,
        ),
        "Relocate",
    )
    assert "secret.mp3" in shown


def test_long_values_are_clipped():
    assert rb_observe._clip("x" * 500).endswith("…")
    assert len(rb_observe._clip("x" * 500)) == rb_observe._VALUE_CLIP + 1
    assert rb_observe._clip(None) == "NULL"


def _pkless_pair(tmp_path, name, before_rows, after_rows, *, after_sql=None):
    """Two single-table PK-less databases, dumped and diffed.

    Returns the TableDiff for DjmdSongPlaylist, which every caller expects to
    have moved.
    """
    engines = []
    for suffix, rows in (("b", before_rows), ("a", after_rows)):
        eng = create_engine(f"sqlite:///{tmp_path / f'{name}{suffix}.db'}")
        with Session(eng) as s:
            s.execute(text("CREATE TABLE DjmdSongPlaylist (PlaylistID TEXT, ContentID TEXT)"))
            for a, b in rows:
                s.execute(text("INSERT INTO DjmdSongPlaylist VALUES (:a, :b)"), {"a": a, "b": b})
            if suffix == "a" and after_sql:
                s.execute(text(after_sql))
            s.commit()
        engines.append(eng)

    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(Session(engines[0])),
        rb_observe.dump_from_session(Session(engines[1])),
    )
    td = next((t for t in report.touched_tables if t.table == "DjmdSongPlaylist"), None)
    assert td is not None, "DjmdSongPlaylist came back untouched"
    return td


def test_table_without_primary_key_still_diffs(tmp_path):
    """Join tables in master.db don't all declare a PK; rows must still key stably."""
    td = _pkless_pair(tmp_path, "n", [("1", "10")], [("1", "10"), ("1", "11")])
    assert td.added == 1


def test_modified_pkless_row_reports_the_written_column(tmp_path):
    """The regression this harness exists to avoid.

    Keying a PK-less row by a hash of its own values makes an UPDATE look like a
    delete plus an insert: 1 added, 1 removed, and no column named — which is
    precisely the fact the observation is being made to establish. Identity has
    to come from rowid, which an UPDATE does not move.
    """
    td = _pkless_pair(
        tmp_path, "mod", [("1", "10")], [("1", "10")],
        after_sql="UPDATE DjmdSongPlaylist SET ContentID = '99'",
    )
    assert td.modified == 1
    assert td.added == 0 and td.removed == 0
    assert set(td.columns_changed) == {"ContentID"}


def test_duplicate_pkless_rows_are_counted_separately(tmp_path):
    """Two identical rows are legal in a PK-less join table; value-keying
    collapsed them into one and under-counted the table."""
    td = _pkless_pair(tmp_path, "dup", [("1", "10")], [("1", "10"), ("1", "10")])
    assert td.added == 1


def test_hash_fallback_key_is_stable_across_processes(tmp_path):
    """The no-rowid fallback key must not depend on PYTHONHASHSEED.

    Hard to reach through SQL — WITHOUT ROWID requires a PRIMARY KEY, so such a
    table takes the PK path — so the fallback is exercised directly. It exists
    for table-like objects that reject `rowid` (virtual-table modules), and the
    built-in hash() it replaces gave the same row a different key per process.
    """
    assert rb_observe._row_hash(("a", None)) == rb_observe._row_hash(("a", None))
    # A real NULL and the literal string "NULL" are different rows.
    assert rb_observe._row_hash(("a", None)) != rb_observe._row_hash(("a", "NULL"))
    # Field boundaries matter: ("ab", "c") is not ("a", "bc").
    assert rb_observe._row_hash(("ab", "c")) != rb_observe._row_hash(("a", "bc"))


class _UnreadableTableSession:
    """Wraps a real session and makes one table fail on SELECT, the way a
    corrupt page or a tableless virtual-table module would."""

    def __init__(self, inner, bad_table):
        self._inner = inner
        self._bad = bad_table

    def execute(self, clause, *args, **kwargs):
        sql = str(clause)
        if f'"{self._bad}"' in sql and sql.lstrip().upper().startswith("SELECT"):
            raise RuntimeError("database disk image is malformed")
        return self._inner.execute(clause, *args, **kwargs)

    def rollback(self):
        self._inner.rollback()


def test_unreadable_table_aborts_the_dump(tmp_path):
    """A partial dump is indistinguishable from a complete one once rendered."""
    session = _UnreadableTableSession(_session(tmp_path, "bad.db", BASE), "DjmdContent")
    with pytest.raises(rb_observe.ObserveError, match="partial dump"):
        rb_observe.dump_from_session(session)


def test_unreadable_table_can_be_skipped_but_is_recorded(tmp_path):
    session = _UnreadableTableSession(
        _session(tmp_path, "bad2.db", BASE, extra_table=True), "DjmdContent"
    )
    skipped = []
    dump = rb_observe.dump_from_session(session, skip_unreadable=True, skipped=skipped)
    assert skipped == ["DjmdContent"]
    assert "DjmdContent" not in dump
    assert "DjmdBrandNew" in dump  # the run continues past the failure


def test_taint_reasons_accumulate():
    """Two independent reasons to distrust a diff must both survive to render."""
    report = rb_observe.DiffReport(before="b", after="a")
    assert not report.tainted
    report.taint_reasons += ["rekordbox was running", "DjmdCue was skipped"]
    assert report.tainted
    out = rb_observe.render_markdown(report, "Some action")
    assert out.count("**Unreliable.**") == 2


def _stub_db_modules(monkeypatch, *, configured=None, live):
    """Stand in for config/rekordbox_safe_write, neither importable in CI."""
    # SimpleNamespace, not ModuleType: `from X import Y` only needs an object in
    # sys.modules carrying the attribute, and this way the stub's attributes are
    # declared rather than bolted on.
    monkeypatch.setitem(
        sys.modules, "rekordbox_safe_write", types.SimpleNamespace(LIVE_DB=Path(live))
    )
    cfg = (
        types.SimpleNamespace(LOCAL_DB=Path(configured))
        if configured is not None
        else types.SimpleNamespace()  # unconfigured: the import raises
    )
    monkeypatch.setitem(sys.modules, "config", cfg)


def test_snapshot_target_follows_the_configured_database(monkeypatch):
    """The toolkit's configured local_db, not the hardcoded macOS path.

    Every other tool here reads config.LOCAL_DB, and the setup wizard puts it
    under AppData on Windows — where rekordbox_safe_write's LIVE_DB could never
    find it. Observing LIVE_DB regardless meant refusing a good library, or
    recording the behaviour of a different one.
    """
    _stub_db_modules(
        monkeypatch, configured="/configured/master.db", live="/hardcoded/master.db"
    )
    assert rb_observe.live_db_path() == Path("/configured/master.db")


def test_snapshot_target_falls_back_when_toolkit_is_unconfigured(monkeypatch):
    """config raises until `cli.py setup` has run; the harness still works."""
    _stub_db_modules(monkeypatch, configured=None, live="/hardcoded/master.db")
    assert rb_observe.live_db_path() == Path("/hardcoded/master.db")


def test_doc_has_the_append_marker():
    """render/append contract: the doc must carry the marker the CLI writes into."""
    doc = (REPO_ROOT.parent / "dev" / "docs" / "rekordbox_behavior.md").read_text()
    assert "<!-- rb_observe:append-below -->" in doc


def test_append_refuses_without_marker(tmp_path, monkeypatch):
    fake = tmp_path / "doc.md"
    fake.write_text("# no marker here\n")
    monkeypatch.setattr(rb_observe, "DOC_PATH", fake)
    with pytest.raises(rb_observe.ObserveError, match="marker"):
        rb_observe.append_to_doc("entry")


def test_append_inserts_at_marker(tmp_path, monkeypatch):
    fake = tmp_path / "doc.md"
    fake.write_text("# doc\n\n<!-- rb_observe:append-below -->\n\nold\n")
    monkeypatch.setattr(rb_observe, "DOC_PATH", fake)
    rb_observe.append_to_doc("### New entry\n")
    body = fake.read_text()
    assert "### New entry" in body
    assert body.index("<!-- rb_observe:append-below -->") < body.index("### New entry")
    # Also assert the entry lands ABOVE the existing content: "after the marker"
    # alone is satisfied by appending at EOF, which is not insertion at all.
    assert "old" in body  # existing content preserved
    assert body.index("### New entry") < body.index("old")


def test_first_append_removes_the_empty_state_placeholder(tmp_path, monkeypatch):
    """Otherwise the doc claims an observation and "none recorded yet" at once."""
    fake = tmp_path / "doc.md"
    fake.write_text(
        "# doc\n\n<!-- rb_observe:append-below -->\n\n"
        "<!-- rb_observe:placeholder -->\n*No observations yet.*\n"
        "<!-- /rb_observe:placeholder -->\n"
    )
    monkeypatch.setattr(rb_observe, "DOC_PATH", fake)
    rb_observe.append_to_doc("### First entry\n")
    body = fake.read_text()
    assert "### First entry" in body
    assert "No observations yet" not in body
    assert "rb_observe:placeholder" not in body


def test_append_keeps_real_entries_when_stripping_the_placeholder(tmp_path, monkeypatch):
    fake = tmp_path / "doc.md"
    fake.write_text(
        "# doc\n\n<!-- rb_observe:append-below -->\n\n### Earlier entry\n\n"
        "<!-- rb_observe:placeholder -->\n*No observations yet.*\n"
        "<!-- /rb_observe:placeholder -->\n"
    )
    monkeypatch.setattr(rb_observe, "DOC_PATH", fake)
    rb_observe.append_to_doc("### Newer entry\n")
    body = fake.read_text()
    assert "### Earlier entry" in body
    assert "No observations yet" not in body


def test_shipped_doc_carries_a_removable_placeholder():
    """The real doc's empty state must be the fenced kind the stripper removes."""
    doc = (REPO_ROOT.parent / "dev" / "docs" / "rekordbox_behavior.md").read_text()
    assert rb_observe.PLACEHOLDER_OPEN in doc
    assert rb_observe.PLACEHOLDER_CLOSE in doc
