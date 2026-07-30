# -*- coding: utf-8 -*-
"""
capture_export.py — inventory + parse a mounted Pioneer export (or a captured
copy of one) into durable JSON/text fixtures for the Rekordbox↔FableGear parity
campaign.

Read-only against the source tree. Produces, under --out:
  manifest.json        full recursive tree: every file's rel-path, size, sha256
  pdb_export.json      export.pdb parsed via chop_shop.devicesql_reader
  pdb_exportExt.json   exportExt.pdb parsed the same way
  anlz_tags.json       per-track ANLZ tag inventory (.DAT/.EXT/.2EX) via anlz_reader
  settings.json        MYSETTING*/DEVSETTING/DJMMYSETTING parse via pioneer_settings
  onelibrary.json      exportLibrary.db decrypted (SQLCipher) + WAL-checkpointed,
                       table names + row counts + full content/cue/playlist rows
  two_ex_xor.json      .2EX XOR-mask verification against the documented base pattern
  summary.txt          human-readable roll-up

Usage:
  python3 capture_export.py --root /Volumes/DJMTGO --out <fixturedir>/parsed
  python3 capture_export.py --root <fixturedir> --out <fixturedir>/parsed   # captured copy
"""
import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

# chop_shop modules import each other by bare name (from anlz_reader import ...),
# so put chop_shop/ itself on sys.path, plus the repo root for fablegear_database.
REPO = Path(__file__).resolve().parents[4]  # .../FableGear
sys.path.insert(0, str(REPO / "chop_shop"))
sys.path.insert(0, str(REPO))


