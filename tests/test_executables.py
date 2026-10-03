from pathlib import Path

import pytest

from backend.executables import resolve_executable


def test_resolve_executable_uses_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_text("fixture")
    monkeypatch.setattr("backend.executables.shutil.which", lambda _name: str(executable))

    assert resolve_executable("codex") == str(executable.resolve())


def test_resolve_executable_honors_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    executable = tmp_path / "custom-codex.exe"
    executable.write_text("fixture")
    monkeypatch.setenv("CODEX_EXECUTABLE", str(executable))

    assert resolve_executable("codex", env_var="CODEX_EXECUTABLE") == str(executable.resolve())


def test_resolve_executable_rejects_missing_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.exe"
    monkeypatch.setenv("CODEX_EXECUTABLE", str(missing))

    with pytest.raises(FileNotFoundError, match="CODEX_EXECUTABLE"):
        resolve_executable("codex", env_var="CODEX_EXECUTABLE")
