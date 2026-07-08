"""
duplicate_detector_database.py — Database-first duplicate detection.

Refactored duplicate detection that uses the FableGear database for
instant duplicate detection via file hashes, only falling back to
acoustic fingerprinting when necessary.

This provides significant performance improvements:
- Database hash matching: O(1) vs O(n) audio processing
- Selective fingerprinting: Only for ambiguous cases
- Instant results for known files
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

try:
    from fablegear_database import FableGearDatabase, ContentRecord
    from fablegear_database.importer import FileImporter
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False
    logging.warning("FableGear database not available, falling back to file-based detection")

from chop_shop.duplicate_detector import (
    _walk_audio_files,
    AUDIO_EXTENSIONS,
    fingerprint_file,
    hamming_distance,
)

log = logging.getLogger(__name__)

# Minimum fingerprint quality threshold for considering a fingerprint reliable.
# Successful fpcalc runs store quality 100; below this threshold a stored
# fingerprint is considered unreliable and will be re-computed.
MIN_FINGERPRINT_QUALITY = 80


@dataclass
class DatabaseDuplicateResult:
    """Result of database-first duplicate detection."""
    hash_duplicates: List[Dict[str, Any]]  # Instant hash-based duplicates
    fingerprint_duplicates: List[Dict[str, Any]]  # Acoustic fingerprint duplicates
    needs_fingerprinting: List[Path]  # Files that need acoustic analysis
    total_files: int = 0
    database_files: int = 0
    new_files: int = 0


def scan_duplicates_database_first(
    root: "Path | list[Path]",
    *,
    max_workers: int = 1,
    match_mode: str = "exact",
    fuzzy_threshold: float = 0.85,
    force_fingerprint: bool = False,
) -> DatabaseDuplicateResult:
    """
    Scan for duplicates using database-first approach.
    
    This function first checks the database for file hash duplicates
    (instant), then only uses acoustic fingerprinting for files that
    need it (ambiguous cases or force_fingerprint=True).
    
    Args:
        root: Directory or list of directories to scan
        max_workers: Number of workers for fingerprinting
        match_mode: Matching strategy (exact, fuzzy, tags, all)
        fuzzy_threshold: Minimum similarity for fuzzy matching
        force_fingerprint: Force acoustic fingerprinting for all files
        
    Returns:
        DatabaseDuplicateResult with duplicate information
    """
    result = DatabaseDuplicateResult()
    
    if not DATABASE_AVAILABLE:
        log.warning("Database not available, use legacy scan_duplicates")
        return result
    
    try:
        # Initialize database
        db = FableGearDatabase()
        importer = FileImporter(db)
        
        # Scan filesystem for audio files
        roots = [root] if isinstance(root, Path) else list(root)
        all_files = []
        
        for r in roots:
            if r.is_dir():
                files = _walk_audio_files(r)
                all_files.extend(files)
        
        result.total_files = len(all_files)
        log.info("Found %d audio files to scan", len(all_files))
        
        # Import files into database (this is fast if already imported)
        import_stats = importer.import_files(roots, force_refresh=False)
        result.database_files = import_stats["new_files"] + import_stats["updated_files"]
        result.new_files = import_stats["new_files"]
        
        # Get hash-based duplicates (instant)
        hash_dupes = db.find_duplicates_by_hash()
        result.hash_duplicates = [
            {
                "hash": file_hash,
                "record_ids": record_ids,
                "method": "file_hash",
            }
            for file_hash, record_ids in hash_dupes
        ]
        
        log.info("Found %d duplicate groups by file hash", len(result.hash_duplicates))
        
        # Determine which files need acoustic fingerprinting
        if force_fingerprint or match_mode in ("fuzzy", "all"):
            # Get all files that don't have fingerprints
            all_records = db.get_all_content(limit=100000)
            needs_fingerprinting = [
                Path(record.file_path)
                for record in all_records
                if not record.acoustic_fingerprint or record.fingerprint_quality < MIN_FINGERPRINT_QUALITY
            ]
            result.needs_fingerprinting = needs_fingerprinting
            
            log.info("%d files need acoustic fingerprinting", len(needs_fingerprinting))
            
            # Fingerprint files that need it
            if needs_fingerprinting:
                fingerprint_duplicates = _fingerprint_files(
                    needs_fingerprinting,
                    db,
                    importer,
                    max_workers,
                    fuzzy_threshold,
                )
                result.fingerprint_duplicates = fingerprint_duplicates
        else:
            # For exact match mode, hash duplicates are sufficient
            result.fingerprint_duplicates = []
        
        return result
        
    except Exception as exc:
        log.error("Database-first duplicate detection failed: %s", exc)
        return result


def _fingerprint_files(
    files: List[Path],
    db: FableGearDatabase,
    importer: FileImporter,
    max_workers: int,
    fuzzy_threshold: float,
) -> List[Dict[str, Any]]:
    """
    Fingerprint files and find acoustic duplicates.
    
    Args:
        files: Files to fingerprint
        db: Database instance
        importer: File importer instance
        max_workers: Number of workers
        fuzzy_threshold: Fuzzy matching threshold
        
    Returns:
        List of duplicate groups found via fingerprinting
    """
    import concurrent.futures
    
    fingerprints = {}
    
    # Fingerprint files
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_file = {
            executor.submit(fingerprint_file, file_path): file_path
            for file_path in files
        }
        
        for future in concurrent.futures.as_completed(future_to_file):
            file_path = future_to_file[future]
            try:
                fp = future.result()
                if fp:
                    fingerprints[file_path] = fp
                    # Update database with fingerprint
                    importer.update_fingerprint(file_path, fp, quality=100)
            except Exception as exc:
                log.error("Failed to fingerprint %s: %s", file_path, exc)
    
    # Find duplicates by fingerprint
    fp_map = {}
    for file_path, fp in fingerprints.items():
        if fp not in fp_map:
            fp_map[fp] = []
        fp_map[fp].append(file_path)
    
    # Filter for groups with 2+ files
    duplicate_groups = [
        {
            "fingerprint": fp,
            "files": file_list,
            "method": "acoustic_fingerprint",
        }
        for fp, file_list in fp_map.items()
        if len(file_list) >= 2
    ]
    
    log.info("Found %d duplicate groups by acoustic fingerprint", len(duplicate_groups))
    return duplicate_groups


def get_duplicate_groups_legacy_compatible(
    result: DatabaseDuplicateResult,
) -> List[Dict[str, Any]]:
    """
    Convert database result to legacy-compatible format.
    
    Args:
        result: DatabaseDuplicateResult
        
    Returns:
        List of duplicate groups in legacy format
    """
    groups = []
    
    # Add hash-based duplicates
    for hash_dupe in result.hash_duplicates:
        try:
            db = FableGearDatabase()
            records = [db.get_content_by_id(rid) for rid in hash_dupe["record_ids"]]
            
            group = {
                "files": [record.file_path for record in records if record],
                "method": "file_hash",
                "hash": hash_dupe["hash"],
            }
            groups.append(group)
        except Exception as exc:
            log.error("Failed to convert hash duplicate: %s", exc)
    
    # Add fingerprint-based duplicates
    for fp_dupe in result.fingerprint_duplicates:
        group = {
            "files": [str(f) for f in fp_dupe["files"]],
            "method": "acoustic_fingerprint",
            "fingerprint": fp_dupe["fingerprint"],
        }
        groups.append(group)
    
    return groups


def hybrid_duplicate_detection(
    root: "Path | list[Path]",
    *,
    max_workers: int = 1,
    match_mode: str = "exact",
    fuzzy_threshold: float = 0.85,
) -> List[Dict[str, Any]]:
    """
    Hybrid duplicate detection that tries database first, falls back to legacy.
    
    This function provides a smooth transition from the old system to the
    new database-first approach.
    
    Args:
        root: Directory or list of directories to scan
        max_workers: Number of workers for fingerprinting
        match_mode: Matching strategy
        fuzzy_threshold: Fuzzy matching threshold
        
    Returns:
        List of duplicate groups
    """
    try:
        # Try database-first approach
        result = scan_duplicates_database_first(
            root,
            max_workers=max_workers,
            match_mode=match_mode,
            fuzzy_threshold=fuzzy_threshold,
        )
        
        # Convert to legacy format
        groups = get_duplicate_groups_legacy_compatible(result)
        
        if groups:
            log.info("Database-first detection found %d duplicate groups", len(groups))
            return groups
        else:
            log.info("No duplicates found in database, falling back to legacy")
            
    except Exception as exc:
        log.warning("Database-first detection failed, falling back to legacy: %s", exc)
    
    # Fallback to legacy system
    from chop_shop.duplicate_detector import scan_duplicates
    legacy_result = scan_duplicates(
        root,
        max_workers=max_workers,
        match_mode=match_mode,
        fuzzy_threshold=fuzzy_threshold,
    )
    
    # Convert legacy result to our format
    return [
        {
            "files": [str(f) for f in group.files],
            "method": "legacy_acoustic",
        }
        for group in legacy_result.groups
    ]