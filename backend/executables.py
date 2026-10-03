"""Executable discovery helpers for subprocesses."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_executable(name: str, *, env_var: str | None = None) -> str:
    """Return an absolute executable path, including Windows app-managed shims."""
    if env_var and (configured := os.environ.get(env_var)):
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise FileNotFoundError(f"{env_var} points to a missing executable: {path}")

    if found := shutil.which(name):
        return str(Path(found).resolve())

    if os.name == "nt" and name.casefold() == "codex":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            codex_bin = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
            candidates = sorted(
                codex_bin.glob("*/codex.exe"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                return str(candidates[0].resolve())

    hint = f" Set {env_var} to its full path." if env_var else ""
    raise FileNotFoundError(f"Executable '{name}' was not found.{hint}")
