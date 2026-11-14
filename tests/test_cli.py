from click.testing import CliRunner

from backend import cli


def test_bare_models_prints_catalog_without_starting_solver(monkeypatch):
    seen = {}

    def fake_catalog(settings):
        seen["claude_cli"] = settings.claude_cli
        seen["codex_cli"] = settings.codex_cli
        return "MODEL CATALOG"

    monkeypatch.setattr(cli, "render_available_models", fake_catalog)

    result = CliRunner().invoke(
        cli.main,
        [
            "--models",
            "--claude-cli",
            "/tmp/custom-claude",
            "--codex-cli",
            "/tmp/custom-codex",
        ],
    )

    assert result.exit_code == 0
    assert result.output == "MODEL CATALOG\n"
    assert seen == {
        "claude_cli": "/tmp/custom-claude",
        "codex_cli": "/tmp/custom-codex",
    }


def test_empty_models_assignment_prints_catalog(monkeypatch):
    monkeypatch.setattr(cli, "render_available_models", lambda settings: "MODEL CATALOG")

    result = CliRunner().invoke(cli.main, ["--models="])

    assert result.exit_code == 0
    assert result.output == "MODEL CATALOG\n"


def test_valued_models_remains_repeatable(monkeypatch):
    captured = {}

    async def fake_run_single(settings, challenge, model_specs, no_submit, max_challenges, msg_port):
        captured["models"] = model_specs

    monkeypatch.setattr(cli, "_run_single", fake_run_single)

    result = CliRunner().invoke(
        cli.main,
        [
            "--challenge",
            "unused",
            "--models",
            "google/gemini-3-flash-preview",
            "--models",
            "azure/gpt-5.4",
        ],
    )

    assert result.exit_code == 0
    assert captured["models"] == [
        "google/gemini-3-flash-preview",
        "azure/gpt-5.4",
    ]
