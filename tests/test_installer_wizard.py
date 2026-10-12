"""Install wizard: checks, install sequences, and the localhost server's guards.

The wizard is stdlib-only and standalone (see installer_wizard.py's docstring), so
these tests import nothing from the rest of FableGear and need no Flask.
"""
import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import installer_wizard as wiz

CHECK_FUNCS = [
    wiz.check_platform,
    wiz.check_disk_space,
    wiz.check_network,
    wiz.check_python,
    wiz.check_venv,
    lambda: wiz.check_tool("ffmpeg", "ffmpeg", "audio"),
]


# ── Checks ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fn", CHECK_FUNCS)
def test_check_never_raises_and_returns_valid_status(fn, monkeypatch):
    monkeypatch.setattr(wiz, "_run", lambda *a, **k: None)  # every probe "fails"
    result = fn()
    assert result.status in ("ok", "warn", "fail")
    assert result.label and isinstance(result.detail, str)


def test_find_python_prefers_supported_version_over_newer(monkeypatch):
    versions = {"/bin/p313": (3, 13, 1), "/bin/p314": (3, 14, 0), "/bin/p39": (3, 9, 6)}
    monkeypatch.setattr(wiz, "PYTHON_CANDIDATES", ("p314", "p39", "p313"))
    monkeypatch.setattr(wiz, "find_tool", lambda n: {"p314": "/bin/p314", "p39": "/bin/p39",
                                                     "p313": "/bin/p313"}.get(n))
    monkeypatch.setattr(wiz.os.path, "isfile", lambda p: p in versions)
    monkeypatch.setattr(wiz, "_python_version", lambda exe: versions.get(exe))
    assert wiz.find_python() == ("/bin/p313", (3, 13, 1))


def test_find_python_none_when_only_unsupported(monkeypatch):
    versions = {"/bin/p314": (3, 14, 0), "/bin/p39": (3, 9, 6)}
    monkeypatch.setattr(wiz, "PYTHON_CANDIDATES", ("p314", "p39"))
    monkeypatch.setattr(wiz, "find_tool", lambda n: "/bin/" + n)
    monkeypatch.setattr(wiz.os.path, "isfile", lambda p: p in versions)
    monkeypatch.setattr(wiz, "_python_version", lambda exe: versions.get(exe))
    assert wiz.find_python() is None
    result = wiz.check_python()
    assert result.status == "fail" and "python.org" in result.detail


def test_missing_tool_fails_with_install_advice_but_never_installs(monkeypatch):
    monkeypatch.setattr(wiz, "find_tool", lambda name: None)
    monkeypatch.setattr(wiz, "_has_homebrew", lambda: True)
    result = wiz.check_tool("fpcalc", "Chromaprint (fpcalc)", "fingerprinting")
    assert result.status == "fail"
    assert "brew install ffmpeg chromaprint" in result.detail  # advice only
    monkeypatch.setattr(wiz, "_has_homebrew", lambda: False)
    assert "brew" not in wiz.check_tool("fpcalc", "x", "y").detail


def test_present_tool_is_ok(monkeypatch):
    monkeypatch.setattr(wiz, "find_tool", lambda name: "/usr/local/bin/" + name)
    assert wiz.check_tool("ffmpeg", "ffmpeg", "audio").status == "ok"


def test_check_venv_states(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "VENV_DIR", tmp_path / "venv")
    assert wiz.check_venv().status == "warn"  # absent is not a failure
    (tmp_path / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "venv" / "bin" / "activate").write_text("")
    assert wiz.check_venv().status == "ok"


def test_run_all_checks_covers_python_and_both_tools_but_not_homebrew():
    ids = [c.id for c in wiz.run_all_checks()]
    assert "python" in ids and "tool:ffmpeg" in ids and "tool:fpcalc" in ids
    assert not any("brew" in i or i.startswith("formula:") for i in ids)


