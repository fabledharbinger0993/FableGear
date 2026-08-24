"""
update_checker.py — GitHub release update checker for FableGear.

Checks whether a newer release exists on GitHub at startup (after a short
delay) and caches the result. The Flask API exposes /api/update/status so
the frontend can show a banner without blocking page load.

Public API
----------
start_background_checker()  — call once at app startup
get_status()                — return the last cached status dict (non-blocking)

Status dict shape:
    {
        "update_available": bool,
        "current_version":  str | None,   # local git tag or commit SHA (short)
        "latest_version":   str | None,   # latest GitHub release tag
        "release_url":      str | None,   # HTML URL of the latest release page
        "download_url":     str | None,   # direct download URL for FableGear.zip
        "is_git_install":   bool,         # False if running from a ZIP extract
        "checked_at":       str | None,   # ISO timestamp of last check
        "error":            str | None,
    }
"""

import logging
import subprocess
import threading
import time
import urllib.request
import urllib.error
import json
import ssl
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_GITHUB_API    = "https://api.github.com/repos/fabledharbinger0993/FableGear/releases/latest"
_DOWNLOAD_URL  = "https://github.com/fabledharbinger0993/FableGear/releases/latest/download/FableGear.zip"
_STARTUP_DELAY = 5        # seconds after boot before first check (non-blocking) —
                          # kept short so the first page load can surface the
                          # permission modal on its 45 s re-check
_REQUEST_TIMEOUT = 8      # seconds for the GitHub API call

_lock: threading.Lock = threading.Lock()
_status: dict = {
    "update_available": False,
    "current_version":  None,
    "latest_version":   None,
    "release_url":      None,
    "download_url":     None,
    "is_git_install":   False,
    "checked_at":       None,
    "error":            None,
}


# ── Core check ────────────────────────────────────────────────────────────────

def check_now() -> dict:
    """
    Hit the GitHub releases API, compare against the local install, update cache.
    Returns the new status dict.
    """
    log.info("update_checker: checking for FableGear updates …")
    
    # 1. Update local git knowledge
    script_dir = Path(__file__).parent
    subprocess.run(["git", "fetch", "--tags"], cwd=script_dir, capture_output=True)

    # 2. Get local version once
    current_version, is_git = _local_version()

    try:
        # 3. Fetch remote info
        data = _fetch_latest_release()

        latest_tag  = data.get("tag_name", "")
        release_url = data.get("html_url", "")

        # 4. Compare
        update_available = _is_newer(latest_tag, current_version, is_git)
        # ... rest of the cache update logic

        _update_cache(
            update_available=update_available,
            current_version=current_version,
            latest_version=latest_tag,
            release_url=release_url,
            download_url=_DOWNLOAD_URL if update_available else None,
            is_git_install=is_git,
            error=None,
        )

        if update_available:
            log.info(
                "update_checker: update available — local=%s latest=%s",
                current_version, latest_tag,
            )
        else:
            log.info(
                "update_checker: FableGear is up to date (local=%s latest=%s)",
                current_version, latest_tag,
            )

    except urllib.error.URLError as exc:
        msg = f"Could not reach GitHub ({exc.reason})"
        log.info("update_checker: %s", msg)
        _update_cache(error=msg, is_git_install=is_git, current_version=current_version)

    except Exception as exc:
        log.warning("update_checker: check failed — %s", exc)
        _update_cache(error=str(exc), is_git_install=is_git, current_version=current_version)

    return get_status()


def _fetch_latest_release() -> dict:
    req = urllib.request.Request(
        _GITHUB_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "FableGear-update-checker/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        if "CERTIFICATE_VERIFY_FAILED" not in reason:
            raise

    # macOS Python environments sometimes miss system trust roots; retry with
    # certifi's CA bundle if available.
    try:
        import certifi  # type: ignore
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT, context=ctx) as resp:
            return json.loads(resp.read())
    except Exception:
        raise


def get_status() -> dict:
    """Return the last cached status (never blocks)."""
    with _lock:
        return dict(_status)


# ── Addition ─────────────────────────────────────────────────────────────────

def _is_newer(latest_tag: str, current: str | None, is_git: bool) -> bool:
    if not latest_tag:
        return False

    if not is_git:
        return bool(current) and _is_semver_tag(current) and _is_semver_tag(latest_tag) \
            and _semver_gt(latest_tag, current)

    if not current:
        return False

    # Authoritative: does HEAD already contain the release commit?
    git_answer = _local_tag_is_current(latest_tag)
    if git_answer is True:
        return False
    if git_answer is False:
        return True

    # REWRITE: If we don't know the ancestry, compare tags directly 
    # if both are semver, OR assume latest is newer if local is just a SHA.
    if _is_semver_tag(latest_tag):
        if _is_semver_tag(current):
            return _semver_gt(latest_tag, current)
        return True # Remote is a version, local is just a SHA/unknown -> Update!

    return False

