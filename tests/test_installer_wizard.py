"""
Installer wizard tests.

These cover the logic that can be verified without a real macOS box, real
Homebrew, or a display: system-check functions (each one independent, none
allowed to raise), the dependency plan (must be read from the real
requirements files, not hand-duplicated), the SSE command-streaming format,
and the exact bug this module already caught once during development --
the bare `bash -c "$(curl ...)"` idiom silently breaking when built as a
Python argv list instead of a shell line, because there is no outer shell
left to perform the substitution before bash sees it.

What these do NOT cover: whether Homebrew, ffmpeg, or pip installs actually
succeed on a real Mac. That's an integration property this suite has no way
to observe, and pretending otherwise would be worse than not testing it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import installer_wizard as wiz

# ─── System checks: independent, never raise ──────────────────────────────

CHECK_FUNCS = [
    wiz.check_platform,
    wiz.check_disk_space,
    wiz.check_network,
    wiz.check_homebrew,
    wiz.check_python_version,
    wiz.check_venv,
]


@pytest.mark.parametrize("fn", CHECK_FUNCS, ids=[f.__name__ for f in CHECK_FUNCS])
def test_check_never_raises_and_returns_valid_status(fn, monkeypatch):
    """
    A check that crashes the wizard over an absent `sw_vers` or a flaky
    network probe is worse than one that reports "warn" and lets the user
    proceed. Force every external call this check might make to fail, and
    confirm it still returns a well-formed CheckResult.
    """
    monkeypatch.setattr(subprocess, "run", _always_fails)
    monkeypatch.setattr("socket.create_connection", _raise_oserror)

    result = fn()

    assert isinstance(result, wiz.CheckResult)
    assert result.status in ("ok", "warn", "fail")
    assert result.label
    assert result.id


def _always_fails(*_a, **_k):
    raise FileNotFoundError("no such command in this sandbox")


def _raise_oserror(*_a, **_k):
    raise OSError("network unreachable")


def test_check_python_version_reflects_actual_interpreter():
    result = wiz.check_python_version()
    assert result.status == "ok"
    assert "Python 3." in result.detail


def test_check_python_version_fails_below_minimum(monkeypatch):
    import sys
    from collections import namedtuple

    FakeVersion = namedtuple("FakeVersion", "major minor micro")
    monkeypatch.setattr(sys, "version_info", FakeVersion(3, 9, 0))
    result = wiz.check_python_version()
    assert result.status == "fail"


def test_check_disk_space_reports_free_bytes():
    result = wiz.check_disk_space()
    assert result.status in ("ok", "fail")
    assert "GB free" in result.detail


def test_check_venv_absent_is_warn_not_fail(tmp_path, monkeypatch):
    """No venv yet is the ordinary state before the first install step runs."""
    monkeypatch.setattr(wiz, "VENV_DIR", tmp_path / "venv")
    result = wiz.check_venv()
    assert result.status == "warn"


def test_check_venv_present_is_ok(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").touch()
    monkeypatch.setattr(wiz, "VENV_DIR", venv)
    result = wiz.check_venv()
    assert result.status == "ok"


def test_run_all_checks_covers_both_formulas():
    ids = {c.id for c in wiz.run_all_checks()}
    for formula in wiz.BREW_FORMULAS:
        assert f"formula:{formula}" in ids


# ─── Dependency plan: read from the real files, never duplicated ─────────

def test_parse_requirements_skips_comments_and_blank_lines(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("# a comment\n\nflask>=3.0\n\n# another\nmutagen==1.47\n")
    assert wiz._parse_requirements(req) == ["flask", "mutagen"]


def test_parse_requirements_skips_include_directives(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text("-r base.txt\nnumpy>=1.26\n")
    assert wiz._parse_requirements(req) == ["numpy"]


def test_parse_requirements_strips_every_specifier_kind(tmp_path):
    req = tmp_path / "req.txt"
    req.write_text(
        "pkg-a==1.0\npkg-b>=1.0\npkg-c<=1.0\npkg-d~=1.0\npkg-e>1.0\npkg-f<1.0\npkg-g\n"
    )
    assert wiz._parse_requirements(req) == [
        "pkg-a", "pkg-b", "pkg-c", "pkg-d", "pkg-e", "pkg-f", "pkg-g",
    ]


def test_parse_requirements_missing_file_returns_empty(tmp_path):
    assert wiz._parse_requirements(tmp_path / "nonexistent.txt") == []


def test_build_plan_reads_the_real_requirements_files():
    """
    This is the test that keeps the wizard's displayed plan honest. If it
    ever starts hand-listing packages instead of reading requirements.txt,
    a real dependency change (mutagen leaving once Anvil is wired in, for
    instance) would go stale here silently. Reading the real file means it
    can't.
    """
    plan = wiz.build_plan()
    by_id = {g.id: g for g in plan}

    assert by_id["ui"].required is True
    assert by_id["optional"].required is False

    core_packages = set(by_id["core"].packages)
    on_disk = set(wiz._parse_requirements(wiz.REPO_ROOT / "requirements.txt"))
    assert core_packages == on_disk

    # Honesty check for the exact framing in the PR: today's real
    # requirements.txt still lists mutagen/librosa, because Anvil replaces
    # mutagen's *code* but nothing has been wired in yet, and Iron (the
    # librosa/essentia replacement) doesn't exist yet either. The wizard
    # must not claim otherwise.
    assert "mutagen" in core_packages
    assert "librosa" in core_packages
    assert "essentia" in set(by_id["optional"].packages)


def test_build_plan_groups_have_no_duplicate_files():
    files = [g.file for g in wiz.build_plan()]
    assert len(files) == len(set(files))


# ─── SSE command streaming ─────────────────────────────────────────────────

def test_stream_command_yields_sse_framed_lines():
    lines = list(wiz.stream_command(["echo", "hello"]))
    assert lines[0].startswith("data: $ echo hello")
    assert any("hello" in line for line in lines)
    assert lines[-1] == "data: [DONE:0]\n\n"


def test_stream_command_reports_nonzero_exit():
    lines = list(wiz.stream_command(["/bin/sh", "-c", "exit 7"]))
    assert lines[-1] == "data: [DONE:7]\n\n"


def test_stream_command_missing_binary_reports_error_not_exception():
    lines = list(wiz.stream_command(["/no/such/binary/anywhere"]))
    assert any(line.startswith("data: [ERROR]") for line in lines)
    assert lines[-1].startswith("data: [DONE:1]")


def test_stream_steps_stops_at_first_failure():
    """A later step must never run after an earlier one in the same
    sequence has already failed -- installing package B after package A's
    install failed would leave a half-installed, hard-to-diagnose state."""
    steps = [
        ["/bin/sh", "-c", "echo first; exit 1"],
        ["/bin/sh", "-c", "echo SHOULD_NOT_RUN"],
    ]
    lines = list(wiz.stream_steps(steps))
    joined = "".join(lines)
    assert "first" in joined
    assert "SHOULD_NOT_RUN" not in joined
    assert lines[-1] == "data: [SEQUENCE_FAILED]\n\n"


def test_stream_steps_all_succeed():
    steps = [["echo", "one"], ["echo", "two"]]
    lines = list(wiz.stream_steps(steps))
    assert lines[-1] == "data: [SEQUENCE_OK]\n\n"


# ─── The word-splitting regression ─────────────────────────────────────────
#
# Caught during development: `bash -c "$(curl ...)"` built as a Python argv
# list has no outer shell to perform the substitution before bash -c sees
# it, so bash evaluates $(...) itself in bare command position, the result
# gets word-split on whitespace/newlines, and the downloaded script's own
# "#!/bin/bash" first line becomes an attempted command name -- failing with
# "No such file or directory" despite curl succeeding and the script being
# perfectly valid. This is exactly the class of bug a smoke test would not
# catch (the command "runs"; it just runs the wrong thing).

def test_brew_install_uses_pipe_not_bare_command_substitution(monkeypatch):
    """
    The specific idiom matters, not just that a Homebrew URL appears
    somewhere. `bash -c "$(curl ...)"` (unquoted from Python's perspective)
    is the regression; `curl ... | bash` is the fix. Assert the actual form,
    not just the presence of the URL, so a future edit that reintroduces the
    broken idiom fails this test even if it still "mentions curl".
    """
    monkeypatch.setattr(wiz, "_find_brew", lambda: None)
    cmds = wiz.brew_install_commands()

    install_cmd = cmds[0]
    assert install_cmd[:2] == ["/bin/bash", "-c"]
    script = install_cmd[2]

    # The regression: a bare, unquoted $(...) used as the entire command.
    assert not script.strip().startswith("$(")
    # The fix: curl's output is piped to a shell, one clean argv string,
    # nothing for bash to word-split.
    assert " | /bin/bash" in script
    assert "curl -fsSL" in script


def test_brew_install_skips_homebrew_step_when_already_present(monkeypatch):
    monkeypatch.setattr(wiz, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    cmds = wiz.brew_install_commands()
    # Every command should be a formula install, not the bootstrap installer.
    assert all("curl" not in " ".join(c) for c in cmds)
    assert len(cmds) == len(wiz.BREW_FORMULAS)


def test_python_install_commands_cover_every_requirements_file():
    cmds = wiz.python_install_commands()
    joined = [" ".join(c) for c in cmds]
    assert any("--upgrade" in c and "pip" in c for c in joined)
    for group in wiz.build_plan():
        if (wiz.REPO_ROOT / group.file).is_file():
            assert any(group.file in c for c in joined)


def test_python_install_prefers_venv_python_when_present(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    fake_python = venv / "bin" / "python"
    fake_python.touch()
    monkeypatch.setattr(wiz, "VENV_DIR", venv)

    cmds = wiz.python_install_commands()
    assert cmds[0][0] == str(fake_python)


# ─── Flask app wiring ──────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "TEMPLATE_PATH", Path(wiz.__file__).parent / "templates" / "installer_wizard.html")
    app = wiz.create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Welcome to FableGear" in resp.data


def test_api_checks_returns_json_list(client):
    resp = client.get("/api/checks")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert all({"id", "label", "status", "detail"} <= set(item) for item in data)


def test_api_plan_returns_the_three_groups(client):
    resp = client.get("/api/plan")
    data = resp.get_json()
    assert {g["id"] for g in data} == {"ui", "core", "optional"}


def test_api_complete_touches_sentinel_and_returns_ok(client, monkeypatch, tmp_path):
    sentinel_dir = tmp_path
    monkeypatch.setattr(wiz, "REPO_ROOT", sentinel_dir)
    monkeypatch.setattr(wiz, "_launch_app_and_exit", lambda: None)

    resp = client.post("/api/complete")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert (sentinel_dir / ".fablegear_ready").exists()