def test_wizard_has_no_homebrew_or_system_install_code():
    source = (REPO_ROOT / "installer_wizard.py").read_text()
    assert not hasattr(wiz, "brew_install_commands")
    assert "Homebrew/install" not in source
    assert '"install", formula' not in source
    html = (REPO_ROOT / "templates" / "installer_wizard.html").read_text().lower()
    assert "api/install/brew" not in html and "homebrew" not in html


# ── Requirements parsing / plan ──────────────────────────────────────────────

def test_parse_requirements_skips_comments_blanks_and_includes(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("# c\n\n-r other.txt\nflask>=3.0\n")
    assert wiz._parse_requirements(req) == ["flask"]


def test_parse_requirements_strips_every_specifier_kind(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("a==1\nb>=2\nc<=3\nd~=4\ne>5\nf<6\ng\n")
    assert wiz._parse_requirements(req) == list("abcdefg")


def test_parse_requirements_missing_file_returns_empty(tmp_path):
    assert wiz._parse_requirements(tmp_path / "nope.txt") == []


def test_build_plan_reads_the_real_requirements_files():
    plan = {g.id: g for g in wiz.build_plan()}
    assert set(plan) == {"ui", "core", "optional"}
    assert plan["ui"].required and plan["core"].required and not plan["optional"].required
    assert "flask" in plan["ui"].packages
    assert "flask-sock" not in plan["ui"].packages  # phone companion is gone
    assert all(g.packages for g in plan.values() if g.id != "optional")


# ── Streaming primitives ─────────────────────────────────────────────────────

def test_stream_command_frames_lines_and_reports_exit():
    out = list(wiz.stream_command([sys.executable, "-c", "print('hi')"]))
    assert out[0].startswith("data: $ ") and "data: hi\n\n" in out and out[-1] == "data: [DONE:0]\n\n"
    bad = list(wiz.stream_command([sys.executable, "-c", "raise SystemExit(3)"]))
    assert bad[-1] == "data: [DONE:3]\n\n"


def test_stream_command_missing_binary_reports_error_not_exception():
    out = list(wiz.stream_command(["/definitely/not/a/binary"]))
    assert any("[ERROR]" in line for line in out) and out[-1] == "data: [DONE:1]\n\n"


def test_stream_steps_stops_at_first_failure():
    ran = list(wiz.stream_steps([
        [sys.executable, "-c", "print('one')"],
        [sys.executable, "-c", "raise SystemExit(1)"],
        [sys.executable, "-c", "print('never')"],
    ]))
    text = "".join(ran)
    assert "one" in text and "never" not in text
    assert ran[-1] == "data: [SEQUENCE_FAILED]\n\n"


def test_stream_steps_all_succeed():
    ran = list(wiz.stream_steps([[sys.executable, "-c", "print('a')"]]))
    assert ran[-1] == "data: [SEQUENCE_OK]\n\n"


# ── Environment + package sequences ──────────────────────────────────────────

def _fake_venv(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("")
    monkeypatch.setattr(wiz, "VENV_DIR", venv)
    return venv


def _ok(label="x"):
    return [sys.executable, "-c", f"print('{label}')"]


def _fail():
    return [sys.executable, "-c", "raise SystemExit(1)"]


def test_python_install_refuses_to_run_without_a_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "VENV_DIR", tmp_path / "no-venv")
    monkeypatch.setattr(wiz, "required_install_commands",
                        lambda: pytest.fail("must not install into the system Python"))
    out = list(wiz.stream_python_install())
    assert out[-1] == "data: [SEQUENCE_FAILED]\n\n"


def test_python_install_required_then_optional_ok(tmp_path, monkeypatch):
    _fake_venv(tmp_path, monkeypatch)
    monkeypatch.setattr(wiz, "required_install_commands", lambda: [_ok("req")])
    monkeypatch.setattr(wiz, "optional_install_command", lambda: _ok("opt"))
    text = "".join(wiz.stream_python_install())
    assert "req" in text and "opt" in text
    assert "OPTIONAL_SKIPPED" not in text and text.rstrip().endswith("[SEQUENCE_OK]")


def test_python_install_required_failure_stops_before_optional(tmp_path, monkeypatch):
    _fake_venv(tmp_path, monkeypatch)
    monkeypatch.setattr(wiz, "required_install_commands", lambda: [_fail()])
    monkeypatch.setattr(wiz, "optional_install_command",
                        lambda: pytest.fail("optional must not run after a required failure"))
    out = list(wiz.stream_python_install())
    assert out[-1] == "data: [SEQUENCE_FAILED]\n\n"
    assert not any("SEQUENCE_OK" in line for line in out)


def test_python_install_optional_failure_never_fails_the_install(tmp_path, monkeypatch):
    _fake_venv(tmp_path, monkeypatch)
    monkeypatch.setattr(wiz, "required_install_commands", lambda: [_ok("req")])
    monkeypatch.setattr(wiz, "optional_install_command", lambda: _fail())
    out = list(wiz.stream_python_install())
    assert "data: [OPTIONAL_SKIPPED]\n\n" in out
    assert out[-1] == "data: [SEQUENCE_OK]\n\n"
    assert not any("SEQUENCE_FAILED" in line for line in out)


def test_venv_setup_fails_cleanly_without_a_supported_python(monkeypatch):
    monkeypatch.setattr(wiz, "find_python", lambda: None)
    assert list(wiz.stream_venv_setup())[-1] == "data: [SEQUENCE_FAILED]\n\n"


def test_venv_setup_rebuilds_a_hollow_venv(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "lib").mkdir(parents=True)  # exists, but no bin/activate
    (venv / "lib" / "stale").write_text("x")
    monkeypatch.setattr(wiz, "VENV_DIR", venv)
    monkeypatch.setattr(wiz, "find_python", lambda: ("/usr/bin/pythonX", (3, 12, 0)))
    seen = []
    monkeypatch.setattr(wiz, "stream_steps",
                        lambda steps, cwd=None: iter(seen.append(steps) or ["data: [SEQUENCE_OK]\n\n"]))
    out = "".join(wiz.stream_venv_setup())
    assert "rebuilding" in out and not (venv / "lib" / "stale").exists()
    assert seen[0][0][:3] == ["/usr/bin/pythonX", "-m", "venv"]
    assert "pip" in seen[0][1]


def test_venv_setup_leaves_a_healthy_venv_alone(tmp_path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("")
    (venv / "bin" / "python").write_text("")
    (venv / "keep").write_text("x")
    monkeypatch.setattr(wiz, "VENV_DIR", venv)
    monkeypatch.setattr(wiz, "find_python", lambda: ("/usr/bin/pythonX", (3, 12, 0)))
    monkeypatch.setattr(wiz, "_python_version", lambda exe: (3, 12, 0))
    seen = []
    monkeypatch.setattr(wiz, "stream_steps",
                        lambda steps, cwd=None: iter(seen.append(steps) or ["data: [SEQUENCE_OK]\n\n"]))
    list(wiz.stream_venv_setup())
    assert (venv / "keep").exists()
    assert len(seen[0]) == 1 and "pip" in seen[0][0]  # only the pip upgrade, no re-create


# ── Launcher script ──────────────────────────────────────────────────────────

def test_launcher_script_is_valid_bash_and_shared_with_setup_sh():
    script = REPO_ROOT / "packaging" / "create_launcher_app.sh"
    assert script.is_file()
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    assert "create_launcher_app.sh" in (REPO_ROOT / "setup.sh").read_text()


def test_create_launcher_app_missing_script_is_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(wiz, "LAUNCHER_SCRIPT", tmp_path / "missing.sh")
    assert wiz.create_launcher_app() is False


# ── HTTP server ──────────────────────────────────────────────────────────────

@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(wiz, "create_launcher_app", lambda: False)
    monkeypatch.setattr(wiz, "_launch_app_and_exit", lambda: None)
    srv = wiz.WizardServer(("127.0.0.1", 0), token="TESTTOKEN")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, host=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{srv.port}")
    conn.putheader("Content-Length", str(len(body or b"")))
    conn.endheaders(body)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_index_serves_page_with_the_run_token_injected(server):
    status, body = _req(server, "GET", "/")
    html = body.decode()
    assert status == 200 and "TESTTOKEN" in html and wiz.TOKEN_PLACEHOLDER not in html


def test_api_checks_and_plan_return_json(server):
    status, body = _req(server, "GET", "/api/checks")
    assert status == 200 and {c["id"] for c in json.loads(body)} >= {"python", "tool:ffmpeg"}
    status, body = _req(server, "GET", "/api/plan")
    assert status == 200 and [g["id"] for g in json.loads(body)] == ["ui", "core", "optional"]


def test_non_loopback_host_header_is_rejected(server):
    assert _req(server, "GET", "/", host="evil.example.com")[0] == 403
    assert _req(server, "GET", "/api/checks", host="evil.example.com:80")[0] == 403


@pytest.mark.parametrize("path", ["/api/install/venv", "/api/install/python",
                                  "/api/install/venv?t=wrong", "/api/install/python?t="])
def test_install_endpoints_require_the_token(server, path):
    assert _req(server, "GET", path)[0] == 403


def test_complete_requires_token_and_writes_the_ready_marker(server, tmp_path):
    assert _req(server, "POST", "/api/complete")[0] == 403
    assert not (tmp_path / ".fablegear_ready").exists()
    status, body = _req(server, "POST", "/api/complete?t=TESTTOKEN")
    assert status == 200 and json.loads(body) == {"ok": True, "launcher": False}
    assert (tmp_path / ".fablegear_ready").exists()


@pytest.mark.parametrize("path", ["/static/../installer_wizard.py", "/static/..%2Finstaller_wizard.py",
                                  "/static/%2e%2e/installer_wizard.py", "/nope"])
def test_static_cannot_escape_and_unknown_paths_404(server, path):
    assert _req(server, "GET", path)[0] == 404


def test_static_serves_real_assets(server):
    assert _req(server, "GET", "/static/fablegear.css")[0] == 200


# ── Review fixes: venv bounds, and success must be an explicit marker ──────────

@pytest.mark.parametrize("version,usable", [((3, 13, 1), True), ((3, 11, 0), True), ((3, 14, 0), False),
                                            ((3, 10, 9), False), (None, False)])
def test_existing_venv_is_reused_only_inside_the_supported_range(tmp_path, monkeypatch, version, usable):
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "activate").write_text("")
    monkeypatch.setattr(wiz, "VENV_DIR", venv)
    monkeypatch.setattr(wiz, "_python_version", lambda exe: version)
    assert wiz._venv_is_usable() is usable


def test_page_treats_a_closed_stream_without_the_success_marker_as_a_failure():
    html = (REPO_ROOT / "templates" / "installer_wizard.html").read_text()
    # both install streams track the explicit marker and fail when the connection closes without it
    assert html.count("let sawOk = false;") == 2
    assert html.count('data === "[SEQUENCE_OK]") { sawOk = true; return; }') == 2
    assert html.count("if (!failed && !sawOk) {") == 2
    assert "finalizeRows();" in html  # a confirmed success settles every row, including the last 'installing' one


def test_install_stream_logic_in_the_page_behaves_under_scripted_event_streams():
    """Runs the page's real streamInstall() under Node with scripted success / dropped / failed streams."""
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = REPO_ROOT / "tests" / "js" / "stream_install_check.js"
    page = REPO_ROOT / "templates" / "installer_wizard.html"
    done = subprocess.run([node, str(script), str(page)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
