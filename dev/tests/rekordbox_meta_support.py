"""
Shared test support for building mock pyrekordbox-schema databases.

pyrekordbox's ``Mapped[str]`` annotations make SQLAlchemy 2.0 infer NOT NULL for
nearly every column — stricter than a real Rekordbox ``master.db``, whose writes
routinely leave descriptive fields NULL. Fixtures that build mock unencrypted
DBs therefore relax nullability before ``create_all`` so the schema matches
real-world data shapes.

The catch: ``pyrekordbox.db6.tables.Base.metadata`` is a PROCESS-GLOBAL object
shared by every test that touches pyrekordbox. Mutating it in place and not
restoring leaks the relaxation into every later test, silently corrupting
schema-shaped assertions and other fixtures' inserts. This helper scopes the
mutation to a context manager and restores every flag on exit; the built DB
file keeps whatever schema was created inside the block, so the window only
needs to wrap ``create_all``.

The regression guard in ``tests/test_zz_rekordbox_metadata_isolation.py`` fails
if any fixture ever leaks again.
"""
from __future__ import annotations

import contextlib


@contextlib.contextmanager
def relaxed_rekordbox_nullability():
    """Temporarily make every non-PK column on the shared pyrekordbox metadata
    nullable, restoring the original flags on exit."""
    from pyrekordbox.db6 import tables as rb_tables

    original: dict[tuple[str, str], bool] = {}
    for table in rb_tables.Base.metadata.tables.values():
        for column in table.columns:
            if not column.primary_key:
                original[(table.name, column.name)] = column.nullable
                column.nullable = True
    try:
        yield
    finally:
        for (tname, cname), was_nullable in original.items():
            rb_tables.Base.metadata.tables[tname].columns[cname].nullable = was_nullable
