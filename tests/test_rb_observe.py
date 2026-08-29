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
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# scripts/ is not a package; load the module by path.
_SPEC = importlib.util.spec_from_file_location(
    "rb_observe", REPO_ROOT / "scripts" / "rb_observe.py"
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


def test_table_without_primary_key_still_diffs(tmp_path):
    """Join tables in master.db don't all declare a PK; rows must still key stably."""
    engine_b = create_engine(f"sqlite:///{tmp_path / 'nb.db'}")
    engine_a = create_engine(f"sqlite:///{tmp_path / 'na.db'}")
    for eng, rows in ((engine_b, [("1", "10")]), (engine_a, [("1", "10"), ("1", "11")])):
        with Session(eng) as s:
            s.execute(text("CREATE TABLE DjmdSongPlaylist (PlaylistID TEXT, ContentID TEXT)"))
            for a, b in rows:
                s.execute(
                    text("INSERT INTO DjmdSongPlaylist VALUES (:a, :b)"), {"a": a, "b": b}
                )
            s.commit()

    report = rb_observe.diff_dumps(
        rb_observe.dump_from_session(Session(engine_b)),
        rb_observe.dump_from_session(Session(engine_a)),
    )
    td = next(t for t in report.touched_tables if t.table == "DjmdSongPlaylist")
    assert td.added == 1


def test_doc_has_the_append_marker():
    """render/append contract: the doc must carry the marker the CLI writes into."""
    doc = (REPO_ROOT / "docs" / "rekordbox_behavior.md").read_text()
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
    assert "old" in body  # existing content preserved
