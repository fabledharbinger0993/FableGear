"""
Cross-module regression guard: the shared pyrekordbox ORM metadata must not be
left mutated by any earlier test.

``test_bidirectional_sync``'s ``rdb_path`` fixture relaxes nullability on the
process-global ``pyrekordbox.db6.tables.Base.metadata`` to build its mock DB.
If it ever stops restoring those flags on teardown, required columns leak as
nullable into every test that runs afterward, silently corrupting schema-shaped
assertions (and the encrypted-DB fixture's row inserts).

This file is named ``test_zz_…`` so it collects last — after the modules that
mutate the metadata — and it carries none of their autouse fixtures, so it sees
the true post-teardown state. Directly asserts a representative set of NOT NULL
columns are still NOT NULL.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

pytest.importorskip("pyrekordbox", reason="pyrekordbox not installed")

from pyrekordbox.db6 import tables as rb_tables


def test_shared_rekordbox_metadata_nullability_not_leaked():
    checks = [
        (rb_tables.DjmdContent, "BPM"),
        (rb_tables.DjmdContent, "FolderPath"),
        (rb_tables.DjmdContent, "FileType"),
        (rb_tables.DjmdArtist, "Name"),
        (rb_tables.AgentRegistry, "int_1"),
    ]
    leaked = [
        f"{model.__tablename__}.{col}"
        for model, col in checks
        if model.__table__.columns[col].nullable
    ]
    assert not leaked, (
        "shared pyrekordbox metadata left with nullable columns — a test "
        f"mutated Base.metadata without restoring: {leaked}"
    )
