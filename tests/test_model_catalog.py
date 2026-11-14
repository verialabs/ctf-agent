from subprocess import CompletedProcess

from backend import model_catalog

CLAUDE_HELP = """
  --effort <level>  Effort level for the current session
                    (low, medium, high, xhigh, max)
  --model <model>   Model for the current session. Provide an alias for the
                    latest model (e.g. 'fable', 'opus', or 'sonnet') or a
                    model's full name.
"""


def test_discover_claude_models_reads_aliases_and_efforts(monkeypatch):
    monkeypatch.setattr(model_catalog, "resolve_cli", lambda command, setting: command)
    monkeypatch.setattr(
        model_catalog.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, CLAUDE_HELP, ""),
    )

    aliases, efforts = model_catalog.discover_claude_models("claude-wrapper")

    assert aliases == ["fable", "opus", "sonnet"]
    assert efforts == ["low", "medium", "high", "xhigh", "max"]


def test_discover_claude_models_falls_back_from_wrapper_help(monkeypatch):
    calls = []

    def fake_resolve(command, setting):
        return f"/usr/bin/{command}"

    def fake_run(args, **kwargs):
        calls.append(args)
        stdout = "ourclaude wrapper help" if args[0].endswith("ourclaude") else CLAUDE_HELP
        return CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(model_catalog, "resolve_cli", fake_resolve)
    monkeypatch.setattr(model_catalog.subprocess, "run", fake_run)

    aliases, efforts = model_catalog.discover_claude_models("ourclaude")

    assert aliases == ["fable", "opus", "sonnet"]
    assert efforts == ["low", "medium", "high", "xhigh", "max"]
    assert calls == [["/usr/bin/ourclaude", "--help"], ["/usr/bin/claude", "--help"]]


def test_render_available_models_includes_claude_specs(monkeypatch):
    monkeypatch.setattr(
        model_catalog,
        "discover_claude_models",
        lambda command: (["opus", "sonnet"], ["medium", "high"]),
    )
    monkeypatch.setattr(model_catalog, "discover_codex_models", lambda command: _empty_models())

    output = model_catalog.render_available_models(model_catalog.Settings())

    assert "Claude models advertised by the local CLI:" in output
    assert "claude-sdk/opus [medium, high]" in output
    assert "claude-sdk/sonnet [medium, high]" in output


async def _empty_models():
    return []
