"""An installer-app install has no .git: its version comes from the VERSION file the build stamps."""
import subprocess

import update_checker as uc


def test_version_file_is_read_and_first_line_wins(tmp_path):
    (tmp_path / "VERSION").write_text("v1.2.3\nignored\n")
    assert uc._installed_version_file(tmp_path) == "v1.2.3"


def test_missing_or_empty_version_file_is_none(tmp_path):
    assert uc._installed_version_file(tmp_path) is None
    (tmp_path / "VERSION").write_text("\n")
    assert uc._installed_version_file(tmp_path) is None


def test_non_git_install_uses_the_version_file(tmp_path, monkeypatch):
    (tmp_path / "VERSION").write_text("v1.1.30\n")
    monkeypatch.setattr(uc, "__file__", str(tmp_path / "update_checker.py"))

    def no_git(*a, **k):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(uc.subprocess, "run", no_git)
    assert uc._local_version() == ("v1.1.30", False)


def test_release_build_is_offered_a_newer_release_but_a_local_build_stays_silent():
    assert uc._is_newer("v1.1.31", "v1.1.30", is_git=False) is True
    assert uc._is_newer("v1.1.30", "v1.1.30", is_git=False) is False
    assert uc._is_newer("v1.1.31", "v1.1.30+gabc1234-local", is_git=False) is False
