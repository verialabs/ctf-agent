"""Discover and format model specs accepted by the solver backends."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from backend.config import Settings, resolve_cli
from backend.models import DEFAULT_MODELS


def _claude_help(command: str) -> str:
    executable = resolve_cli(command, "CLAUDE_CLI")
    result = subprocess.run(
        [executable, "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"Claude CLI exited with status {result.returncode}")
    return f"{result.stdout}\n{result.stderr}"


def _parse_claude_help(help_text: str) -> tuple[list[str], list[str]]:
    model_block = re.search(r"--model <model>(.{0,500})", help_text, flags=re.DOTALL)
    aliases = (
        re.findall(r"'([^']+)'", model_block.group(1).split("model's full name", 1)[0])
        if model_block
        else []
    )

    effort_block = re.search(r"--effort <level>(.{0,300})", help_text, flags=re.DOTALL)
    effort_match = re.search(r"\(([^)]+)\)", effort_block.group(1)) if effort_block else None
    efforts = (
        [effort.strip() for effort in effort_match.group(1).split(",") if effort.strip()]
        if effort_match
        else []
    )
    return aliases, efforts


def discover_claude_models(command: str) -> tuple[list[str], list[str]]:
    """Return aliases and efforts advertised by Claude Code or its configured wrapper."""
    help_text = _claude_help(command)
    aliases, efforts = _parse_claude_help(help_text)
    if aliases:
        return aliases, efforts

    executable = resolve_cli(command, "CLAUDE_CLI")
    if Path(executable).name != "claude":
        aliases, efforts = _parse_claude_help(_claude_help("claude"))
        if aliases:
            return aliases, efforts

    raise RuntimeError("Claude CLI did not advertise any model aliases")


async def _codex_rpc(
    proc: asyncio.subprocess.Process,
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    assert proc.stdin and proc.stdout
    message = {"id": request_id, "method": method, "params": params}
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()

    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        if not line:
            raise RuntimeError("Codex app-server exited before replying")
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") != request_id:
            continue
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response.get("result", {})


async def discover_codex_models(command: str) -> list[dict[str, Any]]:
    """Return visible models advertised by ``codex app-server model/list``."""
    executable = resolve_cli(command, "CODEX_CLI")
    proc = await asyncio.create_subprocess_exec(
        executable,
        "app-server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await _codex_rpc(
            proc,
            1,
            "initialize",
            {
                "clientInfo": {"name": "ctf-agent", "version": "2.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        assert proc.stdin
        proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        await proc.stdin.drain()

        result = await _codex_rpc(proc, 2, "model/list", {})
        return [model for model in result.get("data", []) if not model.get("hidden")]
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except TimeoutError:
                proc.kill()
                await proc.wait()


def _format_efforts(model: dict[str, Any]) -> str:
    efforts = [
        item.get("reasoningEffort", "")
        for item in model.get("supportedReasoningEfforts", [])
        if item.get("reasoningEffort")
    ]
    if not efforts:
        return ""
    default = model.get("defaultReasoningEffort")
    rendered = ", ".join(
        f"{effort} (default)" if effort == default else effort for effort in efforts
    )
    return f" [{rendered}]"


def render_available_models(settings: Settings) -> str:
    """Render configured defaults and models available through local provider CLIs."""
    lines = ["Configured defaults:"]
    lines.extend(f"  {spec}" for spec in DEFAULT_MODELS)

    lines.extend(["", "Claude models advertised by the local CLI:"])
    try:
        aliases, efforts = discover_claude_models(settings.claude_cli)
    except Exception as exc:
        lines.append(f"  unavailable ({exc})")
    else:
        effort_text = f" [{', '.join(efforts)}]" if efforts else ""
        lines.extend(f"  claude-sdk/{alias}{effort_text}" for alias in aliases)
        lines.append("  Full model IDs are also accepted by Claude Code.")

    lines.extend(["", "Codex models available to this account:"])
    try:
        models = asyncio.run(discover_codex_models(settings.codex_cli))
    except Exception as exc:
        lines.append(f"  unavailable ({exc})")
    else:
        if not models:
            lines.append("  none advertised")
        for model in models:
            model_id = model.get("model") or model.get("id")
            lines.append(f"  codex/{model_id}{_format_efforts(model)}")

    lines.extend(
        [
            "",
            "Other accepted forms:",
            "  claude-sdk/<model-or-alias>/<effort>",
            "  bedrock/<model-id>",
            "  azure/<model-id>",
            "  zen/<model-id>",
            "  google/<model-id>",
            "",
            "Repeat --models to race multiple selections.",
        ]
    )
    return "\n".join(lines)