# ── Internals ─────────────────────────────────────────────────────────────────

def _local_version() -> tuple[str | None, bool]:
    """
    Return (version_string, is_git_install).

    For git installs: tries the most recent tag first (e.g. "v1.0.0"), falls
    back to the short commit SHA (e.g. "abc1234") if no tags exist.
    For ZIP installs: returns (None, False).
    """
    script_dir = Path(__file__).parent

    # Is this a git repo at all?
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=script_dir,
            capture_output=True,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None, False   # ZIP install — no git

    # Try latest tag first
    try:
        tag = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if tag.returncode == 0 and tag.stdout.strip():
            return tag.stdout.strip(), True
    except Exception:
        pass

    # Fall back to short SHA
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if sha.returncode == 0:
            return sha.stdout.strip(), True
    except Exception:
        pass

    return None, True


def _is_semver_tag(s: str) -> bool:
    """True only for strict version strings like 'v1.2.3' or '1.2' — NOT for
    commit SHAs that merely happen to start with a digit (e.g. '12aff07')."""
    import re
    return bool(re.fullmatch(r"v?\d+(\.\d+)*", s or ""))


def _semver_gt(a: str, b: str) -> bool:
    """True if version `a` is strictly greater than `b`, length-tolerant
    (1.0 vs 1.0.0 compare equal). Both must be semver-shaped."""
    def parts(t: str) -> tuple[int, ...]:
        return tuple(int(x) for x in t.lstrip("v").split(".") if x.isdigit())
    pa, pb = parts(a), parts(b)
    n = max(len(pa), len(pb))
    pa += (0,) * (n - len(pa))
    pb += (0,) * (n - len(pb))
    return pa > pb


def _local_tag_is_current(latest_tag: str) -> "bool | None":
    """Authoritative git check: is `latest_tag`'s commit already in local HEAD?

    Returns True if HEAD already contains the release tag's commit (no update
    needed), False if the release tag is a commit we don't have (update
    available), or None if it can't be determined (tag not fetched locally)."""
    script_dir = Path(__file__).parent
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{latest_tag}^{{commit}}"],
            cwd=script_dir, capture_output=True, text=True, timeout=5,
        )
        if rev.returncode != 0 or not rev.stdout.strip():
            return None  # tag not present locally — can't compare via git
        tag_commit = rev.stdout.strip()
        anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", tag_commit, "HEAD"],
            cwd=script_dir, capture_output=True, timeout=5,
        )
        # exit 0 => tag_commit is an ancestor of HEAD => we already have it
        return anc.returncode == 0
    except Exception:
        return None


def _is_newer(latest_tag: str, current: str | None, is_git: bool) -> bool:
    """
    Return True if the latest GitHub release is newer than the local install.

    - ZIP installs (not git): offer the download only if the bundled version is
      a real, older semver than the release. If our version is unknown, stay
      silent rather than nag forever.
    - Git installs: prefer an authoritative git ancestry check; fall back to
      strict semver comparison only when BOTH sides are real version tags.
    """
    if not latest_tag:
        return False

    if not is_git:
        return bool(current) and _is_semver_tag(current) and _is_semver_tag(latest_tag) \
            and _semver_gt(latest_tag, current)

    if not current:
        return False

    # Authoritative: does HEAD already contain the release commit?
    git_answer = _local_tag_is_current(latest_tag)
    if git_answer is True:
        return False
    if git_answer is False:
        return True

    # Tag not fetched locally — fall back to semver, but ONLY if BOTH strings
    # are real versions. A commit SHA is never a version. This is the fix for
    # the loop: SHAs like '12aff07' previously parsed to an empty tuple and
    # compared as older than every release, so the banner never cleared.
    if _is_semver_tag(current) and _is_semver_tag(latest_tag):
        return _semver_gt(latest_tag, current)

    return False


def _update_cache(**kwargs) -> None:
    with _lock:
        for k, v in kwargs.items():
            if k in _status:
                _status[k] = v
        _status["checked_at"] = datetime.now().isoformat(timespec="seconds")


def _background_loop() -> None:
    time.sleep(_STARTUP_DELAY)
    check_now()


def start_background_checker() -> None:
    """Start the one-shot background check. Call once at app startup."""
    t = threading.Thread(target=_background_loop, daemon=True, name="update-checker")
    t.start()
    log.info("update_checker: will check for updates in %ds", _STARTUP_DELAY)