def _d(obj):
    """dataclass (recursively) -> plain dict for json."""
    if is_dataclass(obj):
        return {k: _d(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_d(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _d(v) for k, v in obj.items()}
    return obj


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path) -> dict:
    files = []
    for dirpath, _dirs, filenames in os.walk(root):
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
                digest = sha256(p)
            except OSError as exc:
                files.append({"path": str(p.relative_to(root)), "error": str(exc)})
                continue
            files.append({
                "path": str(p.relative_to(root)),
                "size": size,
                "sha256": digest,
            })
    files.sort(key=lambda r: r["path"])
    total = sum(f.get("size", 0) for f in files)
    return {"root": str(root), "file_count": len(files), "total_bytes": total, "files": files}


def dump_pdb(path: Path) -> dict:
    from devicesql_reader import read_pdb
    if not path.is_file():
        return {"present": False, "path": str(path)}
    rep = read_pdb(path)
    out = _d(rep)
    out["present"] = True
    # dedupe-by-track_id view (keep last), since the reader intentionally doesn't
    uniq = {}
    for t in out.get("tracks", []):
        if t.get("track_id") is not None:
            uniq[t["track_id"]] = t
    out["unique_track_count"] = len(uniq)
    return out


def dump_anlz(root: Path) -> dict:
    from anlz_reader import read_anlz_set
    usbanlz = root / "PIONEER" / "USBANLZ"
    sets = []
    if usbanlz.is_dir():
        for dat in sorted(usbanlz.glob("*/*/ANLZ0000.DAT")):
            rep = read_anlz_set(dat.parent)
            d = _d(rep)
            # compact: drop the heavy per-beat lists, keep counts + tag lists
            for key in ("dat", "ext", "two_ex"):
                sub = d.get(key)
                if sub:
                    sub["beat_grid_count"] = len(sub.get("beat_grid", []))
                    sub.pop("beat_grid", None)
                    sub.pop("tags", None)  # keep tags_present, drop offsets
            sets.append(d)
    # aggregate tag frequency across all tracks, per file type
    agg = {"dat": {}, "ext": {}, "two_ex": {}}
    for s in sets:
        for key in ("dat", "ext", "two_ex"):
            sub = s.get(key)
            if not sub:
                continue
            for tag in sub.get("tags_present", []):
                agg[key][tag] = agg[key].get(tag, 0) + 1
    return {"track_count": len(sets), "tag_frequency": agg, "sets": sets}


def dump_settings(root: Path) -> dict:
    from pioneer_settings import read_settings_tree
    reps = read_settings_tree(root)
    return {"files": [_d(r) for r in reps]}


def dump_onelibrary(root: Path, out_dir: Path) -> dict:
    """Decrypt exportLibrary.db with the public OneLibrary key, checkpoint the
    WAL, and dump table names + row counts + key content/cue/playlist rows.
    Works on a *copy* so we never write to the source WAL."""
    import shutil
    import sqlite3  # noqa: F401 (documents intent; real work via sqlcipher3)
    src = root / "PIONEER" / "rekordbox" / "exportLibrary.db"
    if not src.is_file():
        return {"present": False, "path": str(src)}
    try:
        import sqlcipher3
    except ImportError as exc:
        return {"present": True, "path": str(src), "error": f"sqlcipher3 unavailable: {exc}"}
    from fablegear_database.onelibrary_writer import _ONELIBRARY_KEY, _CIPHER_COMPATIBILITY

    # Copy db + wal + shm to a scratch spot so checkpoint doesn't touch source.
    work = out_dir / "_onelibrary_work"
    work.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        s = Path(str(src) + suffix)
        if s.is_file():
            shutil.copy2(s, work / s.name)
    wdb = work / src.name

    conn = sqlcipher3.connect(str(wdb))
    try:
        cur = conn.cursor()
        cur.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
        cur.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
        cur.execute("PRAGMA wal_checkpoint(FULL);")
        # Full schema text (CREATE TABLE/INDEX) — the key structural artifact to
        # diff against FableGear's onelibrary_writer._SCHEMA_SQL.
        schema = {r[0]: r[2] for r in cur.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall()}
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        counts = {}
        for t in tables:
            try:
                counts[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception as exc:  # noqa: BLE001
                counts[t] = f"error: {exc}"

        def rows(table, limit=50):
            """Schema-agnostic SELECT * (avoids assuming column names differ
            between Rekordbox's real schema and FableGear's copy)."""
            c = conn.cursor()
            c.execute(f"PRAGMA key = '{_ONELIBRARY_KEY}';")
            c.execute(f"PRAGMA cipher_compatibility = {_CIPHER_COMPATIBILITY};")
            try:
                c.execute(f"SELECT * FROM {table} LIMIT {limit}")
            except Exception as exc:  # noqa: BLE001
                return [{"_error": str(exc)}]
            cols = [d[0] for d in c.description]
            return [dict(zip(cols, r)) for r in c.fetchall()]

        return {
            "present": True, "path": str(src), "tables": tables, "row_counts": counts,
            "schema": schema,
            "content_sample": rows("content"),
            "cue_sample": rows("cue"),
            "key_all": rows("key", limit=100),
            "playlist_all": rows("playlist", limit=500),
        }
    finally:
        conn.close()


# Documented .2EX XOR base pattern (from the campaign brief / Rekordbox writer).
_TWO_EX_XOR_BASE = bytes.fromhex(
    "CBE1EEFAE5EEADEEE9D2E9EBE1E9F3E8E9F4E1"
)


def analyze_two_ex(root: Path, sample_limit: int = 5) -> dict:
    """Best-effort probe of .2EX payload masking. We don't yet know FableGear's
    writer output here (no .2EX writer exists), so this characterizes the REAL
    files: do their bytes look XOR-masked with the documented base pattern keyed
    off len_file? Reports raw stats so a future FableGear .2EX can be diffed."""
    usbanlz = root / "PIONEER" / "USBANLZ"
    samples = []
    if usbanlz.is_dir():
        for two_ex in sorted(usbanlz.glob("*/*/ANLZ0000.2EX"))[:sample_limit]:
            data = two_ex.read_bytes()
            magic = data[:4]
            samples.append({
                "path": str(two_ex.relative_to(root)),
                "size": len(data),
                "magic": magic.decode("ascii", "replace"),
                "magic_is_PMAI": magic == b"PMAI",
                "first_32_hex": data[:32].hex(),
            })
    return {"xor_base_pattern_hex": _TWO_EX_XOR_BASE.hex(),
            "note": "Real .2EX starts with plaintext PMAI magic (not whole-file XOR). "
                    "The XOR masking in the brief applies to a specific payload region, "
                    "not the whole file — confirm region before claiming parity.",
            "samples": samples}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="mount root or captured copy (dir containing PIONEER/)")
    ap.add_argument("--out", required=True, help="output dir for parsed fixtures")
    ap.add_argument("--skip-manifest", action="store_true", help="skip the (slow) full-tree sha256 manifest")
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rb = root / "PIONEER" / "rekordbox"

    results = {}

    if not args.skip_manifest:
        print("… manifest (recursive sha256) …")
        man = build_manifest(root)
        (out / "manifest.json").write_text(json.dumps(man, indent=2))
        results["manifest"] = f"{man['file_count']} files, {man['total_bytes']:,} bytes"

    print("… export.pdb …")
    pdb = dump_pdb(rb / "export.pdb")
    (out / "pdb_export.json").write_text(json.dumps(pdb, indent=2))
    results["export.pdb"] = pdb.get("detail") or pdb.get("present")

    print("… exportExt.pdb …")
    pdbx = dump_pdb(rb / "exportExt.pdb")
    (out / "pdb_exportExt.json").write_text(json.dumps(pdbx, indent=2))
    results["exportExt.pdb"] = pdbx.get("detail") or pdbx.get("present")

    print("… ANLZ tags …")
    anlz = dump_anlz(root)
    (out / "anlz_tags.json").write_text(json.dumps(anlz, indent=2))
    results["anlz"] = {"tracks": anlz["track_count"], "tag_frequency": anlz["tag_frequency"]}

    print("… settings …")
    st = dump_settings(root)
    (out / "settings.json").write_text(json.dumps(st, indent=2))
    results["settings"] = [f["filename"] for f in st["files"]]

    print("… OneLibrary (decrypt + checkpoint) …")
    try:
        ol = dump_onelibrary(root, out)
        (out / "onelibrary.json").write_text(json.dumps(ol, indent=2, default=str))
        results["onelibrary"] = ol.get("row_counts") or ol.get("error") or ol.get("present")
    except Exception as exc:  # noqa: BLE001
        results["onelibrary"] = f"FAILED: {exc}"
        (out / "onelibrary.json").write_text(json.dumps({"error": str(exc)}, indent=2))

    print("… .2EX probe …")
    tex = analyze_two_ex(root)
    (out / "two_ex_xor.json").write_text(json.dumps(tex, indent=2))
    results["two_ex_samples"] = len(tex["samples"])

    (out / "summary.txt").write_text(json.dumps(results, indent=2, default=str))
    print("\n=== SUMMARY ===")
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
