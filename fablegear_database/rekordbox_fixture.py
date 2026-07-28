"""
rekordbox_fixture — build a real, encrypted Rekordbox v6 ``master.db``.

Rekordbox keeps its library in a SQLCipher-encrypted SQLite database. Neither
an empty file nor a plain (unencrypted) SQLite file can stand in for it in a
test or a local sandbox: FableGear opens the library through pyrekordbox,
which unlocks it with the fixed per-application key and then expects the full
``DjmdContent`` / ``agentRegistry`` schema. A stub that is missing either the
encryption or the ``localUpdateCount`` registry row fails the moment a write
path (rename relink, prune row removal, dedupe) touches it — which is exactly
the code most worth testing.

This module builds the real thing: a schema-complete, correctly-encrypted db6,
seeded with the ``agentRegistry`` row a write session needs, optionally
pre-populated with tracks (and their artists). Use it to exercise the
database-deep paths against a genuine library instead of mocks.

No secret is introduced here — the key is the standard application key
pyrekordbox already derives on every machine (see :func:`default_key`).

Example
-------
    from fablegear_database.rekordbox_fixture import build_rekordbox_db, FixtureTrack

    db_path = build_rekordbox_db(
        tmp_path / "master.db",
        tracks=[
            FixtureTrack("/Volumes/DJ/Music/a.mp3", title="A", artist="Nula"),
            FixtureTrack("/Volumes/DJ/Music/b.aiff", title="B", artist="Nula"),
        ],
    )
    # Now open it exactly like FableGear does:
    #   from db_connection import read_db, write_db
"""
from __future__ import annotations

import datetime as _dt
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# pyrekordbox + its SQLCipher driver are already required dependencies of the
# database layer; importing them here keeps the fixture self-contained.
import sqlcipher3.dbapi2 as _sqlcipher
from sqlalchemy import DateTime as _SADateTime, Float, Integer, create_engine

import pyrekordbox.db6.database as _rbdb
from pyrekordbox.db6 import tables as _tables

# pyrekordbox stores timestamps as strings via a custom TypeDecorator (also
# named DateTime); its bind processor requires a real datetime, not "". Match
# both it and the plain SQLAlchemy DateTime.
_DATETIME_TYPES = (_SADateTime, _tables.DateTime)

__all__ = ["FixtureTrack", "default_key", "build_rekordbox_db"]

# Rekordbox stores BPM as an integer hundredths of a beat (128.00 → 12800).
_BPM_SCALE = 100

# A fixed, arbitrary timestamp for NOT NULL DateTime columns that carry no real
# meaning in a fixture (rekordbox's agentRegistry date_1/date_2 slots).
_EPOCH = _dt.datetime(2020, 1, 1, 0, 0, 0)


@dataclass
class FixtureTrack:
    """One track to seed into the fixture library.

    Only ``folder_path`` is required. ``artist`` creates (and de-duplicates) a
    ``DjmdArtist`` row and links it. ``columns`` is an escape hatch for setting
    any other ``DjmdContent`` column directly (raw rekordbox units).
    """
    folder_path: str
    title: Optional[str] = None
    artist: Optional[str] = None
    bpm: Optional[float] = None
    key: Optional[str] = None            # written to the Comment field for lookup convenience
    columns: dict = field(default_factory=dict)


def default_key() -> str:
    """Return the standard Rekordbox v6 SQLCipher key pyrekordbox derives.

    This is the fixed per-application key baked into every Rekordbox install —
    the same one :class:`pyrekordbox.Rekordbox6Database` uses when no explicit
    key is given. Nothing secret or per-user is exposed by returning it.
    """
    return _rbdb.deobfuscate(_rbdb.BLOB)


def _neutral_default(column) -> Any:
    """A type-appropriate empty value for a NOT NULL column with no schema
    default. Derived from the live column type so the fixture keeps working if
    pyrekordbox reshapes the schema in a future version."""
    t = column.type
    if isinstance(t, _DATETIME_TYPES):  # rekordbox stores these as strings, but
        return _EPOCH                   # the ORM bind processor needs a datetime
    if isinstance(t, Float):
        return 0.0
    if isinstance(t, Integer):   # covers Integer, BigInteger, SmallInteger
        return 0
    return ""                    # VARCHAR / TEXT


def _required_defaults(model) -> dict:
    """Build a full {column: neutral_value} row for every NOT NULL column of
    *model* that has no schema-side default (so a bare INSERT satisfies the
    schema without the caller enumerating ~70 rekordbox columns)."""
    row: dict = {}
    for col in model.__table__.columns:
        if col.primary_key or col.nullable:
            continue
        if col.default is not None or col.server_default is not None:
            continue
        row[col.name] = _neutral_default(col)
    return row


def _full_row(model, **overrides) -> dict:
    """A complete insertable row for *model*: neutral values for every required
    column, explicit created_at/updated_at (these have defaults, so
    _required_defaults skips them — but we set them anyway to stay independent
    of any column-default machinery), then caller overrides."""
    row = _required_defaults(model)
    row["created_at"] = _EPOCH
    row["updated_at"] = _EPOCH
    row.update(overrides)
    return row


def _build_plaintext(db_path: Path, tracks: Iterable[FixtureTrack]) -> None:
    """Create the full schema in an unencrypted SQLite file and populate it.

    Rows are written with SQLAlchemy Core (``table.insert()``), NOT the
    pyrekordbox ORM classes. That is deliberate: instantiating a
    ``DjmdContent(**row)`` runs pyrekordbox's ``Base.__setattr__`` →
    ``RekordboxAgentRegistry`` global hook, whose state leaks across tests in a
    shared process (a prior test binds it to a since-closed session). Core
    inserts touch only this engine and keep the fixture isolated.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        _tables.Base.metadata.create_all(engine)
        content_t = _tables.DjmdContent.__table__
        artist_t = _tables.DjmdArtist.__table__
        registry_t = _tables.AgentRegistry.__table__

        artist_ids: dict[str, str] = {}
        artist_rows: list[dict] = []
        content_rows: list[dict] = []
        next_id = 0
        usn = 0

        def _fresh_id() -> str:
            nonlocal next_id
            next_id += 1
            return str(1000 + next_id)

        for track in tracks:
            usn += 1
            artist_id = ""
            if track.artist:
                artist_id = artist_ids.get(track.artist, "")
                if not artist_id:
                    artist_id = _fresh_id()
                    artist_ids[track.artist] = artist_id
                    artist_rows.append(_full_row(
                        _tables.DjmdArtist,
                        ID=artist_id, Name=track.artist,
                        SearchStr=track.artist, UUID=_fresh_id(),
                        usn=usn, rb_local_usn=usn,
                    ))

            name = Path(track.folder_path).name
            row = _full_row(
                _tables.DjmdContent,
                ID=_fresh_id(),
                FolderPath=track.folder_path,
                FileNameL=name,
                Title=track.title or name,
                ArtistID=artist_id,
                UUID=_fresh_id(),
                usn=usn,
                rb_local_usn=usn,
            )
            if track.bpm is not None:
                row["BPM"] = int(round(track.bpm * _BPM_SCALE))
            if track.key:
                row["Commnt"] = track.key
            row.update(track.columns)
            content_rows.append(row)

        registry_row = _full_row(
            _tables.AgentRegistry,
            registry_id="localUpdateCount", int_1=usn,
        )

        # One INSERT per row rather than an executemany. executemany requires
        # every row dict in the batch to carry an identical key set, which
        # couples us to the exact NOT NULL column set — and another test in the
        # suite (test_bidirectional_sync) permanently flips every column to
        # nullable on the shared pyrekordbox metadata, which would silently
        # drop optional columns from some rows and not others. Per-row inserts
        # are immune to that and to any future metadata drift.
        with engine.begin() as conn:
            for row in artist_rows:
                conn.execute(artist_t.insert().values(**row))
            for row in content_rows:
                conn.execute(content_t.insert().values(**row))
            conn.execute(registry_t.insert().values(**registry_row))
    finally:
        engine.dispose()


def _encrypt(plaintext: Path, target: Path, key: str) -> None:
    """Copy the plaintext DB into a SQLCipher-encrypted file at *target*."""
    conn = _sqlcipher.connect(str(plaintext))
    try:
        # Passphrase mode (PRAGMA key='<hex>'), matching how pyrekordbox opens
        # the library — it passes this same key string as the connection
        # password, so the DB must be sealed the same way to open later. ATTACH
        # takes no bind params for path/key; the key is the fixed application
        # key and target is caller-controlled.
        conn.execute(f"ATTACH DATABASE '{target}' AS enc KEY '{key}'")
        conn.execute("SELECT sqlcipher_export('enc')")
        conn.execute("DETACH DATABASE enc")
    finally:
        conn.close()


def build_rekordbox_db(
    path: "str | Path",
    tracks: Iterable[FixtureTrack] = (),
    *,
    key: Optional[str] = None,
    overwrite: bool = True,
) -> Path:
    """
    Create a real, encrypted Rekordbox v6 ``master.db`` at *path*.

    Parameters
    ----------
    path : str | Path
        Where to write the encrypted database. Parent dirs are created.
    tracks : iterable of FixtureTrack
        Tracks to seed. Empty (default) yields a valid, writable empty library.
    key : str, optional
        SQLCipher key. Defaults to the standard application key
        (:func:`default_key`) so pyrekordbox / FableGear open it with no
        special configuration.
    overwrite : bool
        Replace an existing file at *path* (default). If False and the file
        exists, ``FileExistsError`` is raised.

    Returns
    -------
    Path
        The path written (same as *path*).
    """
    target = Path(path)
    if target.exists():
        if not overwrite:
            raise FileExistsError(target)
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)

    resolved_key = key or default_key()

    # Build plaintext on the default temp filesystem (local/APFS): some
    # removable/exFAT targets reject the intermediate SQLite journal writes,
    # and we only need the encrypted result on *target* anyway.
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="rbfixture_")
    os.close(tmp_fd)
    tmp_plain = Path(tmp_name)
    try:
        tmp_plain.unlink()  # let sqlite create it fresh
        _build_plaintext(tmp_plain, tracks)
        _encrypt(tmp_plain, target, resolved_key)
    finally:
        if tmp_plain.exists():
            tmp_plain.unlink()
    return target
